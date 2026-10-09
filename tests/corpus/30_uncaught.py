print("about to fail")


def level_two(payload):
    return payload["missing_key"]


def level_one(payload):
    return level_two(payload)


print("still fine")
level_one({"present": 1})
print("never reached")
