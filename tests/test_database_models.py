"""Database schema contracts independent of a running PostgreSQL server."""

from sqlalchemy.dialects import postgresql
from sqlalchemy.schema import CreateTable

from malg.database import Base


def test_campaign_and_icp_schema_use_postgresql_jsonb_and_campaign_constraints() -> None:
    """Compile the persistence schema with canonical payload and identity constraints."""
    tables = {table.name: table for table in Base.metadata.sorted_tables}

    assert set(tables) == {"campaigns", "icps"}
    assert "JSONB" in str(CreateTable(tables["campaigns"]).compile(dialect=postgresql.dialect()))
    icp_ddl = str(CreateTable(tables["icps"]).compile(dialect=postgresql.dialect()))
    assert (
        "FOREIGN KEY(campaign_id) REFERENCES campaigns (campaign_id) ON DELETE CASCADE" in icp_ddl
    )
    assert "CONSTRAINT uq_icps_campaign_id_icp_id UNIQUE (campaign_id, icp_id)" in icp_ddl
    assert "CONSTRAINT uq_icps_campaign_id_segment_key UNIQUE (campaign_id, segment_key)" in icp_ddl
