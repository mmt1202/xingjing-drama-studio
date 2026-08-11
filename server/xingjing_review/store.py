from __future__ import annotations

from contextlib import AbstractContextManager
from copy import deepcopy
from threading import RLock
from typing import Protocol, TypeVar

from .models import ApprovalRecord, AuditEvent, DeliveryRecord, ReviewComment, ReviewLink, ReviewSession

T = TypeVar("T")


class ReviewUnitOfWork(Protocol):
    def get_link(self, link_id: str) -> ReviewLink | None: ...
    def find_link_by_token_digest(self, digest: str) -> ReviewLink | None: ...
    def put_link(self, link: ReviewLink) -> None: ...
    def get_comment(self, comment_id: str) -> ReviewComment | None: ...
    def put_comment(self, comment: ReviewComment) -> None: ...
    def get_delivery(self, link_id: str) -> DeliveryRecord | None: ...
    def put_delivery(self, delivery: DeliveryRecord) -> None: ...
    def put_session(self, session: ReviewSession) -> None: ...
    def append_approval(self, approval: ApprovalRecord) -> None: ...
    def append_audit(self, event: AuditEvent) -> None: ...
    def get_idempotent(self, scope: str, key: str) -> object | None: ...
    def put_idempotent(self, scope: str, key: str, value: object) -> None: ...


class ReviewStore(Protocol):
    def atomic(self) -> AbstractContextManager[ReviewUnitOfWork]: ...


class _MemoryUnitOfWork:
    def __init__(self, state: dict[str, object]) -> None:
        self.state = state

    def get_link(self, link_id: str) -> ReviewLink | None:
        return self.state["links"].get(link_id)  # type: ignore[union-attr, no-any-return]

    def find_link_by_token_digest(self, digest: str) -> ReviewLink | None:
        return next((x for x in self.state["links"].values() if x.token_digest == digest), None)  # type: ignore[union-attr]

    def put_link(self, link: ReviewLink) -> None:
        self.state["links"][link.id] = link  # type: ignore[index]

    def get_comment(self, comment_id: str) -> ReviewComment | None:
        return self.state["comments"].get(comment_id)  # type: ignore[union-attr, no-any-return]

    def put_comment(self, comment: ReviewComment) -> None:
        self.state["comments"][comment.id] = comment  # type: ignore[index]

    def get_delivery(self, link_id: str) -> DeliveryRecord | None:
        return self.state["deliveries"].get(link_id)  # type: ignore[union-attr, no-any-return]

    def put_delivery(self, delivery: DeliveryRecord) -> None:
        self.state["deliveries"][delivery.review_link_id] = delivery  # type: ignore[index]

    def put_session(self, session: ReviewSession) -> None:
        self.state["sessions"][session.id] = session  # type: ignore[index]

    def append_approval(self, approval: ApprovalRecord) -> None:
        self.state["approvals"].append(approval)  # type: ignore[union-attr]

    def append_audit(self, event: AuditEvent) -> None:
        self.state["audits"].append(event)  # type: ignore[union-attr]

    def get_idempotent(self, scope: str, key: str) -> object | None:
        return self.state["idempotency"].get((scope, key))  # type: ignore[union-attr]

    def put_idempotent(self, scope: str, key: str, value: object) -> None:
        self.state["idempotency"][(scope, key)] = value  # type: ignore[index]


class _Atomic(AbstractContextManager[_MemoryUnitOfWork]):
    def __init__(self, store: InMemoryReviewStore) -> None:
        self.store = store

    def __enter__(self) -> _MemoryUnitOfWork:
        self.store._lock.acquire()
        self.snapshot = deepcopy(self.store._state)
        return _MemoryUnitOfWork(self.store._state)

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        if exc_type is not None:
            self.store._state = self.snapshot
        self.store._lock.release()


class InMemoryReviewStore:
    """测试/单进程适配器；生产适配器须以数据库事务实现同一原子端口。"""

    def __init__(self) -> None:
        self._lock = RLock()
        self._state: dict[str, object] = {
            "links": {},
            "comments": {},
            "deliveries": {},
            "sessions": {},
            "approvals": [],
            "audits": [],
            "idempotency": {},
        }

    def atomic(self) -> _Atomic:
        return _Atomic(self)

    def get_link(self, link_id: str) -> ReviewLink | None:
        with self._lock:
            return deepcopy(self._state["links"].get(link_id))  # type: ignore[union-attr, no-any-return]

    def get_comment(self, comment_id: str) -> ReviewComment | None:
        with self._lock:
            return deepcopy(self._state["comments"].get(comment_id))  # type: ignore[union-attr, no-any-return]

    def get_delivery(self, link_id: str) -> DeliveryRecord | None:
        with self._lock:
            return deepcopy(self._state["deliveries"].get(link_id))  # type: ignore[union-attr, no-any-return]

    def audit_events(self) -> tuple[AuditEvent, ...]:
        with self._lock:
            return tuple(deepcopy(self._state["audits"]))  # type: ignore[arg-type]
