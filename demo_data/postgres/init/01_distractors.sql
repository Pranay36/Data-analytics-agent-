-- Tables that exist to make retrieval hard.
--
-- With only the six core tables, picking the right one is close to guesswork
-- that happens to work: any top-5 over six tables scores well. These add
-- plausible wrong answers, several deliberately confusable:
--
--   orders_legacy        same columns as orders, but an archive nobody should query
--   order_events         has "order" in the name and a status column
--   web_sessions         has revenue-shaped columns, but is traffic not sales
--   marketing_campaigns  "spend", "channel" and "region" overlap with orders
--
-- Retrieval that cannot separate `orders` from `orders_legacy` would quietly
-- answer every revenue question from stale 2023 data.

-- The read-only role the agent connects as. Created here as well as by load_postgres so that
-- the GRANT at the bottom of this file works on a brand-new database, where this script runs
-- before anything else has had the chance to create it. The password is for the local demo
-- only; any real deployment overrides it.
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'insightflow_ro') THEN
        CREATE ROLE insightflow_ro LOGIN PASSWORD 'insightflow_ro';
    END IF;
END $$;

DROP TABLE IF EXISTS campaign_spend, marketing_campaigns, support_tickets,
    inventory_snapshots, web_sessions, order_events, orders_legacy, suppliers CASCADE;

CREATE TABLE orders_legacy (
    id integer PRIMARY KEY,
    customer_id integer,
    order_date timestamp,
    status text,
    channel text,
    shipping_region text,
    total_amount numeric(12,2)
);
COMMENT ON TABLE orders_legacy IS
    'DEPRECATED archive of 2023 orders, migrated from the old platform. Do not use for reporting; use orders instead.';

CREATE TABLE order_events (
    id integer PRIMARY KEY,
    order_id integer,
    event_type text,
    old_status text,
    new_status text,
    occurred_at timestamp
);
COMMENT ON TABLE order_events IS
    'Audit log of order status changes. One row per transition, not per order.';

CREATE TABLE web_sessions (
    id integer PRIMARY KEY,
    customer_id integer,
    started_at timestamp,
    device text,
    channel text,
    page_views integer,
    converted boolean
);
COMMENT ON TABLE web_sessions IS
    'Website visit tracking. Traffic and engagement only; carries no sales amounts.';

CREATE TABLE marketing_campaigns (
    id integer PRIMARY KEY,
    name text,
    channel text,
    region text,
    started_on date,
    ended_on date
);
COMMENT ON TABLE marketing_campaigns IS 'Marketing campaigns that have run, with their target region and channel.';

CREATE TABLE campaign_spend (
    id integer PRIMARY KEY,
    campaign_id integer,
    spend_date date,
    amount numeric(12,2),
    impressions integer,
    clicks integer
);
COMMENT ON TABLE campaign_spend IS 'Daily advertising spend per campaign. Cost, not revenue.';

CREATE TABLE support_tickets (
    id integer PRIMARY KEY,
    customer_id integer,
    order_id integer,
    subject text,
    category text,
    status text,
    opened_at timestamp,
    resolved_at timestamp
);
COMMENT ON TABLE support_tickets IS 'Customer support tickets, optionally linked to an order.';

CREATE TABLE inventory_snapshots (
    id integer PRIMARY KEY,
    product_id integer,
    warehouse text,
    snapshot_date date,
    units_on_hand integer
);
COMMENT ON TABLE inventory_snapshots IS 'Daily stock levels per product and warehouse.';

CREATE TABLE suppliers (
    id integer PRIMARY KEY,
    name text,
    country text,
    lead_time_days integer
);
COMMENT ON TABLE suppliers IS 'Suppliers that products are sourced from.';

-- A few rows each: column profiling samples real values, and an empty table
-- would produce a thinner chunk than a real one.
INSERT INTO orders_legacy
SELECT i, (i % 500) + 1, DATE '2023-01-01' + (i % 300) * INTERVAL '1 day',
       (ARRAY['SUCCESS','FAILED','CANCELLED'])[1 + i % 3],
       (ARRAY['web','mobile_app'])[1 + i % 2],
       (ARRAY['North','South','East','West'])[1 + i % 4],
       round((500 + (i * 37) % 9000)::numeric, 2)
FROM generate_series(1, 400) i;

INSERT INTO order_events
SELECT i, (i % 2000) + 1, (ARRAY['created','paid','shipped','cancelled'])[1 + i % 4],
       'PENDING', (ARRAY['SUCCESS','FAILED','CANCELLED'])[1 + i % 3],
       DATE '2026-01-01' + (i % 180) * INTERVAL '1 day'
FROM generate_series(1, 500) i;

INSERT INTO web_sessions
SELECT i, (i % 1500) + 1, DATE '2026-01-01' + (i % 180) * INTERVAL '1 day',
       (ARRAY['desktop','mobile','tablet'])[1 + i % 3],
       (ARRAY['web','mobile_app','marketplace'])[1 + i % 3],
       1 + i % 14, i % 7 = 0
FROM generate_series(1, 500) i;

INSERT INTO marketing_campaigns
SELECT i, 'Campaign ' || i, (ARRAY['email','social','search','display'])[1 + i % 4],
       (ARRAY['North','South','East','West'])[1 + i % 4],
       DATE '2025-07-01' + (i * 11) * INTERVAL '1 day',
       DATE '2025-07-21' + (i * 11) * INTERVAL '1 day'
FROM generate_series(1, 30) i;

INSERT INTO campaign_spend
SELECT i, (i % 30) + 1, DATE '2025-07-01' + (i % 330) * INTERVAL '1 day',
       round((1000 + (i * 53) % 40000)::numeric, 2), 1000 + (i * 97) % 90000, 10 + (i * 7) % 900
FROM generate_series(1, 400) i;

INSERT INTO support_tickets
SELECT i, (i % 1200) + 1, (i % 2000) + 1,
       'Ticket subject ' || i,
       (ARRAY['delivery','payment','product_quality','refund'])[1 + i % 4],
       (ARRAY['open','resolved','escalated'])[1 + i % 3],
       DATE '2026-01-01' + (i % 180) * INTERVAL '1 day',
       DATE '2026-01-03' + (i % 180) * INTERVAL '1 day'
FROM generate_series(1, 300) i;

INSERT INTO inventory_snapshots
SELECT i, (i % 54) + 1, (ARRAY['Mumbai DC','Delhi DC','Bengaluru DC'])[1 + i % 3],
       DATE '2026-05-01' + (i % 60) * INTERVAL '1 day', (i * 13) % 500
FROM generate_series(1, 400) i;

INSERT INTO suppliers
SELECT i, 'Supplier ' || i, (ARRAY['India','China','Vietnam','Germany'])[1 + i % 4], 5 + i % 40
FROM generate_series(1, 25) i;

GRANT SELECT ON ALL TABLES IN SCHEMA public TO insightflow_ro;
