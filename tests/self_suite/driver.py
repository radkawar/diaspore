"""Run original test bodies with explicit fixtures and machine-readable outcomes."""

import json
import sys
from pathlib import Path

import pytest
import suite_case
from self_suite_manifest import TESTS

PREFIX = "SELF_SUITE_RESULT "


def function_for(class_name, function_name):
    owner = suite_case if not class_name else getattr(suite_case, class_name)
    return getattr(owner, function_name)


def catalog():
    result = {}
    for name, class_name, function_name, _fixtures in TESTS:
        function = function_for(class_name, function_name)
        result[name] = len(pytest.cases(function))
    return result


def run(selected, root):
    results = []
    found = False
    for name, class_name, function_name, fixtures in TESTS:
        if name != selected:
            continue
        found = True
        function = function_for(class_name, function_name)
        rows = pytest.cases(function)
        for index in range(len(rows)):
            arguments = dict(rows[index])
            if "tmp_path" in fixtures:
                arguments["tmp_path"] = Path(root) / str(index)
            try:
                if class_name:
                    instance = getattr(suite_case, class_name)()
                    getattr(instance, function_name)(**arguments)
                else:
                    function(**arguments)
            except BaseException as error:
                results.append(
                    {
                        "case": name,
                        "index": index,
                        "status": "fail",
                        "type": type(error).__name__,
                        "error": str(error),
                    }
                )
            else:
                results.append({"case": name, "index": index, "status": "pass"})
    if not found:
        raise ValueError("unknown self-suite case: " + selected)
    return results


if sys.argv[1] == "--list":
    print(PREFIX + json.dumps(catalog()))
else:
    print(PREFIX + json.dumps(run(sys.argv[1], sys.argv[2])))
