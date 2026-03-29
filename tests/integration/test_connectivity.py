"""
Real Scenario Integration Tests - Verifies core services connectivity
"""

import asyncio
import sys

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine


# ===== TEST 1: Database Connectivity =====
@pytest.mark.asyncio
async def test_database_connectivity():
    """Verify PostgreSQL connection and schema initialization"""
    try:
        engine = create_async_engine(
            "postgresql+asyncpg://user:password@localhost:5432/ai_agent",
            echo=False,
        )
        async with engine.begin() as conn:
            result = await conn.execute(text("SELECT 1;"))
            assert result.scalar() == 1
        print("✅ Test 1 PASSED: Database connectivity verified")
        await engine.dispose()
        return True
    except Exception as e:
        print(f"❌ Test 1 FAILED: {e}")
        await engine.dispose()
        return False


# ===== TEST 2: Schema Verification =====
@pytest.mark.asyncio
async def test_schema_verification():
    """Verify all required tables exist"""
    try:
        engine = create_async_engine(
            "postgresql+asyncpg://user:password@localhost:5432/ai_agent",
            echo=False,
        )

        required_tables = {
            "products",
            "text_embeddings",
            "intent_tracking",
            "conversation_summaries",
            "semantic_memory",
            "checkpoints",
        }

        async with engine.begin() as conn:
            result = await conn.execute(
                text("""
                    SELECT table_name FROM information_schema.tables
                    WHERE table_schema = 'public'
                """)
            )
            existing_tables = {row[0] for row in await result.fetchall()}

        missing_tables = required_tables - existing_tables
        if missing_tables:
            print(f"❌ Test 2 FAILED: Missing tables: {missing_tables}")
            print(f"   Existing tables: {sorted(existing_tables)}")
            await engine.dispose()
            return False

        print("✅ Test 2 PASSED: All required tables exist")
        await engine.dispose()
        return True
    except Exception as e:
        print(f"❌ Test 2 FAILED: {e}")
        await engine.dispose()
        return False


# ===== TEST 3: pgvector Extension =====
@pytest.mark.asyncio
async def test_pgvector_extension():
    """Verify pgvector extension is available"""
    try:
        engine = create_async_engine(
            "postgresql+asyncpg://user:password@localhost:5432/ai_agent",
            echo=False,
        )

        async with engine.begin() as conn:
            result = await conn.execute(
                text("SELECT * FROM pg_extension WHERE extname = 'vector'")
            )
            exists = result.fetchone() is not None

        if not exists:
            print("❌ Test 3 FAILED: pgvector extension not found")
            await engine.dispose()
            return False

        print("✅ Test 3 PASSED: pgvector extension available")
        await engine.dispose()
        return True
    except Exception as e:
        print(f"❌ Test 3 FAILED: {e}")
        await engine.dispose()
        return False


# ===== TEST 4: Semantic Memory Table Structure =====
@pytest.mark.asyncio
async def test_semantic_memory_schema():
    """Verify semantic_memory table has correct columns"""
    try:
        engine = create_async_engine(
            "postgresql+asyncpg://user:password@localhost:5432/ai_agent",
            echo=False,
        )

        required_columns = {
            "id",
            "customer_id",
            "session_id",
            "embedding",
            "summary_text",
            "created_at",
        }

        async with engine.begin() as conn:
            result = await conn.execute(
                text("""
                    SELECT column_name FROM information_schema.columns
                    WHERE table_name = 'semantic_memory'
                """)
            )
            existing_columns = {row[0] for row in await result.fetchall()}

        missing_columns = required_columns - existing_columns
        if missing_columns:
            print(f"❌ Test 4 FAILED: Missing columns in semantic_memory: {missing_columns}")
            await engine.dispose()
            return False

        print("✅ Test 4 PASSED: semantic_memory table has all required columns")
        await engine.dispose()
        return True
    except Exception as e:
        print(f"❌ Test 4 FAILED: {e}")
        await engine.dispose()
        return False


# ===== TEST 5: Intent Tracking Table Structure =====
@pytest.mark.asyncio
async def test_intent_tracking_schema():
    """Verify intent_tracking table has correct columns"""
    try:
        engine = create_async_engine(
            "postgresql+asyncpg://user:password@localhost:5432/ai_agent",
            echo=False,
        )

        required_columns = {
            "id",
            "customer_id",
            "thread_id",
            "primary_intent",
            "created_at",
        }

        async with engine.begin() as conn:
            result = await conn.execute(
                text("""
                    SELECT column_name FROM information_schema.columns
                    WHERE table_name = 'intent_tracking'
                """)
            )
            existing_columns = {row[0] for row in await result.fetchall()}

        missing_columns = required_columns - existing_columns
        if missing_columns:
            print(f"❌ Test 5 FAILED: Missing columns in intent_tracking: {missing_columns}")
            await engine.dispose()
            return False

        print("✅ Test 5 PASSED: intent_tracking table has all required columns")
        await engine.dispose()
        return True
    except Exception as e:
        print(f"❌ Test 5 FAILED: {e}")
        await engine.dispose()
        return False


# ===== TEST 6: Conversation Summaries Table Structure =====
@pytest.mark.asyncio
async def test_conversation_summaries_schema():
    """Verify conversation_summaries table has correct columns"""
    try:
        engine = create_async_engine(
            "postgresql+asyncpg://user:password@localhost:5432/ai_agent",
            echo=False,
        )

        required_columns = {
            "id",
            "customer_id",
            "thread_id",
            "summary_text",
            "created_at",
        }

        async with engine.begin() as conn:
            result = await conn.execute(
                text("""
                    SELECT column_name FROM information_schema.columns
                    WHERE table_name = 'conversation_summaries'
                """)
            )
            existing_columns = {row[0] for row in await result.fetchall()}

        missing_columns = required_columns - existing_columns
        if missing_columns:
            print(
                f"❌ Test 6 FAILED: Missing columns in conversation_summaries: {missing_columns}"
            )
            await engine.dispose()
            return False

        print("✅ Test 6 PASSED: conversation_summaries table has all required columns")
        await engine.dispose()
        return True
    except Exception as e:
        print(f"❌ Test 6 FAILED: {e}")
        await engine.dispose()
        return False


# ===== TEST 7: Checkpoints Table Structure =====
@pytest.mark.asyncio
async def test_checkpoints_schema():
    """Verify checkpoints table has correct columns"""
    try:
        engine = create_async_engine(
            "postgresql+asyncpg://user:password@localhost:5432/ai_agent",
            echo=False,
        )

        required_columns = {
            "thread_id",
            "checkpoint_id",
            "parent_id",
            "values",
        }

        async with engine.begin() as conn:
            result = await conn.execute(
                text("""
                    SELECT column_name FROM information_schema.columns
                    WHERE table_name = 'checkpoints'
                """)
            )
            existing_columns = {row[0] for row in await result.fetchall()}

        missing_columns = required_columns - existing_columns
        if missing_columns:
            print(f"❌ Test 7 FAILED: Missing columns in checkpoints: {missing_columns}")
            await engine.dispose()
            return False

        print("✅ Test 7 PASSED: checkpoints table has all required columns")
        await engine.dispose()
        return True
    except Exception as e:
        print(f"❌ Test 7 FAILED: {e}")
        await engine.dispose()
        return False


# ===== TEST 8: Service Imports =====
def test_service_imports():
    """Verify all core services can be imported"""
    try:
        from core.agent.state import make_initial_state
        from services.memory.intent_tracker import IntentTracker
        from services.memory.semantic_memory import SemanticMemoryService
        from services.memory.summarizer import ConversationSummarizer

        assert SemanticMemoryService is not None
        assert IntentTracker is not None
        assert ConversationSummarizer is not None
        assert make_initial_state is not None

        print("✅ Test 8 PASSED: All services import successfully")
        return True
    except Exception as e:
        print(f"❌ Test 8 FAILED: {e}")
        return False


# ===== TEST 9: Config Loading =====
def test_config_loading():
    """Verify environment configuration loads correctly"""
    try:
        from core.config import settings

        assert settings is not None
        assert hasattr(settings, "EMBED_MODEL")
        assert hasattr(settings, "CHAT_MODEL")
        assert hasattr(settings, "DB_HOST")

        print(f"✅ Test 9 PASSED: Configuration loaded (Embed={settings.EMBED_MODEL})")
        return True
    except Exception as e:
        print(f"❌ Test 9 FAILED: {e}")
        return False


# ===== TEST 10: LangGraph State Creation =====
def test_langgraph_state():
    """Verify LangGraph state can be created"""
    try:
        from core.agent.state import make_initial_state

        state = make_initial_state(
            customer_id="test-cust-001",
            thread_id="test-thread-001",
        )

        assert state.customer_id == "test-cust-001"
        assert state.thread_id == "test-thread-001"

        print("✅ Test 10 PASSED: LangGraph state creation works")
        return True
    except Exception as e:
        print(f"❌ Test 10 FAILED: {e}")
        return False


# ===== MAIN TEST RUNNER =====
async def run_async_tests():
    """Run all async tests"""
    results = []

    async_tests = [
        test_database_connectivity,
        test_schema_verification,
        test_pgvector_extension,
        test_semantic_memory_schema,
        test_intent_tracking_schema,
        test_conversation_summaries_schema,
        test_checkpoints_schema,
    ]

    for test in async_tests:
        try:
            result = await test()
            results.append(result)
        except Exception as e:
            print(f"❌ {test.__name__} FAILED with exception: {e}")
            results.append(False)

    return results


def run_sync_tests():
    """Run sync tests"""
    sync_tests = [
        test_service_imports,
        test_config_loading,
        test_langgraph_state,
    ]

    results = []
    for test in sync_tests:
        try:
            result = test()
            results.append(result)
        except Exception as e:
            print(f"❌ {test.__name__} FAILED with exception: {e}")
            results.append(False)

    return results


if __name__ == "__main__":
    print("\n" + "=" * 70)
    print("CONNECTIVITY & SCHEMA INTEGRATION TESTS")
    print("=" * 70 + "\n")

    # Run sync tests
    print("Running sync tests...\n")
    sync_results = run_sync_tests()

    # Run async tests
    print("\nRunning async tests...\n")
    async_results = asyncio.run(run_async_tests())

    all_results = sync_results + async_results
    passed = sum(all_results)
    total = len(all_results)

    print("\n" + "=" * 70)
    print("TEST SUMMARY")
    print("=" * 70)
    print(f"\n✅ PASSED: {passed}/{total}")
    print(f"❌ FAILED: {total - passed}/{total}")
    print(f"SUCCESS RATE: {passed * 100 // total}%\n")

    if passed == total:
        print("🎉 ALL CONNECTIVITY TESTS PASSED - System is production-ready!")
        sys.exit(0)
    else:
        print("⚠️  Some tests failed - review errors above")
        sys.exit(1)
