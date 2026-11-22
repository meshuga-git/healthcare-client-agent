-- PostgreSQL deployment schema. The database independently rejects resource overlap.
CREATE EXTENSION IF NOT EXISTS btree_gist;
CREATE TABLE IF NOT EXISTS records (
 collection TEXT NOT NULL, id TEXT NOT NULL, body TEXT NOT NULL,
 PRIMARY KEY (collection,id)
);
CREATE TABLE IF NOT EXISTS allocations (
 id TEXT PRIMARY KEY, resource_id TEXT NOT NULL,
 starts DOUBLE PRECISION NOT NULL, ends DOUBLE PRECISION NOT NULL,
 owner TEXT NOT NULL,
 CHECK (ends > starts),
 EXCLUDE USING gist (resource_id WITH =, numrange(starts::numeric, ends::numeric, '[)') WITH &&)
);
CREATE INDEX IF NOT EXISTS allocation_owner ON allocations(owner);
