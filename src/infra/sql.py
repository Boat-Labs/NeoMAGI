"""Small SQLAlchemy helpers for raw SQL text queries."""

from __future__ import annotations

from sqlalchemy import bindparam, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.sql.elements import TextClause


def jsonb_text(statement: str, *json_param_names: str) -> TextClause:
    """Return a text() clause with the given params typed as JSONB."""
    clause = text(statement)
    if not json_param_names:
        return clause
    return clause.bindparams(*(bindparam(name, type_=JSONB()) for name in json_param_names))
