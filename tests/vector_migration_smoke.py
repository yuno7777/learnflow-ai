"""Copy the disposable Chroma fixture to pgvector and compare the retrieved citation."""
import asyncio
import json
import os
from pathlib import Path
from uuid import UUID

import asyncpg
import chromadb

from learnflow.rag.pgvector_reader import query_chunks
from learnflow.rag.vector_migration import copy_collection, promote_staged_chunks
from learnflow.rag.vectorstore import collection_name


async def verify():
    tenant = UUID(int=1)
    client = chromadb.HttpClient(host=os.environ["CHROMA_HOST"], port=int(os.environ["CHROMA_PORT"]))
    collection = client.get_collection(collection_name(tenant))
    source = collection.get(include=["documents", "metadatas", "embeddings"])
    assert source["ids"] == ["handbook:0"]

    connection = await asyncpg.connect(os.environ["VECTOR_TEST_DSN"])
    try:
        schema = Path(__file__).resolve().parents[1] / "ops/pgvector/001_chunks.sql"
        await connection.execute(schema.read_text(encoding="utf-8"))
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
                CREATE FUNCTION pg_temp.reject_page() RETURNS trigger LANGUAGE plpgsql AS $reject$
                BEGIN
                    IF EXISTS (SELECT FROM pg_temp.rejected_ids WHERE id = NEW.id) THEN
                        RAISE check_violation USING MESSAGE = 'fixture rejects second page';
                    END IF;
                    RETURN NEW;
                END; $reject$;
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
            # Compare the full ranked result and citations for each fixture vector.
            for probe in ([1.0, 0.0], [0.0, 1.0]):
                expected = collection.query(
                    query_embeddings=[probe], n_results=2,
                    include=["documents", "metadatas"],
                )
                rows = await connection.fetch(
                    """SELECT id, body, metadata FROM pg_temp.chunks
                       WHERE tenant = $1 ORDER BY embedding <=> $2::vector, id LIMIT 2""",
                    tenant, json.dumps(probe),
                )
                assert [row["id"] for row in rows] == expected["ids"][0]
                assert [row["body"] for row in rows] == expected["documents"][0]
                assert [json.loads(row["metadata"]) for row in rows] == expected["metadatas"][0]
            await connection.execute("""
                INSERT INTO pg_temp.chunks (tenant, id, body, metadata, embedding)
                VALUES ($1, 'unrelated', 'other tenant', '{}', '[0,1]')
            """, UUID(int=2))
            for _ in range(2):
                await promote_staged_chunks(connection, tenant)
            assert await connection.fetchval(
                "SELECT count(*) FROM learnflow_vector.chunks WHERE tenant = $1", UUID(int=2)
            ) == 0
        finally:
            collection.delete(ids=["appendix:0"])
    finally:
        await connection.close()
    reader = await asyncpg.connect(os.environ["VECTOR_TEST_DSN"])
    try:
        ids = await reader.fetch("SELECT id FROM learnflow_vector.chunks WHERE tenant = $1", tenant)
        assert {row["id"] for row in ids} == {"handbook:0", "appendix:0"}
        matches = await query_chunks(reader, tenant, [1.0, 0.0], top_k=2)
        assert [m.id for m in matches] == ["handbook:0", "appendix:0"]
        assert matches[0].text == source["documents"][0]
        assert matches[0].metadata == source["metadatas"][0]
        for page_filter in (2, [2, 99]):
            filtered = await query_chunks(reader, tenant, [1.0, 0.0], top_k=1, where={"page": page_filter})
            assert [m.id for m in filtered] == ["appendix:0"]
        for criteria in ({"page": []}, {"page": "2"}, {"page": 2, "missing": True}):
            assert await query_chunks(reader, tenant, [1.0, 0.0], top_k=2, where=criteria) == []
        assert [m.score for m in matches] == [1.0, 0.0]
        assert await query_chunks(reader, UUID(int=2), [1.0, 0.0], top_k=2) == []
    finally:
        await reader.close()
    assert collection.count() == 1


if __name__ == "__main__":
    asyncio.run(verify())
