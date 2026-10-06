# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Request/response helpers shared by the Claude-serving providers (Anthropic, Bedrock)."""

from __future__ import annotations

import re
from collections.abc import Iterable
from typing import Any

# Claude families that still accept sampling parameters (temperature/top_p/top_k):
# 3.x and 4.0-4.6 (incl. Haiku 4.5). Opus 4.7+, Sonnet 5+, Opus 5+ and Fable return
# HTTP 400 for them. An allowlist, so models released later default to omitting them.
# Matches first-party IDs ("claude-sonnet-4-6") and Bedrock IDs
# ("us.anthropic.claude-sonnet-4-5-20250929-v1:0").
_SAMPLING_MODELS = re.compile(
    r"claude-(?:3[-.]|(?:opus|sonnet|haiku)-4(?:-[0156])?(?=$|-\d{8}|-v\d|[:@]))"
)


def accepts_sampling_params(model: str) -> bool:
    return bool(_SAMPLING_MODELS.search(model))


def joined_text(blocks: Iterable[Any]) -> str:
    """Concatenate the text blocks of a Messages API response.

    The first block is not necessarily text: models with thinking on lead with a
    ``thinking`` block. Accepts SDK block objects or raw JSON dicts (Bedrock).
    """
    parts = []
    for b in blocks:
        if isinstance(b, dict):
            if b.get("type", "text") == "text":
                parts.append(b.get("text", ""))
        elif getattr(b, "type", None) == "text":
            parts.append(b.text)
    return "".join(parts)
