try:
    import definitely_not_installed_anywhere
    print("impossible")
except ImportError as e:
    print("optional:", e)
except Exception:
    print("wrong handler")


def lazy_loader():
    try:
        import also_missing
        return also_missing
    except ModuleNotFoundError as e:
        return f"lazy miss: {e}"


print(lazy_loader())
print("still alive")

try:
    import nor_this_one as alias
except ImportError:
    alias = "fallback"
print(alias)


print("done")
