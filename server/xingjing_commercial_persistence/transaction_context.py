from __future__ import annotations

from contextvars import ContextVar, Token

from sqlalchemy.orm import Session

_commercial_session: ContextVar[Session | None] = ContextVar("xingjing_commercial_session", default=None)


def bind_commercial_session(session: Session) -> Token[Session | None]:
    return _commercial_session.set(session)


def reset_commercial_session(token: Token[Session | None]) -> None:
    _commercial_session.reset(token)


def current_commercial_session() -> Session:
    session = _commercial_session.get()
    if session is None:
        raise RuntimeError("commercial accounting must run inside the order transaction")
    return session
