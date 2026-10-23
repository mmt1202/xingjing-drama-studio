"""Reject analytics from stale local mirrors when a source writes another database."""

from __future__ import annotations

import os
import re
from pathlib import Path

from sqlalchemy.engine import URL, make_url
from sqlalchemy.exc import ArgumentError

from .contracts import AnalyticsFailure


def identity(value: URL | str) -> tuple[object, ...]:
    url = make_url(value) if isinstance(value, str) else value
    backend = url.get_backend_name()
    backend = "postgresql" if backend == "postgres" else backend
    database = url.database or ""
    if not database:
        raise ValueError("database name missing")
    if backend == "sqlite":
        if database == ":memory:":
            raise ValueError("in-memory databases cannot establish shared identity")
        database = str(Path(database).resolve()).casefold() if os.name == "nt" else str(Path(database).resolve())
    schema = []
    target_options = []
    connection_only = {
        "sslmode",
        "sslrootcert",
        "sslcert",
        "sslkey",
        "ssl",
        "connect_timeout",
        "timeout",
        "application_name",
        "keepalives",
        "keepalives_idle",
        "keepalives_interval",
        "keepalives_count",
        "tcp_user_timeout",
        "channel_binding",
        "gssencmode",
        "client_encoding",
    }
    for key, raw in url.query.items():
        if key.casefold() in {"service", "servicefile"}:
            raise ValueError("service indirection cannot establish shared identity")
        if key.casefold() in {"schema", "currentschema", "search_path"}:
            values = raw if isinstance(raw, tuple) else (raw,)
            schema.append((key.casefold(), tuple(value.strip() for value in values)))
        elif key.casefold() == "options":
            values = raw if isinstance(raw, tuple) else (raw,)
            for option in values:
                matches = re.findall(r"search_path\s*=\s*([^\s]+)", option)
                if "search_path" in option and not matches:
                    raise ValueError("schema options ambiguous")
                schema.extend(("search_path", (match,)) for match in matches)
                remainder = re.sub(r"(?:-c\s*)?search_path\s*=\s*[^\s]+", "", option).strip()
                if remainder:
                    target_options.append(("options", (" ".join(remainder.split()),)))
        elif key.casefold() not in connection_only:
            values = raw if isinstance(raw, tuple) else (raw,)
            target_options.append((key.casefold(), tuple(value.strip() for value in values)))
    default_port = {"postgresql": 5432, "mysql": 3306}.get(backend)
    return (
        backend,
        (url.host or "").casefold(),
        url.port or default_port,
        database,
        tuple(sorted(schema)),
        tuple(sorted(target_options)),
    )


def require_same_database(bound: URL, variables: set[str]) -> None:
    for variable in sorted(variables):
        configured = os.environ.get(variable, "").strip()
        if not configured:
            continue
        try:
            if identity(bound) != identity(configured):
                raise ValueError("source database differs")
        except (ValueError, TypeError, ArgumentError) as error:
            raise AnalyticsFailure("ANALYTICS_SOURCE_DATABASE_MISMATCH", 503) from error
