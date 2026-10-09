"""Provide GC compatibility helpers without controlling VM memory management."""
def collect(generation=2): return 0
def disable(): pass
def enable(): pass
def isenabled(): return True
def get_objects(): return []
def get_referrers(*args): return []
def get_referents(*args): return []
def get_count(): return (0, 0, 0)
def freeze(): pass
def set_debug(flags): pass
