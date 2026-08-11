from datetime import UTC, datetime

from server.xingjing_open_platform.versions import ApiVersionPolicy, VersionStatus

NOW = datetime(2026, 7, 15, tzinfo=UTC)


def test_version_policy_distinguishes_supported_deprecated_and_rejected_versions() -> None:
    policy = ApiVersionPolicy(
        current=2,
        minimum_supported=1,
        deprecated={1: datetime(2026, 12, 31, tzinfo=UTC)},
    )

    assert policy.evaluate("v2", now=NOW).status is VersionStatus.SUPPORTED
    old = policy.evaluate("v1", now=NOW)
    assert (old.status, old.sunset_at) == (
        VersionStatus.DEPRECATED,
        datetime(2026, 12, 31, tzinfo=UTC),
    )
    assert policy.evaluate("v0", now=NOW).status is VersionStatus.REJECTED
    assert policy.evaluate("v3", now=NOW).status is VersionStatus.REJECTED
    assert policy.evaluate("latest", now=NOW).status is VersionStatus.REJECTED
