# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

from core.exceptions import describe_exception


def test_plain_exception() -> None:
    assert describe_exception(ValueError("bad")) == "ValueError: bad"


def test_exception_group_names_its_leaf_cause() -> None:
    """anyio's 'unhandled errors in a TaskGroup (1 sub-exception)' hid a DNS failure."""
    inner = ExceptionGroup("inner", [ConnectionError("Name or service not known")])
    outer = ExceptionGroup("unhandled errors in a TaskGroup", [inner])
    assert describe_exception(outer) == "ConnectionError: Name or service not known"
