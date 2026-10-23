"""上下文共享的保守估算口径；真实计费用量仍以供应商 usage 为准。"""


def estimate_text_tokens(text: str) -> int:
    cn_chars = sum(1 for c in text if '一' <= c <= '鿿')
    return int(cn_chars * 1.5 + (len(text) - cn_chars) * 0.4)
