-- Apply once to the destination database before importing staged chunks.
BEGIN;
CREATE EXTENSION IF NOT EXISTS vector;
CREATE SCHEMA learnflow_vector;
CREATE TABLE learnflow_vector.chunks (
    tenant uuid NOT NULL,
    id text NOT NULL,
    body text NOT NULL,
    metadata jsonb,
    embedding vector NOT NULL,
    PRIMARY KEY (tenant, id)
);
COMMIT;
