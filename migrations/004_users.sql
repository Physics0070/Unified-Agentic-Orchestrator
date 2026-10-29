-- Optional user accounts (backend/auth.py). The app creates all of this itself at
-- first use, idempotently; applying it by hand is optional:
--   turso db shell <db-name> < migrations/004_users.sql

CREATE TABLE IF NOT EXISTS users (
  id            TEXT PRIMARY KEY,
  email         TEXT NOT NULL UNIQUE,          -- lower-cased
  password_hash TEXT NOT NULL,                 -- scrypt$n$r$p$salt$hash, never the password
  created_at    TEXT NOT NULL
);

-- Run ownership. Nullable: runs made anonymously (all of them, before accounts)
-- keep NULL. SQLite has no "ADD COLUMN IF NOT EXISTS": run this ONCE; a second
-- run fails with "duplicate column name", which is harmless (the app itself
-- ignores exactly that error). The append-only triggers on `events` are
-- unaffected.
ALTER TABLE runs ADD COLUMN user_id TEXT;
