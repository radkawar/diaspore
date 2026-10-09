counter = 0


def bump():
    global counter
    counter += 1
    return counter


print(bump(), bump(), counter)


def shadow_test():
    len = "shadowed"
    return len


print(shadow_test(), len("still works"))


def outer():
    x = 1

    def middle():
        x = 2

        def inner():
            nonlocal x
            x = 3
            return x
        inner()
        return x
    return middle(), x


print(outer())


def unbound_demo():
    try:
        print(v)
    except UnboundLocalError as e:
        print("unbound ok")
    v = 5
    return v


print(unbound_demo())

try:
    print(missing_name)
except NameError as e:
    print("name error:", e)


def late_global():
    return defined_later


defined_later = "here now"
print(late_global())

x = "module"


def reads_module():
    return x


def writes_local():
    x = "local"
    return x


print(reads_module(), writes_local(), x)

tmp = "delete me"
del tmp
try:
    print(tmp)
except NameError:
    print("deleted")


def nested_global():
    def setter():
        global deep_value
        deep_value = 99
    setter()


nested_global()
print(deep_value)

items = []


def no_global_needed():
    items.append(len(items))


no_global_needed()
no_global_needed()
print(items)
