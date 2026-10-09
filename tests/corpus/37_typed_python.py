"""A fully type-annotated module: diaspore strips annotations, drops typing-only
imports, and runs it byte-identically to CPython."""

from __future__ import annotations

from typing import Iterator

import json


class Inventory:
    def __init__(self, name: str) -> None:
        self.name: str = name
        self.items: dict[str, int] = {}

    def add(self, sku: str, qty: int = 1) -> int:
        self.items[sku] = self.items.get(sku, 0) + qty
        return self.items[sku]

    def take(self, sku: str, qty: int) -> bool:
        have: int = self.items.get(sku, 0)
        if have < qty:
            return False
        self.items[sku] = have - qty
        if self.items[sku] == 0:
            del self.items[sku]
        return True

    def skus(self) -> list[str]:
        return sorted(self.items)

    def stream(self) -> Iterator[tuple[str, int]]:
        for sku in self.skus():
            yield sku, self.items[sku]


def restock(inv: Inventory, orders: list[tuple[str, int]]) -> dict[str, int]:
    applied: dict[str, int] = {}
    for sku, qty in orders:
        applied[sku] = inv.add(sku, qty)
    return applied


inv = Inventory("warehouse-1")
print(restock(inv, [("bolt", 100), ("nut", 250), ("bolt", 50)]))
print(inv.skus(), inv.take("bolt", 120), inv.take("bolt", 999))
print(list(inv.stream()))

threshold: int = 40
low: list[str] = [sku for sku, n in inv.stream() if n < threshold]
print("low stock:", low)

report: str = json.dumps({"name": inv.name, "items": inv.items})
print(report)

annotated_only: int
try:
    print(annotated_only)
except NameError:
    print("annotation alone binds nothing")
