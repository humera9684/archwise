import os

import pytest


@pytest.fixture
def pg_dsn():
    """A clean PostgreSQL database, or skip.

    Set ARCHWISE_TEST_DATABASE_URL to a disposable database; every test using
    this fixture drops and recreates the public schema.
    """
    dsn = os.getenv("ARCHWISE_TEST_DATABASE_URL")
    if not dsn:
        pytest.skip("ARCHWISE_TEST_DATABASE_URL not set")
    psycopg = pytest.importorskip("psycopg")
    with psycopg.connect(dsn, autocommit=True) as conn:
        conn.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public;")
    from pricing_etl.db import migrate
    migrate(dsn)
    return dsn
