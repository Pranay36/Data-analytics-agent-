-- ShopSphere: the demo "customer" warehouse.
--
-- This is the analytical plane — business data we query in place and never copy
-- into InsightFlow's own database (PROJECT_PLAN §2.3).
--
-- The COMMENT statements are not decoration. Schema introspection reads them,
-- they become part of the text embedded for retrieval, and they are often the
-- difference between the agent picking `orders.shipping_region` and wrongly
-- picking `customers.region`.

DROP TABLE IF EXISTS refunds, payments, order_items, orders, products, customers CASCADE;

CREATE TABLE customers (
    id          integer PRIMARY KEY,
    name        text        NOT NULL,
    email       text        NOT NULL,
    city        text        NOT NULL,
    region      text        NOT NULL,
    segment     text        NOT NULL,
    signup_date date        NOT NULL
);

COMMENT ON TABLE  customers        IS 'One row per registered customer.';
COMMENT ON COLUMN customers.region IS
    'Customer home region. NOT the region used for revenue reporting — use orders.shipping_region for that.';
COMMENT ON COLUMN customers.segment IS 'Customer type: consumer or small_business.';

CREATE TABLE products (
    id         integer PRIMARY KEY,
    name       text          NOT NULL,
    category   text          NOT NULL,
    brand      text          NOT NULL,
    unit_price numeric(12,2) NOT NULL,
    cost       numeric(12,2) NOT NULL
);

COMMENT ON TABLE  products          IS 'Product catalogue. One row per sellable product.';
COMMENT ON COLUMN products.category IS
    'Product category: Electronics, Fashion, Home & Kitchen, Beauty, Books, Sports.';
COMMENT ON COLUMN products.unit_price IS 'Current list price in INR.';
COMMENT ON COLUMN products.cost       IS 'Unit cost to the business in INR. Margin = unit_price - cost.';

CREATE TABLE orders (
    id              integer PRIMARY KEY,
    customer_id     integer       NOT NULL REFERENCES customers (id),
    order_date      timestamp     NOT NULL,
    status          text          NOT NULL,
    channel         text          NOT NULL,
    payment_method  text          NOT NULL,
    shipping_region text          NOT NULL,
    total_amount    numeric(12,2) NOT NULL,
    discount_amount numeric(12,2) NOT NULL
);

COMMENT ON TABLE  orders        IS
    'One row per order placed, including orders that failed or were cancelled.';
COMMENT ON COLUMN orders.status IS
    'Order outcome: SUCCESS, FAILED, CANCELLED or PENDING. Only SUCCESS counts as revenue.';
COMMENT ON COLUMN orders.shipping_region IS
    'Delivery region (North, South, East, West). This is the region used for revenue reporting.';
COMMENT ON COLUMN orders.total_amount IS
    'Order value in INR after discount. Revenue is the sum of this column where status = SUCCESS.';
COMMENT ON COLUMN orders.channel IS 'Where the order was placed: web, mobile_app or marketplace.';

CREATE TABLE order_items (
    id          integer PRIMARY KEY,
    order_id    integer       NOT NULL REFERENCES orders (id),
    product_id  integer       NOT NULL REFERENCES products (id),
    quantity    integer       NOT NULL,
    unit_price  numeric(12,2) NOT NULL,
    line_amount numeric(12,2) NOT NULL
);

COMMENT ON TABLE  order_items             IS 'Line items. One row per product within an order.';
COMMENT ON COLUMN order_items.line_amount IS
    'Line value in INR (quantity x unit_price). Use this for revenue broken down by product or category.';

CREATE TABLE payments (
    id        integer PRIMARY KEY,
    order_id  integer       NOT NULL REFERENCES orders (id),
    method    text          NOT NULL,
    status    text          NOT NULL,
    amount    numeric(12,2) NOT NULL,
    paid_at   timestamp
);

COMMENT ON TABLE  payments        IS 'Payment attempt per order.';
COMMENT ON COLUMN payments.status IS 'captured or failed.';
COMMENT ON COLUMN payments.method IS 'card, upi, wallet or cod.';

CREATE TABLE refunds (
    id            integer PRIMARY KEY,
    order_id      integer       NOT NULL REFERENCES orders (id),
    order_item_id integer       NOT NULL REFERENCES order_items (id),
    amount        numeric(12,2) NOT NULL,
    reason        text          NOT NULL,
    refunded_at   timestamp     NOT NULL
);

COMMENT ON TABLE  refunds        IS 'Refunds issued, at line-item granularity.';
COMMENT ON COLUMN refunds.reason IS 'defective, wrong_item, late_delivery or changed_mind.';
COMMENT ON COLUMN refunds.amount IS 'Amount refunded in INR.';

-- Indexes matching how the agent actually filters: by date, region and status.
CREATE INDEX idx_orders_order_date    ON orders (order_date);
CREATE INDEX idx_orders_status        ON orders (status);
CREATE INDEX idx_orders_region        ON orders (shipping_region);
CREATE INDEX idx_orders_customer      ON orders (customer_id);
CREATE INDEX idx_order_items_order    ON order_items (order_id);
CREATE INDEX idx_order_items_product  ON order_items (product_id);
CREATE INDEX idx_refunds_refunded_at  ON refunds (refunded_at);
CREATE INDEX idx_refunds_item         ON refunds (order_item_id);
CREATE INDEX idx_payments_order       ON payments (order_id);
