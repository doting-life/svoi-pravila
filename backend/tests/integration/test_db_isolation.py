"""Manual-testing database must stay untouched by the test suite."""

from __future__ import annotations

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import create_async_engine

from svoi_pravila.adapters.persistence.engine import create_engine, dispose_engine
from svoi_pravila.adapters.persistence.models import Base
from svoi_pravila.bootstrap import load_test_infra_settings
from svoi_pravila.config import Settings
from tests.support.postgres import database_name_from_url, truncate_all_tables


async def _domain_table_counts(database_url: str) -> dict[str, int]:
    """Read-only row counts for every mapped domain table."""
    engine = create_async_engine(database_url)
    try:
        counts: dict[str, int] = {}
        async with engine.connect() as conn:
            for table in Base.metadata.sorted_tables:
                result = await conn.execute(select(func.count()).select_from(table))
                counts[table.name] = int(result.scalar_one())
        return counts
    finally:
        await engine.dispose()


@pytest.mark.integration
async def test_manual_database_untouched_by_db_tests(migrated_schema: Settings) -> None:
    manual_url = load_test_infra_settings().database_url.get_secret_value()
    test_url = migrated_schema.database_url.get_secret_value()
    manual_name = database_name_from_url(manual_url)
    test_name = database_name_from_url(test_url)

    assert test_name.endswith("_test")
    assert test_name != manual_name

    before = await _domain_table_counts(manual_url)

    test_engine = create_engine(migrated_schema)
    try:
        await truncate_all_tables(test_engine)
    finally:
        await dispose_engine(test_engine)

    after = await _domain_table_counts(manual_url)
    assert after == before
