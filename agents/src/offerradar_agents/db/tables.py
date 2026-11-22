from __future__ import annotations

from sqlalchemy import (
    BigInteger,
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Identity,
    Integer,
    MetaData,
    Table,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.types import NullType

METADATA = MetaData()

companies = Table(
    "companies",
    METADATA,
    Column("id", BigInteger, Identity(always=True), primary_key=True),
    Column("name", Text, nullable=False),
    Column("normalized_name", Text, nullable=False, unique=True),
    Column("domain", Text),
    Column("source", Text, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    Column("updated_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
)

ats_boards = Table(
    "ats_boards",
    METADATA,
    Column("id", BigInteger, Identity(always=True), primary_key=True),
    Column(
        "company_id", BigInteger, ForeignKey("companies.id", ondelete="CASCADE"), nullable=False
    ),
    Column("provider", Text, nullable=False),
    Column("board_token", Text, nullable=False),
    Column("last_polled_at", DateTime(timezone=True)),
    Column("etag", Text),
    Column("consecutive_misses", Integer, nullable=False, server_default=text("0")),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    Column("updated_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    UniqueConstraint(
        "company_id", "provider", "board_token", name="uq_ats_boards_company_provider_token"
    ),
)

postings = Table(
    "postings",
    METADATA,
    Column("id", BigInteger, Identity(always=True), primary_key=True),
    Column(
        "company_id", BigInteger, ForeignKey("companies.id", ondelete="RESTRICT"), nullable=False
    ),
    Column("title", Text, nullable=False),
    Column("normalized_title", Text, nullable=False),
    Column("location", Text),
    Column("remote", Boolean, nullable=False, server_default=text("false")),
    Column("term", Text),
    Column("canonical_url", Text, nullable=False),
    Column("dedup_key", Text, nullable=False, unique=True),
    Column("description", Text),
    Column("description_hash", Text),
    # vector(768); M4 replaces NullType with a real pgvector type.
    Column("embedding", NullType),
    Column("embedding_model", Text),
    Column("status", Text, nullable=False, server_default=text("'open'")),
    Column("merged_into_id", BigInteger, ForeignKey("postings.id", ondelete="RESTRICT")),
    Column("first_seen_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    Column("last_seen_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    Column("missed_polls", Integer, nullable=False, server_default=text("0")),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    Column("updated_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
)

posting_sightings = Table(
    "posting_sightings",
    METADATA,
    Column("id", BigInteger, Identity(always=True), primary_key=True),
    Column("posting_id", BigInteger, ForeignKey("postings.id", ondelete="CASCADE"), nullable=False),
    Column("source", Text, nullable=False),
    Column("source_url", Text, nullable=False),
    Column("seen_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    UniqueConstraint(
        "posting_id", "source", "source_url", name="uq_posting_sightings_posting_source_url"
    ),
)

source_cursors = Table(
    "source_cursors",
    METADATA,
    Column("id", BigInteger, Identity(always=True), primary_key=True),
    Column("source", Text, nullable=False),
    Column("cursor_key", Text, nullable=False),
    Column("etag", Text),
    Column("last_polled_at", DateTime(timezone=True)),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    Column("updated_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    UniqueConstraint("source", "cursor_key", name="uq_source_cursors_source_key"),
)
