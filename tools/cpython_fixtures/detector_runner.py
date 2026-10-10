"""Emit explicit test outcomes without treating an imported module as a pass.

This is detector infrastructure, not a replacement unittest implementation.
Native unittest suites run themselves when available. The restricted legacy
shim path uses the CPython-discovered inventory and reports unsupported test
objects as errors rather than silently dropping them.
"""

import json
import unittest

_token = ""


def event(kind, **fields):
    """Emit one token-prefixed detector event."""
    fields["kind"] = kind
    print(_token + json.dumps(fields))


def stage(name):
    """Record the detector execution stage."""
    event("stage", stage=name)


def _unsupported_assertion(*args, **kwargs):
    raise NotImplementedError("detector refuses an assertion that omits part of its contract")


def configure(token, guards):
    """Set the event token and reject known incomplete assertion implementations."""
    global _token
    _token = token
    for name in guards:
        setattr(unittest.TestCase, name, _unsupported_assertion)


def import_error(error):
    """Record an import skip or genuine import-stage failure."""
    if isinstance(error, unittest.SkipTest):
        event("module_skip", reason=str(error))
    else:
        event("stage_error", stage="import", reason=f"{type(error).__name__}: {error}")


def _outcome(index, ident, outcome, error=""):
    event("outcome", index=index, id=ident, outcome=outcome, reason=str(error))


def _resolve(modules, item):
    cls = modules[item["module"]]
    for part in item["class"].split("."):
        cls = getattr(cls, part)
    return cls


def _fixture(obj, name):
    method = getattr(obj, name, None)
    if method is not None:
        method()


def _legacy_run(modules, inventory):
    current_class = None
    current_module = None
    blocked = None
    module_blocked = None
    for item in inventory:
        index = item["index"]
        ident = item["id"]
        event("start", index=index, id=ident)
        outcome = "passed"
        reason = ""
        try:
            if not item["standard_case"] or item["method"] is None:
                raise NotImplementedError(
                    "test object cannot be reconstructed with the legacy unittest shim"
                )
            cls = _resolve(modules, item)
            module = modules[item["module"]]
            if cls is not current_class:
                if current_class is not None and blocked is None:
                    _fixture(current_class, "tearDownClass")
                    _fixture(current_class, "doClassCleanups")
                current_class = None
                blocked = None
                if module is not current_module:
                    if current_module is not None and module_blocked is None:
                        _fixture(current_module, "tearDownModule")
                    current_module = module
                    module_blocked = None
                    try:
                        _fixture(module, "setUpModule")
                    except BaseException as error:
                        module_blocked = error
                current_class = cls
                if module_blocked is not None:
                    blocked = module_blocked
                elif getattr(cls, "__unittest_skip__", False):
                    blocked = unittest.SkipTest(
                        getattr(cls, "__unittest_skip_why__", "class skipped")
                    )
                else:
                    try:
                        _fixture(cls, "setUpClass")
                    except BaseException as error:
                        blocked = error
            if blocked is not None:
                raise blocked
            instance = cls(item["method"])
            method = getattr(instance, item["method"])
            if getattr(method, "__unittest_skip__", False):
                raise unittest.SkipTest(getattr(method, "__unittest_skip_why__", "method skipped"))
            expecting = getattr(method, "__unittest_expecting_failure__", False)
            instance.setUp()
            try:
                try:
                    method()
                except unittest.SkipTest:
                    raise
                except BaseException:
                    if not expecting:
                        raise
                    outcome = "expected_failure"
                else:
                    if expecting:
                        outcome = "unexpected_success"
            finally:
                instance.tearDown()
                _fixture(instance, "doCleanups")
        except unittest.SkipTest as error:
            outcome = "skipped"
            reason = str(error)
        except AssertionError as error:
            outcome = "failed"
            reason = str(error)
        except BaseException as error:
            outcome = "errored"
            reason = f"{type(error).__name__}: {error}"
        _outcome(index, ident, outcome, reason)
    if current_class is not None and blocked is None:
        _fixture(current_class, "tearDownClass")
        _fixture(current_class, "doClassCleanups")
    if current_module is not None and module_blocked is None:
        _fixture(current_module, "tearDownModule")


def _native_run(module):
    class Result(unittest.TestResult):
        def __init__(self):
            super().__init__()
            self.index = -1
            self.active = None

        def startTest(self, test):
            super().startTest(test)
            self.index += 1
            self.active = test
            event("start", index=self.index, id=test.id())

        def stopTest(self, test):
            super().stopTest(test)
            self.active = None

        def record(self, test, outcome, reason=""):
            index = self.index if test is self.active else -1
            _outcome(index, str(test), outcome, reason)

        def addSuccess(self, test):
            super().addSuccess(test)
            self.record(test, "passed")

        def addFailure(self, test, error):
            super().addFailure(test, error)
            self.record(test, "failed", self._exc_info_to_string(error, test))

        def addError(self, test, error):
            super().addError(test, error)
            self.record(test, "errored", self._exc_info_to_string(error, test))

        def addSkip(self, test, reason):
            super().addSkip(test, reason)
            self.record(test, "skipped", reason)

        def addExpectedFailure(self, test, error):
            super().addExpectedFailure(test, error)
            self.record(test, "expected_failure", self._exc_info_to_string(error, test))

        def addUnexpectedSuccess(self, test):
            super().addUnexpectedSuccess(test)
            self.record(test, "unexpected_success")

        def addSubTest(self, test, subtest, error):
            super().addSubTest(test, subtest, error)
            if error is not None:
                outcome = "failed" if issubclass(error[0], test.failureException) else "errored"
                self.record(test, outcome, self._exc_info_to_string(error, test))

    loader = unittest.TestLoader()
    suite = loader.loadTestsFromModule(module)
    if loader.errors:
        raise RuntimeError(str(loader.errors))
    suite.run(Result())


def run(modules, inventory, target):
    """Execute discovered test cases and report unsupported or failed execution."""
    stage("discovery")
    if inventory is None:
        event("stage_error", stage="discovery", reason="CPython did not provide a test inventory")
        return
    stage("tests")
    try:
        if hasattr(unittest, "TestLoader"):
            _native_run(modules[target])
        else:
            _legacy_run(modules, inventory)
        event("complete")
    except BaseException as error:
        event("stage_error", stage="tests", reason=f"{type(error).__name__}: {error}")
