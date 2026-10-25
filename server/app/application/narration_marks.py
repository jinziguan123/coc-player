"""旁白改写后的交错位置变换；不读取模型、存档或回合上下文。"""

def snap_offset(text: str, off: int) -> int:
    """把偏移吸附到最近的句末/换行边界，避免在句子中间插入对话气泡（就近向后、再向前找）。"""
    n = len(text)
    off = max(0, min(off, n))
    if off <= 0 or off >= n:
        return off
    for i in range(off, min(off + 40, n)):
        if text[i] in "。！？…\n":
            return i + 1
    for i in range(off, max(off - 40, 0), -1):
        if text[i] in "。！？…\n":
            return i + 1
    return off


def remap_marks_after_rewrite(
    result: list, old_narr: str, event_order: list | None = None,
) -> None:
    """旁白被校验改写后，把 result[3]（对话交错偏移）/result[4]（分组偏移）及 event_order
    （tool-loop 事件的广播偏移）按长度比例重映射到新文本并吸附到句界——**保住交错顺序**，
    气泡/工具事件仍插在对应旁白之后，而非全部堆到末尾。"""
    new_narr = result[0]
    old_len = len(old_narr)
    if old_len <= 0:
        if len(result) > 3:
            if hasattr(result, "dialogue_marks"):
                result.dialogue_marks.clear()
                result.group_marks.clear()
            else:
                del result[3:]
        if event_order is not None:
            event_order[:] = [(0, eid) for _o, eid in event_order]
        return
    scale = len(new_narr) / old_len

    def _remap(off: int) -> int:
        return snap_offset(new_narr, int(round(off * scale)))

    if len(result) > 3 and result[3]:
        result[3] = [(_remap(o), spk, txt) for (o, spk, txt) in result[3]]
    if len(result) > 4 and result[4]:
        result[4] = [(_remap(o), label) for (o, label) in result[4]]
    if event_order:
        event_order[:] = [(_remap(o), eid) for (o, eid) in event_order]


