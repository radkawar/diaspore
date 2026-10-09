import json

orders = [
    {"item": "Notebook", "price": 8, "quantity": 3},
    {"item": "Pencil", "price": 2, "quantity": 12},
    {"item": "Ruler", "price": 5, "quantity": 2},
]

receipt = {
    order["item"]: order["price"] * order["quantity"]
    for order in orders
}

for item, total in receipt.items():
    print(f"{item:<12} {total:>3}")
print(f"{'Total':<12} {sum(receipt.values()):>3}")
print()
print(json.dumps(receipt, sort_keys=True))
