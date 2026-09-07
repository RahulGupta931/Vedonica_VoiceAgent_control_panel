-- Minimal schema matching app/db.py. Adjust to your real data model —
-- in most real deployments `orders`/`customers` already exist and you'll
-- just point DATABASE_URL at that database and adapt the queries in db.py.

CREATE TABLE IF NOT EXISTS customers (
    customer_id   UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    phone         TEXT UNIQUE NOT NULL,
    full_name     TEXT,
    tier          TEXT DEFAULT 'standard',
    created_at    TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE IF NOT EXISTS orders (
    order_id        TEXT PRIMARY KEY,
    customer_phone  TEXT NOT NULL REFERENCES customers(phone),
    status          TEXT NOT NULL,
    eta_date        DATE,
    total_amount    NUMERIC(10, 2),
    item_summary    TEXT,
    created_at      TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE IF NOT EXISTS support_tickets (
    ticket_id       UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    customer_phone  TEXT NOT NULL,
    summary         TEXT NOT NULL,
    priority        TEXT NOT NULL DEFAULT 'normal',
    status          TEXT NOT NULL DEFAULT 'open',
    created_at      TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE IF NOT EXISTS call_transcripts (
    id          BIGSERIAL PRIMARY KEY,
    call_sid    TEXT NOT NULL,
    role        TEXT NOT NULL,   -- 'user' | 'assistant'
    text        TEXT NOT NULL,
    created_at  TIMESTAMPTZ DEFAULT now()
);

-- One row per call (Twilio call, or a browser test-console session). Used
-- by the dashboard for the call list; call_transcripts holds the turns.
CREATE TABLE IF NOT EXISTS calls (
    call_sid        TEXT PRIMARY KEY,
    caller_number   TEXT,
    source          TEXT NOT NULL DEFAULT 'twilio',  -- 'twilio' | 'test_console'
    started_at      TIMESTAMPTZ DEFAULT now(),
    ended_at        TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_orders_customer_phone ON orders(customer_phone);
CREATE INDEX IF NOT EXISTS idx_tickets_customer_phone ON support_tickets(customer_phone);
CREATE INDEX IF NOT EXISTS idx_transcripts_call_sid ON call_transcripts(call_sid);
CREATE INDEX IF NOT EXISTS idx_calls_started_at ON calls(started_at);
