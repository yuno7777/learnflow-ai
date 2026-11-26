"""Copy the disposable Chroma fixture to pgvector and compare the retrieved citation."""
import asyncio
import json
import os
from uuid import UUID

import asyncpg
import chromadb

from learnflow.rag.vector_migration import copy_collection
from learnflow.rag.vectorstore import collection_name


async def verify():
    tenant = UUID(int=1)
    client = chromadb.HttpClient(host=os.environ["CHROMA_HOST"], port=int(os.environ["CHROMA_PORT"]))
    collection = client.get_collection(collection_name(tenant))
    source = collection.get(include=["documents", "metadatas", "embeddings"])
    assert source["ids"] == ["handbook:0"]

    connection = await asyncpg.connect(os.environ["VECTOR_TEST_DSN"])
    try:
        await connection.execute("CREATE EXTENSION IF NOT EXISTS vector")
        await connection.execute("""
            CREATE TEMP TABLE chunks (
                tenant uuid, id text, body text, metadata jsonb, embedding vector,
                PRIMARY KEY (tenant, id)
            )
        """)
        try:
            await copy_collection(collection, connection, UUID(int=2))
        except ValueError as exc:
            assert "tenant" in str(exc)
        else:
            raise AssertionError("Accepted a collection from another tenant")
        assert await connection.fetchval("SELECT count(*) FROM pg_temp.chunks") == 0
        for _ in range(2):
            copied = await copy_collection(collection, connection, tenant, batch_size=1)
            assert copied == 1
        assert await connection.fetchval("SELECT count(*) FROM pg_temp.chunks") == 1
        query = """
            SELECT id, body, metadata, embedding::text AS embedding
            FROM chunks WHERE tenant = $1 ORDER BY embedding <=> $2::vector LIMIT 1
        """
        row = await connection.fetchrow(query, tenant, "[1,0]")
        assert row["id"] == source["ids"][0]
        assert row["body"] == source["documents"][0]
        assert json.loads(row["metadata"]) == source["metadatas"][0]
        assert json.loads(row["embedding"]) == list(source["embeddings"][0])
        assert await connection.fetchrow(query, UUID(int=2), "[1,0]") is None
        # Exercise a real second page without depending on Chroma's record order.
        collection.add(ids=["appendix:0"], documents=["Appendix"],
                       metadatas=[{"page": 2}], embeddings=[[0.0, 1.0]])
        try:
            last_id = collection.get(limit=1, offset=1)["ids"][0]
            await connection.execute("TRUNCATE pg_temp.chunks")
            await connection.execute("""
                CREATE TEMP TABLE rejected_ids (id text PRIMARY KEY);
                CREATE FUNCTION pg_temp.reject_page() RETURNS trigger LANGUAGE plpgsql AS $
                BEGIN
                    IF EXISTS (SELECT FROM pg_temp.rejected_ids WHERE id = NEW.id) THEN
                        RAISE check_violation USING MESSAGE = 'fixture rejects second page';
                    END IF;
                    RETURN NEW;
                END $;
                CREATE TRIGGER reject_page BEFORE INSERT ON pg_temp.chunks
                FOR EACH ROW EXECUTE FUNCTION pg_temp.reject_page()
            """)
            await connection.execute("INSERT INTO pg_temp.rejected_ids VALUES ($1)", last_id)
            try:
                await copy_collection(collection, connection, tenant, batch_size=1)
            except asyncpg.CheckViolationError as exc:
                assert "fixture rejects second page" in str(exc)
            else:
                raise AssertionError("Expected second-page write to fail")
            assert await connection.fetchval("SELECT count(*) FROM pg_temp.chunks") == 0
            await connection.execute("TRUNCATE pg_temp.rejected_ids")
            assert await copy_collection(collection, connection, tenant, batch_size=1) == 2
            ids = await connection.fetch("SELECT id FROM pg_temp.chunks WHERE tenant = $1", tenant)
            assert {record["id"] for record in ids} == {"handbook:0", "appendix:0"}
        finally:
            collection.delete(ids=["appendix:0"])
    finally:
        await connection.close()
    assert collection.count() == 1


if __name__ == "__main__":
    asyncio.run(verify())
