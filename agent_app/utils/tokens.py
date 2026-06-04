from __future__ import annotations


def estimate_tokens(value: str) -> int:
    # LM Studio raw logs for tool-heavy JSON conversations trend close to
    # two characters per token. Use the conservative estimate so context
    # pruning starts before the model is already out of headroom.
    return max(1, len(value) // 2)
