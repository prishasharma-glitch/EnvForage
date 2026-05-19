"""Pytest configuration and shared fixtures."""
import json
import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.sql.expression import BinaryExpression
from sqlalchemy import event
from app.database import Base

# 1. Compile postgresql.ARRAY and JSONB to TEXT/JSON on SQLite
@compiles(ARRAY, "sqlite")
def compile_array_sqlite(element, compiler, **kw):
    return "TEXT"

@compiles(JSONB, "sqlite")
def compile_jsonb_sqlite(element, compiler, **kw):
    return "JSON"

# Compile containment operator (@>) for SQLite
@compiles(BinaryExpression, "sqlite")
def compile_binary_sqlite(element, compiler, **kw):
    operator = element.operator
    op_str = getattr(operator, "opstring", "")
    if op_str == "@>":
        left = compiler.process(element.left, **kw)
        right = compiler.process(element.right, **kw)
        return f"array_contains({left}, {right})"
    return compiler.visit_binary(element, **kw)

# 2. Monkeypatch bind/result processors of postgresql.ARRAY for SQLite
_orig_bind_processor = ARRAY.bind_processor
_orig_result_processor = ARRAY.result_processor

def new_bind_processor(self, dialect):
    if dialect.name == "sqlite":
        def process(value):
            if value is None:
                return None
            return json.dumps(value)
        return process
    return _orig_bind_processor(self, dialect)

def new_result_processor(self, dialect, coltype):
    if dialect.name == "sqlite":
        def process(value):
            if value is None:
                return None
            try:
                return json.loads(value)
            except Exception:
                return value
        return process
    return _orig_result_processor(self, dialect, coltype)

ARRAY.bind_processor = new_bind_processor
ARRAY.result_processor = new_result_processor

# Use in-memory SQLite for unit tests (no Postgres needed)
TEST_DB_URL = "sqlite+aiosqlite:///:memory:"


@pytest.fixture(scope="session")
def event_loop_policy():
    """Use default event loop policy for pytest-asyncio."""
    import asyncio
    return asyncio.DefaultEventLoopPolicy()


@pytest.fixture
async def db_session():
    """Provide a test database session backed by in-memory SQLite."""
    engine = create_async_engine(TEST_DB_URL, echo=False)

    @event.listens_for(engine.sync_engine, "connect")
    def register_sqlite_functions(dbapi_connection, connection_record):
        def array_contains(arr_str, item_str):
            if not arr_str or not item_str:
                return False
            try:
                arr = json.loads(arr_str)
                try:
                    item = json.loads(item_str)
                except Exception:
                    item = item_str
                
                if isinstance(item, list):
                    return all(x in arr for x in item)
                return item in arr
            except Exception:
                return False
                
        dbapi_connection.create_function("array_contains", 2, array_contains)

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_factory = async_sessionmaker(
        bind=engine, class_=AsyncSession, expire_on_commit=False
    )
    async with session_factory() as session:
        yield session

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()
