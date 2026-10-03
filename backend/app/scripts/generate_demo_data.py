"""Generate the ShopSphere demo dataset.

The dataset exists to make evaluation *objective*. Because we generate the data
ourselves, we know the true answer to every question we ask the agent — so
"did it get it right?" is a computation, not an opinion
(see PROJECT_PLAN §18.2).

Three patterns are planted deliberately:

1. **June 2026 revenue drop**, concentrated in the South region and, within
   South, in Electronics. This is what the drill-down feature must discover.
2. **A refund spike** in Home & Kitchen over May–June 2026, driven by one
   defective product.
3. **~8% of orders never complete** (FAILED / CANCELLED / PENDING). This is the
   trap: revenue means *successful* orders only, and a model that ignores
   `status` gets a plausible but wrong number.

Everything is driven by a fixed seed, so the data is identical on every machine.
Ground-truth facts are *computed from the generated rows* rather than hardcoded,
so they can never drift away from the data they describe.

Usage:
    uv run python -m app.scripts.generate_demo_data
"""

from __future__ import annotations

import csv
import json
import random
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

import numpy as np
from faker import Faker

# ── Reproducibility ──────────────────────────────────────────────────────────
SEED = 20260630
rng = np.random.default_rng(SEED)
faker = Faker("en_IN")
Faker.seed(SEED)
random.seed(SEED)

OUTPUT_DIR = Path(__file__).resolve().parents[3] / "demo_data" / "generated"

# ── Shape of the business ────────────────────────────────────────────────────
START_MONTH = date(2025, 7, 1)
MONTHS = 12                      # July 2025 → June 2026
AS_OF_DATE = date(2026, 6, 30)   # "today" for the demo; see PROJECT_PLAN §15.6

N_CUSTOMERS = 2_000
N_PRODUCTS = 60
ORDERS_PER_MONTH = 2_100         # ≈ 25k orders over the year

REGIONS = ["North", "South", "East", "West"]
REGION_WEIGHTS = [0.30, 0.28, 0.22, 0.20]

CATEGORIES = ["Electronics", "Fashion", "Home & Kitchen", "Beauty", "Books", "Sports"]
CATEGORY_WEIGHTS = [0.26, 0.24, 0.18, 0.14, 0.10, 0.08]

CHANNELS = ["web", "mobile_app", "marketplace"]
CHANNEL_WEIGHTS = [0.45, 0.38, 0.17]

PAYMENT_METHODS = ["card", "upi", "wallet", "cod"]
PAYMENT_WEIGHTS = [0.34, 0.40, 0.12, 0.14]

SEGMENTS = ["consumer", "small_business"]
SEGMENT_WEIGHTS = [0.82, 0.18]

# Order outcomes. ~8% of orders never become revenue — pattern 3.
ORDER_STATUSES = ["SUCCESS", "FAILED", "CANCELLED", "PENDING"]
STATUS_WEIGHTS = [0.92, 0.035, 0.03, 0.015]

REFUND_REASONS = ["defective", "wrong_item", "late_delivery", "changed_mind"]
BASE_REFUND_RATE = 0.028         # share of successful order lines refunded

# ── Planted pattern 1: the June 2026 collapse ────────────────────────────────
# Multipliers applied to order volume in June 2026 only. South is hit hardest,
# and inside South, Electronics is hit hardest of all — so a single-level
# breakdown by region is not enough to explain it. That is what forces the
# agent to drill a second level.
JUNE = date(2026, 6, 1)
JUNE_REGION_VOLUME = {"North": 0.98, "South": 0.84, "East": 1.00, "West": 0.99}
JUNE_SOUTH_ELECTRONICS_WEIGHT = 0.52   # Electronics' share within South, scaled
JUNE_SOUTH_ELECTRONICS_BASKET = 0.72   # and smaller baskets when it is bought

# ── Planted pattern 2: the Home & Kitchen refund spike ───────────────────────
SPIKE_PRODUCT = "AeroBlend Pro Blender"
SPIKE_CATEGORY = "Home & Kitchen"
SPIKE_MONTHS = {date(2026, 5, 1), date(2026, 6, 1)}
SPIKE_REFUND_RATE = 0.42         # for the faulty product during the spike
SPIKE_CATEGORY_REFUND_RATE = 0.10  # elevated across the category too

CITIES_BY_REGION = {
    "North": ["Delhi", "Jaipur", "Lucknow", "Chandigarh", "Noida"],
    "South": ["Bengaluru", "Chennai", "Hyderabad", "Kochi", "Coimbatore"],
    "East": ["Kolkata", "Bhubaneswar", "Patna", "Guwahati", "Ranchi"],
    "West": ["Mumbai", "Pune", "Ahmedabad", "Surat", "Nagpur"],
}

PRODUCT_NAMES: dict[str, list[str]] = {
    "Electronics": [
        "Nimbus 5G Smartphone", "Vertex Noise-Cancelling Headphones", "Orbit Smartwatch",
        "Pulse Bluetooth Speaker", "Lumen 4K Monitor", "Cobalt Mechanical Keyboard",
        "Quartz Wireless Mouse", "Helios Power Bank", "Vantage Laptop Sleeve",
        "Zenith Tablet 11", "Apex Action Camera", "Strata USB-C Hub",
    ],
    "Fashion": [
        "Harbour Cotton Shirt", "Meridian Denim Jacket", "Trailhead Running Shoes",
        "Linden Linen Kurta", "Cove Leather Belt", "Summit Hoodie",
        "Dune Chino Trousers", "Aster Silk Scarf", "Ridge Canvas Backpack",
        "Willow Summer Dress", "Fern Ankle Boots",
    ],
    "Home & Kitchen": [
        SPIKE_PRODUCT, "Hearth Cast Iron Skillet", "Verdant Indoor Planter",
        "Clay Ceramic Dinner Set", "Aurora Table Lamp", "Drift Cotton Bedsheet",
        "Basil Spice Rack", "Nook Storage Basket", "Ember Coffee Press",
        "Terra Dining Mat",
    ],
    "Beauty": [
        "Rosewater Facial Mist", "Amber Beard Oil", "Silk Shampoo Bar",
        "Dewdrop Moisturiser", "Clove Lip Balm", "Saffron Face Serum",
        "Marine Clay Mask", "Petal Hand Cream",
    ],
    "Books": [
        "The Quiet Algorithm", "Monsoon Letters", "Atlas of Small Things",
        "The Last Ledger", "Nightfall in Nagpur", "Paper Boats",
        "A Brief History of Tea",
    ],
    "Sports": [
        "Vector Yoga Mat", "Grip Pro Dumbbell Set", "Tempo Cricket Bat",
        "Range Badminton Racket", "Stride Running Belt", "Flow Resistance Bands",
    ],
}

PRICE_RANGE_BY_CATEGORY = {
    "Electronics": (2_000, 38_000),
    "Fashion": (500, 6_500),
    "Home & Kitchen": (700, 11_000),
    "Beauty": (200, 3_200),
    "Books": (150, 1_400),
    "Sports": (500, 8_500),
}


# ── Helpers ──────────────────────────────────────────────────────────────────
def money(value: float) -> Decimal:
    """Round to paise. Money is never a float once it leaves this module."""
    return Decimal(str(value)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def month_starts() -> list[date]:
    months = []
    year, month = START_MONTH.year, START_MONTH.month
    for _ in range(MONTHS):
        months.append(date(year, month, 1))
        year, month = (year + 1, 1) if month == 12 else (year, month + 1)
    return months


def days_in_month(first: date) -> int:
    nxt = date(first.year + 1, 1, 1) if first.month == 12 else date(first.year, first.month + 1, 1)
    return (nxt - first).days


def pick(options: list[str], weights: list[float]) -> str:
    return str(rng.choice(options, p=np.array(weights) / np.sum(weights)))


def random_datetime_in(month: date) -> datetime:
    day = int(rng.integers(1, days_in_month(month) + 1))
    hour, minute = int(rng.integers(6, 23)), int(rng.integers(0, 60))
    return datetime(month.year, month.month, day, hour, minute)


# ── Row types ────────────────────────────────────────────────────────────────
@dataclass
class Product:
    id: int
    name: str
    category: str
    brand: str
    unit_price: Decimal
    cost: Decimal


@dataclass
class Customer:
    id: int
    name: str
    email: str
    city: str
    region: str
    segment: str
    signup_date: date


# ── Generators ───────────────────────────────────────────────────────────────
def generate_customers() -> list[Customer]:
    customers = []
    earliest = START_MONTH - timedelta(days=540)
    for customer_id in range(1, N_CUSTOMERS + 1):
        region = pick(REGIONS, REGION_WEIGHTS)
        name = faker.name()
        customers.append(
            Customer(
                id=customer_id,
                name=name,
                # Synthetic address on a reserved example domain — never routable.
                email=f"{name.lower().replace(' ', '.')}{customer_id}@example.com",
                city=str(rng.choice(CITIES_BY_REGION[region])),
                region=region,
                segment=pick(SEGMENTS, SEGMENT_WEIGHTS),
                signup_date=earliest + timedelta(days=int(rng.integers(0, 900))),
            )
        )
    return customers


def generate_products() -> list[Product]:
    products: list[Product] = []
    product_id = 1
    for category, names in PRODUCT_NAMES.items():
        low, high = PRICE_RANGE_BY_CATEGORY[category]
        for name in names:
            if product_id > N_PRODUCTS:
                break
            # Log-uniform pricing: many affordable items, a few expensive ones.
            price = float(np.exp(rng.uniform(np.log(low), np.log(high))))
            price = round(price / 10) * 10 - 1 if price > 500 else round(price)
            products.append(
                Product(
                    id=product_id,
                    name=name,
                    category=category,
                    brand=name.split()[0],
                    unit_price=money(price),
                    cost=money(price * rng.uniform(0.52, 0.78)),
                )
            )
            product_id += 1
    return products


def _category_weights_for(month: date, region: str) -> list[float]:
    """Category mix, adjusted for the planted June collapse in South Electronics."""
    weights = list(CATEGORY_WEIGHTS)
    if month == JUNE and region == "South":
        weights[CATEGORIES.index("Electronics")] *= JUNE_SOUTH_ELECTRONICS_WEIGHT
    return weights


def _orders_this_month(month: date, region: str) -> int:
    """How many orders a region produces in a month, before noise."""
    share = REGION_WEIGHTS[REGIONS.index(region)]
    base = ORDERS_PER_MONTH * share
    if month == JUNE:
        base *= JUNE_REGION_VOLUME[region]
    return int(rng.normal(base, base * 0.04))


def generate_orders(
    customers: list[Customer], products: list[Product]
) -> tuple[list[dict], list[dict], list[dict], list[dict]]:
    """Build orders and everything hanging off them.

    Returns (orders, order_items, payments, refunds).
    """
    by_category: dict[str, list[Product]] = defaultdict(list)
    for product in products:
        by_category[product.category].append(product)
    customers_by_region: dict[str, list[Customer]] = defaultdict(list)
    for customer in customers:
        customers_by_region[customer.region].append(customer)

    orders: list[dict] = []
    order_items: list[dict] = []
    payments: list[dict] = []
    refunds: list[dict] = []

    order_id = item_id = payment_id = refund_id = 1

    for month in month_starts():
        for region in REGIONS:
            category_weights = _category_weights_for(month, region)

            for _ in range(_orders_this_month(month, region)):
                customer = customers_by_region[region][
                    int(rng.integers(0, len(customers_by_region[region])))
                ]
                ordered_at = random_datetime_in(month)
                status = pick(ORDER_STATUSES, STATUS_WEIGHTS)

                # Basket: 1–4 lines, occasionally more.
                n_lines = int(rng.choice([1, 2, 3, 4, 5], p=[0.46, 0.28, 0.15, 0.08, 0.03]))
                lines: list[dict] = []
                for _ in range(n_lines):
                    category = pick(CATEGORIES, category_weights)
                    product = by_category[category][
                        int(rng.integers(0, len(by_category[category])))
                    ]
                    quantity = int(rng.choice([1, 2, 3], p=[0.74, 0.20, 0.06]))

                    # Inside the collapse, even the baskets that do happen are smaller.
                    if (
                        month == JUNE
                        and region == "South"
                        and category == "Electronics"
                        and rng.random() > JUNE_SOUTH_ELECTRONICS_BASKET
                    ):
                        quantity = 1

                    line_amount = money(float(product.unit_price) * quantity)
                    lines.append(
                        {
                            "id": item_id,
                            "order_id": order_id,
                            "product_id": product.id,
                            "quantity": quantity,
                            "unit_price": product.unit_price,
                            "line_amount": line_amount,
                            "_category": category,
                            "_product_name": product.name,
                        }
                    )
                    item_id += 1

                gross = sum(float(line["line_amount"]) for line in lines)
                discount_pct = rng.choice([0, 0, 0, 0.05, 0.10], p=[0.62, 0.12, 0.08, 0.1, 0.08])
                discount = money(gross * discount_pct)
                total = money(max(gross - float(discount), 0))

                orders.append(
                    {
                        "id": order_id,
                        "customer_id": customer.id,
                        "order_date": ordered_at.isoformat(sep=" "),
                        "status": status,
                        "channel": pick(CHANNELS, CHANNEL_WEIGHTS),
                        "payment_method": pick(PAYMENT_METHODS, PAYMENT_WEIGHTS),
                        "shipping_region": region,
                        "total_amount": total,
                        "discount_amount": discount,
                    }
                )
                order_items.extend(lines)

                payments.append(
                    {
                        "id": payment_id,
                        "order_id": order_id,
                        "method": orders[-1]["payment_method"],
                        "status": "captured" if status == "SUCCESS" else "failed",
                        "amount": total,
                        "paid_at": ordered_at.isoformat(sep=" ") if status == "SUCCESS" else "",
                    }
                )
                payment_id += 1

                # Refunds only exist for orders that actually completed.
                if status == "SUCCESS":
                    for line in lines:
                        rate = BASE_REFUND_RATE
                        if month in SPIKE_MONTHS:
                            if line["_product_name"] == SPIKE_PRODUCT:
                                rate = SPIKE_REFUND_RATE
                            elif line["_category"] == SPIKE_CATEGORY:
                                rate = SPIKE_CATEGORY_REFUND_RATE

                        if rng.random() < rate:
                            reason = (
                                "defective"
                                if line["_product_name"] == SPIKE_PRODUCT
                                and month in SPIKE_MONTHS
                                else pick(REFUND_REASONS, [0.28, 0.22, 0.2, 0.3])
                            )
                            refunds.append(
                                {
                                    "id": refund_id,
                                    "order_id": order_id,
                                    "order_item_id": line["id"],
                                    "amount": line["line_amount"],
                                    "reason": reason,
                                    "refunded_at": (
                                        ordered_at + timedelta(days=int(rng.integers(2, 21)))
                                    ).isoformat(sep=" "),
                                }
                            )
                            refund_id += 1

                order_id += 1

    for line in order_items:
        line.pop("_category", None)
        line.pop("_product_name", None)

    return orders, order_items, payments, refunds


# ── Ground truth ─────────────────────────────────────────────────────────────
def compute_ground_truth(
    orders: list[dict],
    order_items: list[dict],
    refunds: list[dict],
    products: list[Product],
    customers: list[Customer],
) -> dict:
    """Derive the true answers *from the generated rows*.

    Never hardcode these: if the generator changes, the facts must follow.
    """
    category_of = {product.id: product.category for product in products}
    order_by_id = {order["id"]: order for order in orders}
    customer_name = {customer.id: customer.name for customer in customers}

    def month_of(value: str) -> str:
        return value[:7]

    successful = [order for order in orders if order["status"] == "SUCCESS"]

    revenue_by_month: dict[str, float] = defaultdict(float)
    revenue_by_month_region: dict[tuple[str, str], float] = defaultdict(float)
    revenue_by_customer: dict[int, float] = defaultdict(float)
    for order in successful:
        month = month_of(order["order_date"])
        revenue_by_month[month] += float(order["total_amount"])
        revenue_by_month_region[(month, order["shipping_region"])] += float(order["total_amount"])
        revenue_by_customer[order["customer_id"]] += float(order["total_amount"])

    # Category-level revenue uses line amounts, since an order spans categories.
    south_category: dict[tuple[str, str], float] = defaultdict(float)
    category_month: dict[tuple[str, str], float] = defaultdict(float)
    for line in order_items:
        order = order_by_id[line["order_id"]]
        if order["status"] != "SUCCESS":
            continue
        month = month_of(order["order_date"])
        category = category_of[line["product_id"]]
        category_month[(month, category)] += float(line["line_amount"])
        if order["shipping_region"] == "South":
            south_category[(month, category)] += float(line["line_amount"])

    def pct_change(previous: float, current: float) -> float:
        return round((current - previous) / previous * 100, 2) if previous else 0.0

    may, june = "2026-05", "2026-06"

    region_changes = {
        region: {
            "may": round(revenue_by_month_region[(may, region)], 2),
            "june": round(revenue_by_month_region[(june, region)], 2),
            "pct_change": pct_change(
                revenue_by_month_region[(may, region)], revenue_by_month_region[(june, region)]
            ),
        }
        for region in REGIONS
    }
    total_decline = revenue_by_month[june] - revenue_by_month[may]
    for values in region_changes.values():
        change = values["june"] - values["may"]
        values["share_of_total_change"] = (
            round(change / total_decline * 100, 2) if total_decline else 0.0
        )

    south_changes = {
        category: {
            "may": round(south_category[(may, category)], 2),
            "june": round(south_category[(june, category)], 2),
            "pct_change": pct_change(
                south_category[(may, category)], south_category[(june, category)]
            ),
        }
        for category in CATEGORIES
    }

    # Refund rate by category and month: refunded line value / sold line value.
    refunded_value: dict[tuple[str, str], float] = defaultdict(float)
    refund_count_by_product: dict[str, int] = defaultdict(int)
    item_by_id = {line["id"]: line for line in order_items}
    product_name = {product.id: product.name for product in products}
    for refund in refunds:
        line = item_by_id[refund["order_item_id"]]
        key = (month_of(refund["refunded_at"]), category_of[line["product_id"]])
        refunded_value[key] += float(refund["amount"])
        refund_count_by_product[product_name[line["product_id"]]] += 1

    refund_rate_home_kitchen = {
        month: round(
            refunded_value[(month, SPIKE_CATEGORY)]
            / category_month[(month, SPIKE_CATEGORY)]
            * 100,
            2,
        )
        if category_month[(month, SPIKE_CATEGORY)]
        else 0.0
        for month in sorted({month_of(order["order_date"]) for order in successful})
    }

    status_counts: dict[str, int] = defaultdict(int)
    for order in orders:
        status_counts[order["status"]] += 1

    top_customers = sorted(revenue_by_customer.items(), key=lambda kv: kv[1], reverse=True)[:10]

    worst_region = min(region_changes.items(), key=lambda kv: kv[1]["pct_change"])
    worst_category_in_south = min(south_changes.items(), key=lambda kv: kv[1]["pct_change"])

    return {
        "_description": (
            "True answers computed from the generated rows. Used by the evaluation "
            "suite so correctness is objective rather than a matter of opinion."
        ),
        "seed": SEED,
        "as_of_date": AS_OF_DATE.isoformat(),
        "revenue_definition": "SUM(orders.total_amount) WHERE orders.status = 'SUCCESS'",
        "row_counts": {
            "customers": len(customers),
            "products": len(products),
            "orders": len(orders),
            "order_items": len(order_items),
            "refunds": len(refunds),
        },
        "order_status_distribution": {
            status: {
                "count": count,
                "pct": round(count / len(orders) * 100, 2),
            }
            for status, count in sorted(status_counts.items())
        },
        "revenue_by_month": {
            month: round(value, 2) for month, value in sorted(revenue_by_month.items())
        },
        "pattern_1_june_revenue_drop": {
            "may_revenue": round(revenue_by_month[may], 2),
            "june_revenue": round(revenue_by_month[june], 2),
            "pct_change": pct_change(revenue_by_month[may], revenue_by_month[june]),
            "by_region": region_changes,
            "worst_region": worst_region[0],
            "south_by_category": south_changes,
            "worst_category_in_south": worst_category_in_south[0],
            "expected_drilldown_path": [
                {"dimension": "orders.shipping_region", "segment": worst_region[0]},
                {"dimension": "products.category", "segment": worst_category_in_south[0]},
            ],
        },
        "pattern_2_refund_spike": {
            "category": SPIKE_CATEGORY,
            "product": SPIKE_PRODUCT,
            "refund_rate_pct_by_month": refund_rate_home_kitchen,
            "top_refunded_products": dict(
                sorted(refund_count_by_product.items(), key=lambda kv: kv[1], reverse=True)[:5]
            ),
        },
        "pattern_3_incomplete_orders": {
            "note": "Revenue must count SUCCESS only; ignoring status inflates it.",
            "non_success_pct": round(
                (len(orders) - status_counts["SUCCESS"]) / len(orders) * 100, 2
            ),
            "revenue_if_status_ignored": round(
                sum(float(order["total_amount"]) for order in orders), 2
            ),
            "correct_total_revenue": round(sum(revenue_by_month.values()), 2),
        },
        "top_10_customers_by_revenue": [
            {"customer_id": cid, "name": customer_name[cid], "revenue": round(value, 2)}
            for cid, value in top_customers
        ],
        "average_order_value": round(
            sum(float(order["total_amount"]) for order in successful) / len(successful), 2
        ),
    }


# ── Output ───────────────────────────────────────────────────────────────────
def write_csv(filename: str, rows: list[dict], columns: list[str]) -> Path:
    path = OUTPUT_DIR / filename
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    return path


def main() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    print(f"Generating ShopSphere demo data (seed={SEED}) …")

    customers = generate_customers()
    products = generate_products()
    orders, order_items, payments, refunds = generate_orders(customers, products)

    write_csv(
        "customers.csv",
        [vars(customer) for customer in customers],
        ["id", "name", "email", "city", "region", "segment", "signup_date"],
    )
    write_csv(
        "products.csv",
        [vars(product) for product in products],
        ["id", "name", "category", "brand", "unit_price", "cost"],
    )
    write_csv(
        "orders.csv",
        orders,
        [
            "id", "customer_id", "order_date", "status", "channel",
            "payment_method", "shipping_region", "total_amount", "discount_amount",
        ],
    )
    write_csv(
        "order_items.csv",
        order_items,
        ["id", "order_id", "product_id", "quantity", "unit_price", "line_amount"],
    )
    write_csv(
        "payments.csv", payments, ["id", "order_id", "method", "status", "amount", "paid_at"]
    )
    write_csv(
        "refunds.csv",
        refunds,
        ["id", "order_id", "order_item_id", "amount", "reason", "refunded_at"],
    )

    truth = compute_ground_truth(orders, order_items, refunds, products, customers)
    (OUTPUT_DIR / "ground_truth.json").write_text(json.dumps(truth, indent=2), encoding="utf-8")

    pattern_1 = truth["pattern_1_june_revenue_drop"]
    print(f"\n  {len(orders):,} orders · {len(order_items):,} items · {len(refunds):,} refunds")
    print(f"\n  Pattern 1 — June revenue {pattern_1['pct_change']:+.1f}% vs May")
    for region, values in pattern_1["by_region"].items():
        print(f"      {region:<6} {values['pct_change']:+7.1f}%   "
              f"({values['share_of_total_change']:+.0f}% of the decline)")
    print(f"    worst region: {pattern_1['worst_region']} → "
          f"worst category there: {pattern_1['worst_category_in_south']} "
          f"({pattern_1['south_by_category'][pattern_1['worst_category_in_south']]['pct_change']:+.1f}%)")

    rates = truth["pattern_2_refund_spike"]["refund_rate_pct_by_month"]
    print(f"\n  Pattern 2 — {SPIKE_CATEGORY} refund rate by month")
    print("      " + "  ".join(f"{m[-2:]}:{v:.1f}%" for m, v in list(rates.items())[-6:]))

    pattern_3 = truth["pattern_3_incomplete_orders"]
    print(f"\n  Pattern 3 — {pattern_3['non_success_pct']:.1f}% of orders never completed")
    print(f"      correct revenue   ₹{pattern_3['correct_total_revenue']:,.0f}")
    print(f"      if status ignored ₹{pattern_3['revenue_if_status_ignored']:,.0f}  ← the trap")
    print(f"\n  Written to {OUTPUT_DIR}")


if __name__ == "__main__":
    main()
