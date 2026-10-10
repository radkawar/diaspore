"""Provide GC compatibility helpers without controlling VM memory management."""


def collect(generation=2):
    """Return zero; host memory collection is not exposed to sandbox code."""
    return 0


def disable():
    """Leave host-managed collection enabled."""
    pass


def enable():
    """Leave host-managed collection enabled."""
    pass


def isenabled():
    """Return True because host memory management remains enabled."""
    return True


def get_objects():
    """Return an empty list without exposing host-managed objects."""
    return []


def get_referrers(*args):
    """Return an empty list without exposing host object references."""
    return []


def get_referents(*args):
    """Return an empty list without exposing host object references."""
    return []


def get_count():
    """Return zero counters because host allocation counts are unavailable."""
    return (0, 0, 0)


def freeze():
    """Leave host-managed objects outside sandbox control."""
    pass


def set_debug(flags):
    """Accept debug flags without changing host collector diagnostics."""
    pass
