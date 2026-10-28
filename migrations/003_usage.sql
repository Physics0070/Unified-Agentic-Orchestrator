-- Durable daily token/spend counter for the public-site cap (backend/budget.py).
-- The app creates this idempotently at first use; applying it by hand is optional:
--   turso db shell <db-name> < migrations/003_usage.sql

CREATE TABLE IF NOT EXISTS usage_daily (
  day      TEXT PRIMARY KEY,
  tokens   INTEGER NOT NULL DEFAULT 0,
  cost_usd REAL NOT NULL DEFAULT 0
);
