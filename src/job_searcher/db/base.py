"""Declarative base.

The naming convention is not cosmetic: without it Alembic autogenerate emits
unnamed constraints and indexes, which cannot be dropped on downgrade. Set it
before the first migration or it is a painful retrofit.
"""

from datetime import datetime
from decimal import Decimal

from sqlalchemy import DateTime, MetaData, Numeric, Text
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import DeclarativeBase

NAMING_CONVENTION = {
    "ix": "ix_%(table_name)s_%(column_0_N_name)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)

    type_annotation_map = {
        dict: JSONB,
        list[str]: ARRAY(Text),
        datetime: DateTime(timezone=True),
        Decimal: Numeric(12, 2),
    }
