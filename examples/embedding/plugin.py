"""A pricing policy with access only to the host's customer lookup bindings."""

import host

TIERS = {"free": 0.0, "pro": 0.15, "enterprise": 0.25}


def price(order):
    base = sum(item["qty"] * item["unit"] for item in order["items"])
    tier = host.customer_tier(order["customer"])
    discount = TIERS.get(tier, 0.0)
    total = round(base * (1 - discount), 2)
    if total > host.credit_limit(order["customer"]):
        raise ValueError(f"order exceeds credit limit for {order['customer']}")
    return total


def risk_score(order):
    score = 0
    score += min(len(order["items"]) * 5, 30)
    if order.get("rush"):
        score += 20
    if host.customer_tier(order["customer"]) == "free":
        score += 25
    return min(score, 100)
