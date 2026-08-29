"""生成 DESIGN.md 里的架构图（SVG），输出到 docs/images/。

    python3 scripts/gen_design_diagrams.py

**为什么用脚本生成而不是手写 SVG**：这几张图有几百个坐标，手写必然出现框重叠、文字出界、
改一处要顺着挪一片。这里把「排版」交给代码——行内等距分列、盒子按最长文本估宽、
容器按内容自动收边——内容改动只需要改文案，坐标自己算。

**为什么不用 mermaid**：mermaid 的布局不可控（`direction` 在被外部边连接的子图里会被忽略、
链式边会把整图拉成一条竖线），而且渲染依赖阅读器支持。SVG 文件在 GitHub、编辑器预览、
离线打开都是同一个样子。

配色跟随系统深浅色（``prefers-color-scheme``），并显式画出底板——即便阅读器没传深色偏好，
图也只是「浅色卡片」，不会变成黑底黑字。
"""

from __future__ import annotations

import html
from pathlib import Path

OUT = Path(__file__).resolve().parent.parent / "docs" / "images"

# 中文按等宽估算：14px 字号约 14px/字，12px 约 12.5px/字；ASCII 约为其一半。
CJK_W14, CJK_W12 = 14.0, 12.5


def text_width(s: str, size: int = 14) -> float:
    per = CJK_W14 if size >= 14 else CJK_W12
    total = 0.0
    for ch in s:
        total += per if ord(ch) > 0x2E80 else per * 0.52
    return total


STYLE = """<style>
svg{--bg:#FFFFFF;--card:#F7F6F2;--line:#B4B2A9;--txt:#2C2C2A;--txt2:#5F5E5A;
--pu-bg:#EEEDFE;--pu-ln:#7F77DD;--pu-tx:#26215C;
--te-bg:#E1F5EE;--te-ln:#1D9E75;--te-tx:#04342C;
--co-bg:#FAECE7;--co-ln:#D85A30;--co-tx:#4A1B0C;
--gy-bg:#F1EFE8;--gy-ln:#888780;--gy-tx:#2C2C2A}
@media (prefers-color-scheme:dark){svg{--bg:#1A1A18;--card:#232320;--line:#5F5E5A;--txt:#F1EFE8;--txt2:#B4B2A9;
--pu-bg:#3C3489;--pu-ln:#AFA9EC;--pu-tx:#EEEDFE;
--te-bg:#085041;--te-ln:#5DCAA5;--te-tx:#E1F5EE;
--co-bg:#712B13;--co-ln:#F0997B;--co-tx:#FAECE7;
--gy-bg:#444441;--gy-ln:#B4B2A9;--gy-tx:#F1EFE8}}
text{font-family:-apple-system,BlinkMacSystemFont,"PingFang SC","Microsoft YaHei",Helvetica,Arial,sans-serif}
.t{font-size:14px;fill:var(--txt)}
.th{font-size:14px;font-weight:500;fill:var(--txt)}
.ts{font-size:12px;fill:var(--txt2)}
.ln{stroke:var(--line);stroke-width:1;fill:none}
.dash{stroke:var(--line);stroke-width:1;fill:none;stroke-dasharray:5 4}
</style>"""

MARKER = (
    '<defs><marker id="a" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="6" markerHeight="6" '
    'orient="auto-start-reverse"><path d="M2 1L8 5L2 9" fill="none" stroke="context-stroke" '
    'stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/></marker></defs>'
)

RAMP = {"pu": "pu", "te": "te", "co": "co", "gy": "gy"}


def esc(s: str) -> str:
    return html.escape(s, quote=False)


class Svg:
    def __init__(self, w: int, h: int, title: str, desc: str):
        self.w, self.h = w, h
        self.parts: list[str] = []
        self.title, self.desc = title, desc

    def box(self, x, y, w, h, title, subs=(), ramp="gy", rx=6):
        c = RAMP[ramp]
        self.parts.append(
            f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="{rx}" '
            f'fill="var(--{c}-bg)" stroke="var(--{c}-ln)" stroke-width="1"/>'
        )
        cx = x + w / 2
        lines = [(title, "th")] + [(s, "ts") for s in subs]
        block = 19 * len(lines)
        top = y + (h - block) / 2 + 14
        for i, (s, cls) in enumerate(lines):
            fill = f'fill="var(--{c}-tx)"' if cls == "th" else f'fill="var(--{c}-tx)" opacity="0.78"'
            self.parts.append(
                f'<text class="{cls}" x="{cx:.0f}" y="{top + i * 19:.0f}" '
                f'text-anchor="middle" {fill}>{esc(s)}</text>'
            )

    def container(self, x, y, w, h, label):
        self.parts.append(
            f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="10" class="dash"/>'
        )
        self.parts.append(
            f'<text class="ts" x="{x + 14}" y="{y + 20}">{esc(label)}</text>'
        )

    def arrow(self, x1, y1, x2, y2, dashed=False):
        cls = "dash" if dashed else "ln"
        self.parts.append(
            f'<line class="{cls}" x1="{x1:.0f}" y1="{y1:.0f}" x2="{x2:.0f}" y2="{y2:.0f}" '
            f'marker-end="url(#a)"/>'
        )

    def biarrow(self, x1, y1, x2, y2):
        self.parts.append(
            f'<line class="dash" x1="{x1:.0f}" y1="{y1:.0f}" x2="{x2:.0f}" y2="{y2:.0f}" '
            f'marker-start="url(#a)" marker-end="url(#a)"/>'
        )

    def elbow(self, x1, y1, x2, y2, midx):
        """先横到 midx 再竖下去。midx 必须显式给——默认取中点几乎总会穿过别的盒子。"""
        self.parts.append(
            f'<path class="ln" d="M{x1:.0f} {y1:.0f} H{midx:.0f} V{y2:.0f} H{x2:.0f}" '
            f'marker-end="url(#a)"/>'
        )

    def fan(self, x1, y1, x2, y2, midy):
        """先竖到 midy 再横再竖：一对多扇出时用它，横段走在盒子之间的空档里。"""
        self.parts.append(
            f'<path class="ln" d="M{x1:.0f} {y1:.0f} V{midy:.0f} H{x2:.0f} V{y2:.0f}" '
            f'marker-end="url(#a)"/>'
        )

    def label(self, x, y, s, anchor="start", cls="ts"):
        self.parts.append(
            f'<text class="{cls}" x="{x:.0f}" y="{y:.0f}" text-anchor="{anchor}">{esc(s)}</text>'
        )

    def render(self) -> str:
        return (
            f'<svg xmlns="http://www.w3.org/2000/svg" width="{self.w}" height="{self.h}" '
            f'viewBox="0 0 {self.w} {self.h}" role="img">'
            f"<title>{esc(self.title)}</title><desc>{esc(self.desc)}</desc>"
            f"{STYLE}{MARKER}"
            f'<rect width="{self.w}" height="{self.h}" rx="12" fill="var(--bg)"/>'
            + "".join(self.parts)
            + "</svg>\n"
        )


def spread(x0: int, x1: int, n: int, gap: int) -> tuple[int, list[int]]:
    """在 [x0, x1] 内等距排 n 个同宽盒子，返回 (宽度, 各自左边界)。"""
    w = (x1 - x0 - gap * (n - 1)) // n
    return w, [x0 + i * (w + gap) for i in range(n)]


# ── 图 1：运行形态与部署拓扑 ────────────────────────────────────────────────

def deployment() -> tuple[str, str]:
    s = Svg(1180, 560, "运行形态与部署拓扑",
            "客人机器的 SPA 经 Tauri 外壳里的 iroh 端点，通过 QUIC 隧道连到房主机器；"
            "房主机器上 FastAPI 同源托管前端，领域服务下接 SQLite、事件日志、向量检索、"
            "规则引擎与 LLM Provider，实时事件经 RoomHub 走 SSE。")

    s.container(24, 40, 236, 260, "客人机器")
    s.box(48, 96, 188, 56, "React SPA", ("客人自己的前端",), "pu")
    s.box(48, 216, 188, 60, "iroh Endpoint", ("Tauri 外壳",), "gy")
    s.arrow(142, 152, 142, 216)

    s.label(292, 236, "QUIC 直连", "middle")
    s.label(292, 254, "打不通走 relay", "middle")
    s.biarrow(240, 210, 344, 210)

    s.container(324, 40, 832, 480, "房主机器")
    s.box(352, 76, 200, 56, "React SPA", ("同源托管",), "pu")
    s.box(352, 216, 200, 60, "iroh Endpoint + 反代", ("Tauri 外壳",), "gy")
    s.box(352, 320, 200, 56, "Tauri 外壳", ("spawn sidecar",), "gy")
    s.box(624, 216, 176, 56, "FastAPI", ("REST /api · SSE /live",), "te")
    s.box(880, 76, 248, 60, "RoomHub", ("进程内广播 → SSE",), "te")
    s.box(872, 216, 256, 56, "领域服务", ("session / turn / combat …",), "te")

    s.label(566, 90, "SSE /live")
    s.arrow(552, 94, 880, 94)                 # SPA → RoomHub
    s.label(596, 150, "REST /api")
    s.elbow(552, 118, 624, 232, midx=588)     # SPA → FastAPI（绕开 iroh 盒子右侧）
    s.arrow(552, 244, 624, 244)               # iroh 反代 → FastAPI
    s.elbow(552, 348, 624, 258, midx=588)     # Tauri 外壳 → FastAPI
    s.arrow(800, 244, 872, 244)               # FastAPI → 领域服务
    s.arrow(1000, 216, 1000, 140)             # 领域服务 → RoomHub

    w, xs = spread(352, 1128, 5, 20)
    backs = [
        ("SQLite", ("SQLAlchemy + Alembic",)),
        ("event_logs", ("事件日志与重放",)),
        ("向量检索", ("规则书 / 模组 / 事件",)),
        ("规则引擎", ("CoC 七版",)),
        ("LLM Provider", ("OpenAI 兼容 / Anthropic",)),
    ]
    for x, (t, sub) in zip(xs, backs):
        s.box(x, 424, w, 60, t, sub, "gy")
        s.fan(1000, 272, x + w / 2, 424, 392)

    return "deployment-topology.svg", s.render()


# ── 图 2：回合主链路（时序）────────────────────────────────────────────────

def turn_pipeline() -> tuple[str, str]:
    cols = [
        ("前端", "GameSessionPage"),
        ("API", "api/chat.py"),
        ("会话域", "session_service"),
        ("生成锁", "GenerationManager"),
        ("编排", "turn_orchestrator"),
        ("规划器", "turn_planner"),
        ("队友", "team_turn_service"),
        ("KP", "KPAgent + 工具循环"),
        ("规则/服务", "确定性执行"),
        ("实时", "RoomHub → SSE"),
    ]
    msgs = [
        (0, 1, "POST /chat（行动 / 台词 / OOC）"),
        (1, 2, "写 pending_turn 事件"),
        (1, 9, "广播事件与 turn_state"),
        (0, 1, "POST /advance"),
        (1, 2, "turn_confirm 齐 → commit_turn"),
        (1, 3, "start(run_chat_generation)"),
        (3, 4, "并发锁 + 房间配额 + in-flight buffer"),
        (4, 5, "① TurnPlan（快模型，温度 0，JSON）"),
        (5, 4, "裁定：检定 / 线索 / NPC / 场景 / 开战"),
        (4, 6, "② AI 队友回合（主模型）"),
        (6, 4, "队友行动与台词（暗骰不落库）"),
        (4, 5, "③ 二次 TurnPlan（仅当前提被改变）"),
        (4, 7, "④ 注入计划 + KP 上下文，流式叙事"),
        (7, 8, "工具调用（掷骰 / SAN / 开战 / 切场景）"),
        (8, 7, "确定性结果回注，或 suspend 等真人投骰"),
        (4, 4, "⑤ TurnValidator 落库前安检（预筛命中才调）"),
        (4, 8, "计划状态守卫：确保开战 / 伤害落地"),
        (4, 2, "持久化事件（唯一序号）"),
        (4, 9, "广播 token / 离散事件 / done"),
        (4, 4, "后台收尾：滚动摘要、幕后推演、配图"),
    ]
    colw, gap, left, top = 152, 14, 28, 40
    head_h, first, step = 58, 132, 40
    width = left * 2 + len(cols) * colw + (len(cols) - 1) * gap
    height = first + step * len(msgs) + 40
    s = Svg(width, height, "一轮对话的主链路时序",
            "玩家发言经 API 暂存并广播，确认齐后由 GenerationManager 唯一入口进入编排："
            "规划器、AI 队友、二次规划、KP 叙事与工具循环、校验器，最后确定性落库与广播。")

    centers = []
    for i, (name, sub) in enumerate(cols):
        x = left + i * (colw + gap)
        cx = x + colw / 2
        centers.append(cx)
        ramp = "te" if i in (5, 6, 7) else "gy"
        s.box(x, top, colw, head_h, name, (sub,), ramp)
        s.parts.append(
            f'<line class="dash" x1="{cx:.0f}" y1="{top + head_h}" x2="{cx:.0f}" '
            f'y2="{height - 28}"/>'
        )

    for i, (a, b, text) in enumerate(msgs):
        y = first + i * step
        if a == b:
            cx = centers[a]
            s.parts.append(
                f'<path class="ln" d="M{cx:.0f} {y - 8:.0f} h34 v18 h-34" '
                f'marker-end="url(#a)"/>'
            )
            s.label(cx + 44, y + 3, text)
        else:
            x1, x2 = centers[a], centers[b]
            d = 7 if x2 > x1 else -7
            s.arrow(x1 + d, y, x2 - d, y)
            mid = (x1 + x2) / 2
            s.label(mid, y - 8, text, "middle")

    return "turn-pipeline.svg", s.render()


# ── 图 3：四个阶段与两条纪律线 ────────────────────────────────────────────

def turn_phases() -> tuple[str, str]:
    s = Svg(1180, 692, "一轮生成的四个阶段",
            "回合确认与落库守卫是确定性的 fail-closed 段，中间五个串行 LLM 环节是玩家等待的"
            "来源，最后的后台收尾 fail-open、三者彼此独立。")

    s.container(24, 36, 1132, 116, "一、回合确认（确定性）")
    w, xs = spread(48, 1132, 3, 40)
    p1 = [
        ("POST /chat", ("暂存本轮发言",)),
        ("POST /advance", ("需确认的真人都点过",)),
        ("commit_turn", ("暂存转正，触发生成",)),
    ]
    for x, (t, sub) in zip(xs, p1):
        s.box(x, 72, w, 62, t, sub, "gy")
    for i in range(2):
        s.arrow(xs[i] + w, 103, xs[i + 1], 103)

    s.box(370, 184, 440, 62, "GenerationManager.start",
          ("全应用唯一生成入口：并发锁 + 房间配额",), "gy")
    s.arrow(590, 152, 590, 184)

    s.container(24, 278, 1132, 122, "二、串行 LLM 环节（玩家等待的来源；紫＝快模型，绿＝主模型）")
    w2, xs2 = spread(48, 1132, 5, 22)
    p2 = [
        ("① TurnPlan", ("快模型 · 温度 0",), "pu"),
        ("② AI 队友回合", ("主模型 · 玩家直接看到",), "te"),
        ("③ 二次 TurnPlan", ("快模型 · 仅前提被改变",), "pu"),
        ("④ KP 叙事", ("主模型 · 工具循环",), "te"),
        ("⑤ TurnValidator", ("快模型 · 预筛命中才调",), "pu"),
    ]
    for x, (t, sub, ramp) in zip(xs2, p2):
        s.box(x, 312, w2, 66, t, sub, ramp)
    for i in range(4):
        s.arrow(xs2[i] + w2, 345, xs2[i + 1], 345)
    s.arrow(590, 246, 590, 278)

    s.container(24, 426, 1132, 116, "三、落库与守卫（fail-closed：必须成功）")
    w3, xs3 = spread(48, 1132, 3, 40)
    p3 = [
        ("状态守卫", ("确保已裁定的落地",)),
        ("唯一序号落库", ("同事务内分配",)),
        ("广播 done", ("前端收束本轮",)),
    ]
    for x, (t, sub) in zip(xs3, p3):
        s.box(x, 462, w3, 62, t, sub, "gy")
    for i in range(2):
        s.arrow(xs3[i] + w3, 493, xs3[i + 1], 493)
    s.arrow(590, 400, 590, 426)

    s.container(24, 566, 1132, 92, "四、后台收尾（fail-open：失败即退化；三者彼此独立、无先后）")
    w4, xs4 = spread(48, 1132, 3, 40)
    p4 = [
        ("滚动摘要", ("推进浓缩游标",)),
        ("幕后推演", ("只落 KP 可见事件",)),
        ("场景配图", ("回写 scene.image",)),
    ]
    for x, (t, sub) in zip(xs4, p4):
        s.box(x, 600, w4, 44, t, sub, "co")
    s.arrow(590, 542, 590, 566)

    return "turn-phases.svg", s.render()


# ── 图 4：一次请求的上下文注入规则 ────────────────────────────────────────

def context_injection() -> tuple[str, str]:
    s = Svg(1180, 560, "一次请求的上下文注入规则",
            "总预算由模型窗口推导并经在线校准，切成系统提示、事件区与输出预留；"
            "系统提示内部按缓存稳定性分静态、半静态、易变三档，事件区以滚动摘要游标为界。")

    s.box(48, 48, 280, 62, "模型上下文窗口", ("× 0.6，clamp 48k–150k",), "gy")
    s.arrow(328, 79, 396, 79)
    s.box(396, 48, 240, 62, "基准预算", ("× budget_scale 在线校准",), "gy")
    s.arrow(636, 79, 704, 79)
    s.box(704, 48, 240, 62, "本轮总预算", (), "gy")

    w, xs = spread(48, 1132, 3, 32)
    tops = [
        ("系统提示", ("上限 30k",), "pu"),
        ("事件区", ("总预算 − 系统 − 预留",), "te"),
        ("输出预留", ("7k，恒定",), "gy"),
    ]
    for x, (t, sub, ramp) in zip(xs, tops):
        s.box(x, 168, w, 62, t, sub, ramp)
        s.fan(824, 110, x + w / 2, 168, 140)

    s.container(24, 262, 748, 156, "系统提示：顺序＝缓存稳定性，不是阅读顺序")
    w2, xs2 = spread(48, 748, 3, 20)
    tiers = [
        ("静态 · 9 节", ("整场逐字不变", "缓存命中的主体")),
        ("半静态 · 2 节", ("模组数据、幕后真相", "同一轮内稳定")),
        ("易变 · 13 节", ("位置、台账、记忆、战斗", "排最后不污染前缀")),
    ]
    for x, (t, subs) in zip(xs2, tiers):
        s.box(x, 300, w2, 96, t, subs, "pu")
        # 横段走 y=256，压在事件区扇出（y=246）之下，两束互不重叠
        s.fan(xs[0] + w / 2, 230, x + w2 / 2, 300, 256)

    s.container(800, 262, 356, 156, "事件区：以滚动摘要游标为界")
    w3, xs3 = spread(824, 1132, 2, 20)
    evs = [("游标之前", ("只给持久摘要",)), ("游标之后", ("按预算给全文", "至少 10 条"))]
    for x, (t, subs) in zip(xs3, evs):
        s.box(x, 300, w3, 96, t, subs, "te")
        s.fan(xs[1] + w / 2, 230, x + w3 / 2, 300, 246)

    s.container(24, 442, 1132, 96, "超预算时按 priority 从大到小整段丢弃（判据：丢了能不能捞回来）")
    w4, xs4 = spread(48, 1132, 4, 24)
    drops = [
        ("40 · 先丢", ("模组与规则摘录",)),
        ("35 → 10", ("临场、幕后、手书、记忆",)),
        ("6 → 2", ("台账、机制点、队伍位置",)),
        ("0 · 永不丢", ("身份、手册、模组、战斗态",)),
    ]
    for x, (t, sub) in zip(xs4, drops):
        s.box(x, 474, w4, 50, t, sub, "co")

    return "context-injection.svg", s.render()


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    for build in (deployment, turn_pipeline, turn_phases, context_injection):
        name, svg = build()
        (OUT / name).write_text(svg, "utf-8")
        print(f"已写入 docs/images/{name}（{len(svg)} 字节）")


if __name__ == "__main__":
    main()
