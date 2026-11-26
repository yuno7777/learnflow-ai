"""Copy the disposable Chroma fixture to pgvector and compare the retrieved citation."""
import asyncio
import json
import os
from uuid import UUID

import asyncpg
import chromadb

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
        rows = zip(source["ids"], source["documents"], source["metadatas"],
                   source["embeddings"], strict=True)
        for record_id, body, metadata, embedding in rows:
            await connection.execute(
                "INSERT INTO chunks VALUES ($1, $2, $3, $4, $5)",
                tenant, record_id, body, json.dumps(metadata), json.dumps(list(map(float, embedding))),
            )
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
    finally:
        await connection.close()
    assert collection.count() == 1


if __name__ == "__main__":
    asyncio.run(verify())
