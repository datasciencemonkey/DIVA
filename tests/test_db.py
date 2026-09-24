import pytest
from src.services.db import _run_query, QueryError


class _FakeCursor:
    def __init__(self, rows, cols, boom=False):
        self._rows, self._cols, self._boom = rows, cols, boom
    async def __aenter__(self): return self
    async def __aexit__(self, *a): return False
    async def execute(self, sql, params=None):
        if self._boom:
            raise RuntimeError("connection reset")
    @property
    def description(self):
        return [(c,) for c in self._cols]
    async def fetchall(self):
        return self._rows


class _FakeConn:
    def __init__(self, cursor): self._cursor = cursor
    async def __aenter__(self): return self
    async def __aexit__(self, *a): return False
    def cursor(self): return self._cursor


class _FakePool:
    def __init__(self, cursor): self._cursor = cursor
    def connection(self): return _FakeConn(self._cursor)


async def test_run_query_maps_rows_to_dicts():
    pool = _FakePool(_FakeCursor([(1, "VIP")], ["customer_id", "loyalty_tier"]))
    rows = await _run_query(pool, "SELECT 1")
    assert rows == [{"customer_id": 1, "loyalty_tier": "VIP"}]


async def test_run_query_raises_queryerror_after_retry():
    pool = _FakePool(_FakeCursor([], [], boom=True))
    with pytest.raises(QueryError):
        await _run_query(pool, "SELECT 1")
