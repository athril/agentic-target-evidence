# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Token-budget-aware batch packing for LLM calls over evidence lists.

Evidence text length varies a lot by source (PubMed abstracts vs. patent/
clinical-trial/FDA text), so fixed item-count batches risk overflowing the
model's context window when a few oversized documents land in the same
batch. This packs items by running token estimate instead, with an item-count
ceiling as a secondary bound.
"""

from __future__ import annotations

from collections.abc import Callable


def estimate_tokens(text: str) -> int:
    """Rough token-count heuristic (~4 chars/token for English).

    No tokenizer dependency — only needs to be good enough to keep batches
    under the model's num_ctx, with callers leaving their own safety margin.
    """
    return max(1, len(text) // 4)


def pack_batches[T](
    items: list[T],
    text_fn: Callable[[T], str],
    max_tokens: int,
    max_items: int,
) -> list[list[T]]:
    """Greedily pack items into batches bounded by both estimated input
    tokens (via text_fn) and item count, so one oversized item can't blow
    the context window for the whole batch."""
    batches: list[list[T]] = []
    current: list[T] = []
    current_tokens = 0
    for item in items:
        item_tokens = estimate_tokens(text_fn(item))
        if current and (current_tokens + item_tokens > max_tokens or len(current) >= max_items):
            batches.append(current)
            current = []
            current_tokens = 0
        current.append(item)
        current_tokens += item_tokens
    if current:
        batches.append(current)
    return batches
