import os
from datetime import date

import pytest
from sqlalchemy import create_engine, text

from querypilot.config import get_settings

get_settings().reference_date = date(2026, 10, 10)


def _db_up() -> bool:
    try:
        with create_engine(get_settings().db_reader_url).connect() as c:
            c.execute(text("SELECT 1"))
        return True
    except Exception:
        return False


DB = _db_up()
needs_db = pytest.mark.skipif(not DB and not os.getenv("QP_REQUIRE_DB"), reason="PostgreSQL with seeded data not reachable")


@pytest.fixture(scope="session")
def pilot():
    from querypilot.pipeline.service import QueryPilot
    return QueryPilot(persist=False)


@pytest.fixture(scope="session")
def policy():
    from querypilot.safety.policy import get_policy
    return get_policy()
