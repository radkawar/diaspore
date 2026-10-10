"""Warning assertions cannot be replaced by silent context managers."""


def check_warnings(*args, **kwargs):
    """Reject warning assertions that cannot capture VM warning records."""
    raise NotImplementedError("detector cannot capture and assert VM warning records")


def check_no_resource_warning(*args, **kwargs):
    """Reject assertions that cannot capture VM resource warnings."""
    raise NotImplementedError("detector cannot capture and assert VM resource warnings")


def ignore_warnings(*args, **kwargs):
    """Reject requests to change unavailable VM warning filters."""
    raise NotImplementedError("detector cannot control VM warning filters")
