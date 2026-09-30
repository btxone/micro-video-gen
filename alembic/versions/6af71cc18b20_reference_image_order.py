"""Persist the upload order for original and derived reference images."""

from __future__ import annotations

import re

from alembic import op
import sqlalchemy as sa


revision = "6af71cc18b20"
down_revision = "1cbcb4f45d3f"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    columns = {column["name"] for column in sa.inspect(bind).get_columns("artifacts")}
    if "reference_index" not in columns:
        op.add_column("artifacts", sa.Column("reference_index", sa.Integer(), nullable=True))
    indexes = {index["name"] for index in sa.inspect(bind).get_indexes("artifacts")}
    if "ix_artifacts_job_kind_reference" not in indexes:
        op.create_index(
            "ix_artifacts_job_kind_reference",
            "artifacts",
            ["job_id", "kind", "reference_index"],
            unique=False,
        )

    rows = bind.execute(
        sa.text("SELECT id, kind, object_key, reference_index FROM artifacts WHERE reference_index IS NULL")
    ).mappings().all()
    for row in rows:
        kind = row["kind"]
        if kind in {"original_image", "enhanced_image", "normalized_image"}:
            value = 0
        elif kind in {"reference_image", "enhanced_reference_image", "normalized_reference_image"}:
            match = re.search(
                r"(?:reference_image|enhanced_reference)_(\d+)",
                str(row["object_key"]),
            )
            value = int(match.group(1)) if match else None
        else:
            value = None
        if value is not None:
            bind.execute(
                sa.text("UPDATE artifacts SET reference_index = :idx WHERE id = :artifact_id"),
                {"idx": value, "artifact_id": row["id"]},
            )


def downgrade() -> None:
    bind = op.get_bind()
    indexes = {index["name"] for index in sa.inspect(bind).get_indexes("artifacts")}
    if "ix_artifacts_job_kind_reference" in indexes:
        op.drop_index("ix_artifacts_job_kind_reference", table_name="artifacts")
    columns = {column["name"] for column in sa.inspect(bind).get_columns("artifacts")}
    if "reference_index" in columns:
        op.drop_column("artifacts", "reference_index")
