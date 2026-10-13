# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import httpx

from core.exceptions import describe_exception


def test_describe_exception_names_the_type_when_the_message_is_empty() -> None:
    assert describe_exception(httpx.ReadTimeout("")) == "ReadTimeout"
    assert describe_exception(TimeoutError()) == "TimeoutError"
    assert describe_exception(ValueError("bad input")) == "ValueError: bad input"
