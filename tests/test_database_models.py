"""Database schema contracts independent of a running PostgreSQL server."""

from sqlalchemy.dialects import postgresql
from sqlalchemy.schema import CreateTable

from malg.database import Base


def test_persistence_schema_uses_postgresql_jsonb_and_account_relationships() -> None:
    """Compile the persistence schema with canonical payload and identity constraints."""
    tables = {table.name: table for table in Base.metadata.sorted_tables}

    assert set(tables) == {
        "account_matches",
        "account_validation_runs",
        "accounts",
        "campaigns",
        "communication_endpoints",
        "contacts",
        "employments",
        "icps",
    }
    assert "JSONB" in str(CreateTable(tables["campaigns"]).compile(dialect=postgresql.dialect()))
    icp_ddl = str(CreateTable(tables["icps"]).compile(dialect=postgresql.dialect()))
    assert (
        "FOREIGN KEY(campaign_id) REFERENCES campaigns (campaign_id) ON DELETE CASCADE" in icp_ddl
    )
    assert "CONSTRAINT uq_icps_campaign_id_icp_id UNIQUE (campaign_id, icp_id)" in icp_ddl
    assert "CONSTRAINT uq_icps_campaign_id_segment_key UNIQUE (campaign_id, segment_key)" in icp_ddl
    account_match_ddl = str(
        CreateTable(tables["account_matches"]).compile(dialect=postgresql.dialect())
    )
    assert "CONSTRAINT uq_account_matches_campaign_icp_account UNIQUE" in account_match_ddl
    endpoint_ddl = str(
        CreateTable(tables["communication_endpoints"]).compile(dialect=postgresql.dialect())
    )
    assert "CONSTRAINT ck_communication_endpoints_one_owner CHECK" in endpoint_ddl
