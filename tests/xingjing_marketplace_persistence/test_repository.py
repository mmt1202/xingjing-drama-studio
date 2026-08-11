from __future__ import annotations

import asyncio
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, replace
from datetime import UTC, datetime

import pytest
from sqlalchemy import inspect, text
from sqlalchemy.dialects import postgresql
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool
from sqlalchemy.schema import CreateTable

from server.xingjing_generation_persistence.repository import GenerationBillingAccountRow
from server.xingjing_marketplace import (
    ADMIN_BUSINESS_MANAGE,
    ADMIN_BUSINESS_VIEW,
    COMMUNITY_MANAGE,
    COMMUNITY_VIEW,
    TEMPLATE_MANAGE,
    TEMPLATE_VIEW,
    AuditEvent,
    CreateFork,
    CreateTemplate,
    CreateTemplateVersion,
    DecideTemplateReview,
    ForkNotFound,
    IdempotencyConflict,
    MarketplaceService,
    MarketQuery,
    PublishCommunityProject,
    RequestContext,
    RevenueShareInput,
    ReviewDecision,
    RightsPolicyInput,
    RightsScope,
    SubmitTemplateReview,
    TemplateKind,
    TemplateNotFound,
    VersionConflict,
    WithdrawTemplate,
)
from server.xingjing_marketplace_persistence import Base, SqlAlchemyMarketplaceRepository
from server.xingjing_platform_persistence.persistence import ProjectRow

NOW = datetime(2026, 7, 16, 8, 0, tzinfo=UTC)


@pytest.fixture
def database(tmp_path) -> Iterator[DatabaseHarness]:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'marketplace.sqlite3'}", poolclass=NullPool)

    async def create_schema() -> None:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
            await connection.run_sync(GenerationBillingAccountRow.metadata.create_all)
            await connection.run_sync(ProjectRow.metadata.create_all)
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        async with sessions.begin() as session:
            session.add_all([
                GenerationBillingAccountRow(workspace_id="workspace-b", currency="CNY", available_minor=100_000,
                    held_minor=0, spent_minor=0, version=1, updated_at=NOW),
                GenerationBillingAccountRow(workspace_id="workspace-c", currency="CNY", available_minor=100_000,
                    held_minor=0, spent_minor=0, version=1, updated_at=NOW),
            ])

    asyncio.run(create_schema())
    repository = SqlAlchemyMarketplaceRepository(async_sessionmaker(engine, expire_on_commit=False))
    yield DatabaseHarness(repository, engine)
    repository.close()
    asyncio.run(engine.dispose())


@pytest.fixture
def repository(database: DatabaseHarness) -> SqlAlchemyMarketplaceRepository:
    return database.repository


@dataclass(frozen=True, slots=True)
class DatabaseHarness:
    repository: SqlAlchemyMarketplaceRepository
    engine: AsyncEngine


def context(actor_id: str, workspace_id: str, *permissions: str) -> RequestContext:
    return RequestContext(actor_id, workspace_id, frozenset(permissions), f"request-{actor_id}", "tenant-test")


def publish_template(
    service: MarketplaceService,
    *,
    workspace_id: str,
    title: str,
    kind: TemplateKind,
    tags: tuple[str, ...],
    rights: RightsPolicyInput,
    price_minor: int,
    key: str,
) -> str:
    creator = context(f"creator-{key}", workspace_id, TEMPLATE_MANAGE, TEMPLATE_VIEW)
    created = service.create_template(
        creator,
        CreateTemplate(
            title=title,
            kind=kind,
            content={"title": title},
            tags=tags,
            rights=rights,
            revenue_shares=(
                RevenueShareInput("template_author", 8_000),
                RevenueShareInput("platform", 2_000),
            ),
            idempotency_key=f"create-{key}",
            price_minor=price_minor,
        ),
    )
    submitted = service.submit_template_review(
        creator,
        SubmitTemplateReview(created.resource_id, 1, "权利已核验", f"submit-{key}"),
    )
    service.decide_template_review(
        context(f"admin-{key}", workspace_id, ADMIN_BUSINESS_MANAGE),
        DecideTemplateReview(
            submitted.resource_id,
            submitted.resource_version,
            ReviewDecision.APPROVE,
            "审核通过",
            f"approve-{key}",
        ),
    )
    return created.resource_id


def test_template_creation_is_durable_idempotent_audited_and_tenant_isolated(
    repository: SqlAlchemyMarketplaceRepository,
) -> None:
    service = MarketplaceService(repository, clock=lambda: NOW)
    creator = context("creator-a", "workspace-a", TEMPLATE_MANAGE, TEMPLATE_VIEW)
    command = CreateTemplate(
        title="可复用项目模板",
        kind=TemplateKind.PROJECT,
        content={"project": {"mode": "drama"}},
        tags=("项目", "剧情"),
        rights=RightsPolicyInput.public(
            commercial_use=True,
            attribution_required=True,
            inheritable_scopes=("storyboards",),
        ),
        revenue_shares=(
            RevenueShareInput("template_author", 8_000),
            RevenueShareInput("platform", 2_000),
        ),
        idempotency_key="create-template-1",
        price_minor=1_200,
    )

    created = service.create_template(creator, command)
    replay = service.create_template(creator, command)
    stored = service.get_template(creator, created.resource_id)
    events = service.list_audit_events(
        context("admin-a", "workspace-a", ADMIN_BUSINESS_VIEW),
        subject_id=created.resource_id,
    )

    assert replay == created
    assert stored.versions[0].content == {"project": {"mode": "drama"}}
    assert stored.versions[0].price_minor == 1_200
    assert [event.action for event in events] == ["template.created"]
    with pytest.raises(TemplateNotFound):
        service.get_template(
            context("reader-b", "workspace-b", TEMPLATE_VIEW),
            created.resource_id,
        )

    with pytest.raises(IdempotencyConflict):
        service.create_template(creator, replace(command, title="复用键但请求不同"))


def test_concurrent_idempotent_creation_commits_one_template_and_one_audit(
    repository: SqlAlchemyMarketplaceRepository,
) -> None:
    service = MarketplaceService(repository, clock=lambda: NOW)
    creator = context("creator-a", "workspace-a", TEMPLATE_MANAGE, TEMPLATE_VIEW)
    command = CreateTemplate(
        title="并发幂等模板",
        kind=TemplateKind.PROJECT,
        content={"idempotent": True},
        tags=("并发",),
        rights=RightsPolicyInput.public(
            commercial_use=True,
            attribution_required=False,
            inheritable_scopes=("storyboards",),
        ),
        revenue_shares=(
            RevenueShareInput("template_author", 8_000),
            RevenueShareInput("platform", 2_000),
        ),
        idempotency_key="concurrent-create",
    )

    with ThreadPoolExecutor(max_workers=2) as executor:
        receipts = tuple(executor.map(lambda _index: service.create_template(creator, command), range(2)))

    assert receipts[0] == receipts[1]
    stored = service.get_template(creator, receipts[0].resource_id)
    events = service.list_audit_events(
        context("auditor-a", "workspace-a", ADMIN_BUSINESS_VIEW),
        subject_id=stored.id,
    )
    assert [event.action for event in events] == ["template.created"]


def test_template_versions_reviews_publication_and_withdrawal_history_are_durable(
    repository: SqlAlchemyMarketplaceRepository,
) -> None:
    service = MarketplaceService(repository, clock=lambda: NOW)
    creator = context("creator-a", "workspace-a", TEMPLATE_MANAGE, TEMPLATE_VIEW)
    created = service.create_template(
        creator,
        CreateTemplate(
            title="镜头模板",
            kind=TemplateKind.STORYBOARD,
            content={"camera": "wide"},
            tags=("分镜", "悬疑"),
            rights=RightsPolicyInput.public(
                commercial_use=True,
                attribution_required=True,
                inheritable_scopes=("storyboards",),
            ),
            revenue_shares=(
                RevenueShareInput("template_author", 8_000),
                RevenueShareInput("platform", 2_000),
            ),
            idempotency_key="template-lifecycle-create",
            price_minor=800,
        ),
    )
    versioned = service.create_template_version(
        creator,
        CreateTemplateVersion(
            template_id=created.resource_id,
            expected_revision=1,
            content={"camera": "close-up"},
            rights=RightsPolicyInput.public(
                commercial_use=True,
                attribution_required=True,
                inheritable_scopes=("storyboards",),
            ),
            revenue_shares=(
                RevenueShareInput("template_author", 7_500),
                RevenueShareInput("platform", 2_500),
            ),
            idempotency_key="template-lifecycle-version",
            price_minor=1_000,
        ),
    )
    submitted = service.submit_template_review(
        creator,
        SubmitTemplateReview(
            template_id=created.resource_id,
            expected_revision=versioned.resource_version,
            statement="版权与素材来源已核验",
            idempotency_key="template-lifecycle-submit",
        ),
    )
    service.decide_template_review(
        context("admin-a", "workspace-a", ADMIN_BUSINESS_MANAGE),
        DecideTemplateReview(
            review_id=submitted.resource_id,
            expected_revision=submitted.resource_version,
            decision=ReviewDecision.APPROVE,
            reason="审核通过",
            idempotency_key="template-lifecycle-approve",
        ),
    )

    consumer = context("consumer-b", "workspace-b", COMMUNITY_VIEW)
    published = service.list_market(consumer, MarketQuery(search="镜头", page_size=10))
    assert [item.source_id for item in published.items] == [created.resource_id]
    assert published.items[0].source_snapshot_json == '{"camera":"close-up"}'

    service.withdraw_template(
        context("admin-a", "workspace-a", ADMIN_BUSINESS_MANAGE),
        WithdrawTemplate(
            template_id=created.resource_id,
            expected_revision=4,
            reason="许可策略调整",
            idempotency_key="template-lifecycle-withdraw",
        ),
    )

    stored = service.get_template(creator, created.resource_id)
    events = service.list_audit_events(
        context("auditor-a", "workspace-a", ADMIN_BUSINESS_VIEW),
        subject_id=created.resource_id,
    )
    assert [version.content for version in stored.versions] == [{"camera": "wide"}, {"camera": "close-up"}]
    assert [review.status.value for review in stored.reviews] == ["approved"]
    assert stored.publication_state.value == "withdrawn"
    assert service.list_market(consumer, MarketQuery(page_size=10)).items == ()
    assert [event.action for event in events] == [
        "template.created",
        "template.version_created",
        "template.withdrawn",
    ]


def test_market_search_applies_rights_filters_and_stable_cursor_in_sql(
    repository: SqlAlchemyMarketplaceRepository,
) -> None:
    service = MarketplaceService(repository, clock=lambda: NOW)
    public = RightsPolicyInput.public(
        commercial_use=True,
        attribution_required=True,
        inheritable_scopes=("storyboards",),
    )
    publish_template(
        service,
        workspace_id="workspace-a",
        title="都市悬疑项目",
        kind=TemplateKind.PROJECT,
        tags=("悬疑", "都市"),
        rights=public,
        price_minor=1_200,
        key="public-project",
    )
    publish_template(
        service,
        workspace_id="workspace-a",
        title="悬疑分镜",
        kind=TemplateKind.STORYBOARD,
        tags=("悬疑",),
        rights=public,
        price_minor=800,
        key="public-storyboard",
    )
    publish_template(
        service,
        workspace_id="workspace-a",
        title="内部悬疑项目",
        kind=TemplateKind.PROJECT,
        tags=("悬疑",),
        rights=RightsPolicyInput(
            scope=RightsScope.WORKSPACE,
            commercial_use=True,
            attribution_required=False,
            inheritable_scopes=("storyboards",),
        ),
        price_minor=100,
        key="workspace-project",
    )
    publish_template(
        service,
        workspace_id="workspace-a",
        title="白名单悬疑项目",
        kind=TemplateKind.PROJECT,
        tags=("悬疑", "白名单"),
        rights=RightsPolicyInput(
            scope=RightsScope.ALLOWLIST,
            commercial_use=False,
            attribution_required=True,
            inheritable_scopes=("storyboards",),
            allowed_workspace_ids=("workspace-b",),
        ),
        price_minor=1_500,
        key="allowlist-project",
    )
    workspace_b = context("reader-b", "workspace-b", COMMUNITY_VIEW)

    filtered = service.list_market(
        workspace_b,
        MarketQuery(
            search="都市",
            kinds=frozenset({TemplateKind.PROJECT}),
            tags=frozenset({"悬疑"}),
            commercial_use=True,
            maximum_price_minor=1_300,
            page_size=10,
        ),
    )
    first = service.list_market(workspace_b, MarketQuery(tags=frozenset({"悬疑"}), page_size=1))
    second = service.list_market(
        workspace_b,
        MarketQuery(tags=frozenset({"悬疑"}), page_size=1, cursor=first.next_cursor),
    )
    workspace_c = service.list_market(
        context("reader-c", "workspace-c", COMMUNITY_VIEW),
        MarketQuery(tags=frozenset({"悬疑"}), page_size=10),
    )

    assert [item.title for item in filtered.items] == ["都市悬疑项目"]
    assert first.total == second.total == 3
    assert first.next_cursor is not None
    assert first.items[0].id != second.items[0].id
    assert {item.title for item in workspace_c.items} == {"都市悬疑项目", "悬疑分镜"}


def test_fork_snapshots_rights_revenue_projects_and_multi_generation_lineage_are_durable(
    repository: SqlAlchemyMarketplaceRepository,
) -> None:
    service = MarketplaceService(repository, clock=lambda: NOW)
    creator = context("creator-a", "workspace-a", TEMPLATE_MANAGE, TEMPLATE_VIEW)
    created = service.create_template(
        creator,
        CreateTemplate(
            title="公开项目模板",
            kind=TemplateKind.PROJECT,
            content={"project": {"mode": "drama"}, "assets": ["character-a"]},
            tags=("公开",),
            rights=RightsPolicyInput.public(
                commercial_use=True,
                attribution_required=True,
                inheritable_scopes=("storyboards", "characters"),
            ),
            revenue_shares=(
                RevenueShareInput("template_author", 7_000),
                RevenueShareInput("fork_creator", 2_000),
                RevenueShareInput("platform", 1_000),
            ),
            idempotency_key="fork-source-create",
            price_minor=2_000,
        ),
    )
    submitted = service.submit_template_review(
        creator,
        SubmitTemplateReview(created.resource_id, 1, "权利完整", "fork-source-submit"),
    )
    service.decide_template_review(
        context("admin-a", "workspace-a", ADMIN_BUSINESS_MANAGE),
        DecideTemplateReview(
            submitted.resource_id,
            submitted.resource_version,
            ReviewDecision.APPROVE,
            "审核通过",
            "fork-source-approve",
        ),
    )
    workspace_b = context("creator-b", "workspace-b", COMMUNITY_VIEW, COMMUNITY_MANAGE)
    source_item = service.list_market(workspace_b, MarketQuery(page_size=10)).items[0]
    first_receipt = service.create_fork(
        workspace_b,
        CreateFork(
            market_item_id=source_item.id,
            expected_market_revision=source_item.revision,
            expected_source_version_id=source_item.source_version_id,
            project_name="第一代二创",
            intended_commercial_use=True,
            requested_inheritable_scopes=("storyboards",),
            idempotency_key="fork-generation-1",
        ),
    )
    first = service.get_fork(workspace_b, first_receipt.resource_id)
    first_project = service.get_fork_project(workspace_b, first.target_project_id)

    assert first.source_snapshot.content == {
        "assets": ["character-a"],
        "project": {"mode": "drama"},
    }
    assert first.rights_record.granted_scopes == ("storyboards",)
    assert first.revenue_rule.id == source_item.revenue_rule.id
    assert first_project.content_digest == first.source_snapshot.content_digest
    with pytest.raises(ForkNotFound):
        service.get_fork(
            context("reader-c", "workspace-c", COMMUNITY_VIEW),
            first.id,
        )

    service.publish_community_project(
        workspace_b,
        PublishCommunityProject(
            fork_id=first.id,
            expected_project_revision=first_project.revision,
            title="第一代公开二创",
            tags=("社区", "二创"),
            rights=RightsPolicyInput.public(
                commercial_use=True,
                attribution_required=True,
                inheritable_scopes=("storyboards",),
            ),
            revenue_shares=(
                RevenueShareInput("source_project", 4_000),
                RevenueShareInput("fork_creator", 5_000),
                RevenueShareInput("platform", 1_000),
            ),
            price_minor=1_000,
            idempotency_key="publish-generation-1",
        ),
    )
    workspace_c = context("creator-c", "workspace-c", COMMUNITY_VIEW, COMMUNITY_MANAGE)
    community_item = service.list_market(
        workspace_c,
        MarketQuery(search="第一代公开", page_size=10),
    ).items[0]
    second_receipt = service.create_fork(
        workspace_c,
        CreateFork(
            market_item_id=community_item.id,
            expected_market_revision=community_item.revision,
            expected_source_version_id=community_item.source_version_id,
            project_name="第二代二创",
            intended_commercial_use=True,
            requested_inheritable_scopes=("storyboards",),
            idempotency_key="fork-generation-2",
        ),
    )
    second = service.get_fork(workspace_c, second_receipt.resource_id)

    assert second.parent_fork_id == first.id
    assert second.ancestor_fork_ids == (first.id,)
    assert [node.fork_id for node in second.lineage] == [first.id]
    assert service.list_forks(workspace_b) == (first,)


def test_database_conditional_update_unique_constraint_and_atomic_rollback_are_real(
    repository: SqlAlchemyMarketplaceRepository,
) -> None:
    service = MarketplaceService(repository, clock=lambda: NOW)
    creator = context("creator-a", "workspace-a", TEMPLATE_MANAGE, TEMPLATE_VIEW)
    receipt = service.create_template(
        creator,
        CreateTemplate(
            title="并发模板",
            kind=TemplateKind.PROJECT,
            content={"revision": 1},
            tags=("并发",),
            rights=RightsPolicyInput.public(
                commercial_use=True,
                attribution_required=False,
                inheritable_scopes=("storyboards",),
            ),
            revenue_shares=(
                RevenueShareInput("template_author", 8_000),
                RevenueShareInput("platform", 2_000),
            ),
            idempotency_key="database-guard-create",
        ),
    )
    original = service.get_template(creator, receipt.resource_id)
    original_version = original.versions[0]
    duplicate_number = replace(
        original_version,
        id="duplicate-version-id",
        rights=replace(original_version.rights, id="duplicate-rights-id"),
        revenue_rule=replace(original_version.revenue_rule, id="duplicate-revenue-id"),
    )
    with pytest.raises(VersionConflict):
        repository.atomic(
            lambda transaction: transaction.save_template(
                replace(
                    original,
                    revision=2,
                    latest_version_id=duplicate_number.id,
                    versions=(original_version, duplicate_number),
                ),
                expected_revision=1,
            )
        )

    winner = replace(original, title="条件更新胜者", revision=2)
    stale = replace(original, title="陈旧写入", revision=2)

    repository.atomic(lambda transaction: transaction.save_template(winner, expected_revision=1))
    with pytest.raises(VersionConflict):
        repository.atomic(lambda transaction: transaction.save_template(stale, expected_revision=1))
    with pytest.raises(VersionConflict):
        repository.atomic(lambda transaction: transaction.save_template(original, expected_revision=None))

    class InjectedFailure(RuntimeError):
        pass

    def fail_after_writes(transaction) -> None:
        transaction.save_template(replace(winner, title="必须回滚", revision=3), expected_revision=2)
        transaction.append_audit(
            AuditEvent(
                id="rollback-audit",
                schema_version=1,
                workspace_id="workspace-a",
                actor_id="tester",
                request_id="rollback-request",
                subject_type="template",
                subject_id=winner.id,
                action="rollback.must_not_persist",
                before_json="{}",
                after_json="{}",
                result="failed",
                occurred_at=NOW,
            )
        )
        raise InjectedFailure

    with pytest.raises(InjectedFailure):
        repository.atomic(fail_after_writes)

    stored = service.get_template(creator, receipt.resource_id)
    events = service.list_audit_events(
        context("auditor-a", "workspace-a", ADMIN_BUSINESS_VIEW),
        subject_id=receipt.resource_id,
    )
    assert (stored.title, stored.revision) == ("条件更新胜者", 2)
    assert "rollback.must_not_persist" not in [event.action for event in events]


def test_market_item_update_rejects_a_stale_database_revision(
    repository: SqlAlchemyMarketplaceRepository,
) -> None:
    service = MarketplaceService(repository, clock=lambda: NOW)
    publish_template(
        service,
        workspace_id="workspace-a",
        title="市场并发源",
        kind=TemplateKind.PROJECT,
        tags=("市场并发",),
        rights=RightsPolicyInput.public(
            commercial_use=True,
            attribution_required=False,
            inheritable_scopes=("storyboards",),
        ),
        price_minor=500,
        key="market-concurrency",
    )
    reader = context("reader-b", "workspace-b", COMMUNITY_VIEW)
    original = service.list_market(reader, MarketQuery(page_size=10)).items[0]
    winner = replace(original, title="市场条件更新胜者", revision=original.revision + 1)
    stale = replace(original, title="市场陈旧写入", revision=original.revision + 1)

    repository.atomic(lambda transaction: transaction.save_market_item(winner, expected_revision=original.revision))
    with pytest.raises(VersionConflict):
        repository.atomic(lambda transaction: transaction.save_market_item(stale, expected_revision=original.revision))

    assert [item.title for item in service.list_market(reader, MarketQuery(page_size=10)).items] == ["市场条件更新胜者"]


def test_sqlite_schema_and_history_tables_preserve_review_and_withdrawal_revisions(
    database: DatabaseHarness,
) -> None:
    service = MarketplaceService(database.repository, clock=lambda: NOW)
    creator = context("creator-a", "workspace-a", TEMPLATE_MANAGE, TEMPLATE_VIEW)
    created = service.create_template(
        creator,
        CreateTemplate(
            title="历史模板",
            kind=TemplateKind.PROJECT,
            content={"history": True},
            tags=("历史",),
            rights=RightsPolicyInput.public(
                commercial_use=True,
                attribution_required=True,
                inheritable_scopes=("storyboards",),
            ),
            revenue_shares=(
                RevenueShareInput("template_author", 8_000),
                RevenueShareInput("platform", 2_000),
            ),
            idempotency_key="history-create",
        ),
    )
    submitted = service.submit_template_review(
        creator,
        SubmitTemplateReview(created.resource_id, 1, "历史审核", "history-submit"),
    )
    service.decide_template_review(
        context("admin-a", "workspace-a", ADMIN_BUSINESS_MANAGE),
        DecideTemplateReview(
            submitted.resource_id,
            submitted.resource_version,
            ReviewDecision.APPROVE,
            "历史通过",
            "history-approve",
        ),
    )
    service.withdraw_template(
        context("admin-a", "workspace-a", ADMIN_BUSINESS_MANAGE),
        WithdrawTemplate(created.resource_id, 3, "历史下架", "history-withdraw"),
    )

    async def inspect_database() -> tuple[set[str], tuple[int, int, int, int]]:
        async with database.engine.connect() as connection:
            tables = set(await connection.run_sync(lambda sync_connection: inspect(sync_connection).get_table_names()))
            counts = []
            for table_name in (
                "xingjing_marketplace_template_state_history",
                "xingjing_marketplace_template_reviews",
                "xingjing_marketplace_item_revision_history",
                "xingjing_marketplace_audit_events",
            ):
                counts.append(int((await connection.execute(text(f"SELECT COUNT(*) FROM {table_name}"))).scalar_one()))
            return tables, (counts[0], counts[1], counts[2], counts[3])

    tables, counts = asyncio.run(inspect_database())
    assert {
        "xingjing_marketplace_templates",
        "xingjing_marketplace_template_versions",
        "xingjing_marketplace_template_reviews",
        "xingjing_marketplace_template_state_history",
        "xingjing_marketplace_items",
        "xingjing_marketplace_item_revision_history",
        "xingjing_marketplace_idempotency",
        "xingjing_marketplace_audit_events",
        "xingjing_marketplace_forks",
        "xingjing_marketplace_fork_projects",
        "xingjing_marketplace_fork_source_snapshots",
        "xingjing_marketplace_fork_rights_records",
        "xingjing_marketplace_fork_lineage",
    } <= tables
    assert counts == (4, 1, 2, 4)


def test_all_marketplace_tables_compile_for_postgresql() -> None:
    ddl = "\n".join(
        str(CreateTable(table).compile(dialect=postgresql.dialect())) for table in Base.metadata.sorted_tables
    )

    assert "CREATE TABLE xingjing_marketplace_templates" in ddl
    assert "CREATE TABLE xingjing_marketplace_forks" in ddl
    assert "uq_xj_marketplace_idempotency_scope" in ddl
    assert "uq_xj_marketplace_template_version_number" in ddl
    assert "uq_xj_marketplace_item_revision" in ddl
    assert "AUTOINCREMENT" not in ddl


def test_from_url_owns_a_dedicated_async_engine_without_runtime_schema_creation(tmp_path) -> None:
    database_url = f"sqlite+aiosqlite:///{tmp_path / 'from-url.sqlite3'}"
    migration_engine = create_async_engine(database_url, poolclass=NullPool)

    async def migrate_schema() -> None:
        async with migration_engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        await migration_engine.dispose()

    asyncio.run(migrate_schema())
    repository = SqlAlchemyMarketplaceRepository.from_url(database_url)
    try:
        service = MarketplaceService(repository, clock=lambda: NOW)
        created = service.create_template(
            context("creator-a", "workspace-a", TEMPLATE_MANAGE, TEMPLATE_VIEW),
            CreateTemplate(
                title="独立引擎模板",
                kind=TemplateKind.PROJECT,
                content={"engine": "owned"},
                tags=("引擎",),
                rights=RightsPolicyInput.public(
                    commercial_use=True,
                    attribution_required=False,
                    inheritable_scopes=("storyboards",),
                ),
                revenue_shares=(
                    RevenueShareInput("template_author", 8_000),
                    RevenueShareInput("platform", 2_000),
                ),
                idempotency_key="from-url-create",
            ),
        )
        assert created.resource_version == 1
    finally:
        repository.close()
