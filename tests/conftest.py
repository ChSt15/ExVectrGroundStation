"""Test selection helpers.

Tests that drive a real headless Chromium are slow and the only flaky ones (temp-dir cleanup, port reuse under load).
They are marked `browser` automatically, so everyday runs can skip them:

    pytest -m "not browser"          # fast: unit and integration tests without a browser
    pytest -m browser                # only the browser tests
    pytest                           # everything (before a merge)

`tools/test_relevant.py` picks the test files that relate to the files you changed.
"""

from pathlib import Path

import pytest

_BROWSER_MODULES = {"browser_check"}


def pytest_collection_modifyitems(config, items):
    cache: dict[str, bool] = {}
    for item in items:
        path = str(item.fspath)
        if path not in cache:
            try:
                text = Path(path).read_text()
            except OSError:
                text = ""
            cache[path] = any(m in text for m in _BROWSER_MODULES)
        if cache[path]:
            item.add_marker(pytest.mark.browser)
