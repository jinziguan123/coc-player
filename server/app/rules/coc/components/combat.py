"""CoC 7th 战斗轮的**纯确定性**规则引擎（P1 地基，不接 LLM、不碰 DB/广播）。

只负责规则计算：先攻排序（火器优先）、武器伤害、攻击解析（近战对抗 / 远程检定 / 命中伤害 /
贯穿）、启发式 NPC 选择、回合推进、结束判定。状态机 / 上下文 / 落库 / 前端在 P2+ 另接。

复用现有原语：resolve_skill_check（达成等级/成败）、roll（骰池）、COC_WEAPONS（武器表）。
掷骰经 roll / roll_percentile，测试可 monkeypatch 钉死。
"""

from __future__ import annotations

import math
import random
import re

from app.rules.coc.components.check import CheckConfig, resolve_skill_check
from app.rules.kernel import Component, Provide

# ── 武器表（106 件），取自克苏鲁公社人物卡生成器 ──────────────────────────
# 字段：name 名称 / skill 使用技能 / dam 伤害 / tho 是否贯穿(0/1) /
# range 射程 / round 次数(a/b) / num 装弹量 / price 价格 / err 故障率 / time 时代 / category 大类。

WEAPON_CATEGORY_ORDER: list[str] = [
    "常规",
    "手枪",
    "半自动步枪",
    "全自动步枪",
    "霰弹枪",
    "冲锋枪",
    "狙击步枪",
    "机枪",
    "其他",
]

COC_WEAPONS: list[dict] = [
    {"name": "徒手格斗", "skill": "格斗(斗殴)", "dam": "1D3+DB", "tho": 0, "range": "接触", "round": "1", "num": "", "price": "", "err": "", "time": "1920s，现代", "category": "常规"},
    {"name": "弓箭", "skill": "射击(弓弩)", "dam": "1D6+半DB", "tho": 0, "range": "30", "round": "1", "num": "1", "price": "7/75", "err": "97", "time": "1920s，现代", "category": "常规"},
    {"name": "黄铜指虎", "skill": "格斗(斗殴)", "dam": "1D3+1+DB", "tho": 0, "range": "接触", "round": "1", "num": "", "price": "1/10", "err": "", "time": "1920s，现代", "category": "常规"},
    {"name": "长鞭", "skill": "格斗(鞭)", "dam": "1D3+半DB", "tho": 0, "range": "3", "round": "1", "num": "", "price": "5/50", "err": "97", "time": "1920s", "category": "常规"},
    {"name": "燃烧的火把", "skill": "格斗(斗殴)", "dam": "1D6+燃烧", "tho": 0, "range": "接触", "round": "1", "num": "", "price": "0.05/0.5", "err": "", "time": "1920s，现代", "category": "常规"},
    {"name": "链锯", "skill": "格斗(链锯)", "dam": "2D8", "tho": 1, "range": "接触", "round": "1", "num": "", "price": "-/300", "err": "95", "time": "现代", "category": "常规"},
    {"name": "包革金属棒(大头棍、护身棒)", "skill": "格斗(斗殴)", "dam": "1D8+DB", "tho": 0, "range": "接触", "round": "1", "num": "", "price": "2/15", "err": "", "time": "1920s，现代", "category": "常规"},
    {"name": "大棒(棒球棒、拨火棍)", "skill": "格斗(斗殴)", "dam": "1D8+DB", "tho": 0, "range": "接触", "round": "1", "num": "", "price": "3/35", "err": "", "time": "1920s，现代", "category": "常规"},
    {"name": "小型棍棒(警棍)", "skill": "格斗(斗殴)", "dam": "1D6+DB", "tho": 0, "range": "接触", "round": "1", "num": "", "price": "3/35", "err": "", "time": "1920s，现代", "category": "常规"},
    {"name": "弩", "skill": "射击(弓弩)", "dam": "1D8+2", "tho": 1, "range": "50", "round": "1/2", "num": "1", "price": "10/100", "err": "96", "time": "1920s，现代", "category": "常规"},
    {"name": "绞索", "skill": "格斗(绞索)", "dam": "1D6+DB", "tho": 1, "range": "接触", "round": "1", "num": "", "price": "0.5/3", "err": "", "time": "1920s，现代", "category": "常规"},
    {"name": "手斧/手镰", "skill": "格斗(斧)", "dam": "1D6+1+DB", "tho": 1, "range": "接触", "round": "1", "num": "", "price": "3/9", "err": "", "time": "1920s，现代", "category": "常规"},
    {"name": "大型刀(甘蔗刀等)", "skill": "格斗(斗殴)", "dam": "1D8+DB", "tho": 1, "range": "接触", "round": "1", "num": "", "price": "4/50", "err": "", "time": "1920s，现代", "category": "常规"},
    {"name": "中型刀(切肉餐刀等)", "skill": "格斗(斗殴)", "dam": "1D4+2+DB", "tho": 1, "range": "接触", "round": "1", "num": "", "price": "2/15", "err": "", "time": "1920s，现代", "category": "常规"},
    {"name": "小型刀(弹簧折叠刀等)", "skill": "格斗(斗殴)", "dam": "1D4+DB", "tho": 1, "range": "接触", "round": "1", "num": "", "price": "2/6", "err": "", "time": "1920s，现代", "category": "常规"},
    {"name": "220V通电导线", "skill": "格斗(斗殴)", "dam": "2D8+晕", "tho": 0, "range": "接触", "round": "1", "num": "", "price": "", "err": "95", "time": "现代", "category": "常规"},
    {"name": "催泪喷雾", "skill": "格斗(斗殴)", "dam": "晕", "tho": 0, "range": "2", "round": "1", "num": "25", "price": "-/10", "err": "", "time": "1920s，现代", "category": "常规"},
    {"name": "双节棍", "skill": "格斗(链枷)", "dam": "1D8+DB", "tho": 0, "range": "接触", "round": "1", "num": "", "price": "1/10", "err": "", "time": "1920s，现代", "category": "常规"},
    {"name": "投石", "skill": "投掷", "dam": "1D4+半DB", "tho": 0, "range": "STR/5m", "round": "1", "num": "", "price": "", "err": "", "time": "1920s，现代", "category": "常规"},
    {"name": "手里剑", "skill": "投掷", "dam": "1D3+半DB", "tho": 1, "range": "STR/5m", "round": "2", "num": "1", "price": "0.5/3", "err": "100", "time": "1920s，现代", "category": "常规"},
    {"name": "矛、骑士长枪", "skill": "格斗(矛)", "dam": "1D8+1", "tho": 1, "range": "接触", "round": "1", "num": "", "price": "25/150", "err": "", "time": "1920s，现代", "category": "常规"},
    {"name": "投矛", "skill": "投掷", "dam": "1D8+半DB", "tho": 1, "range": "STR/5m", "round": "1", "num": "", "price": "42760", "err": "", "time": "稀有", "category": "常规"},
    {"name": "大型剑(马刀)", "skill": "格斗(刀剑)", "dam": "1D8+1+DB", "tho": 1, "range": "接触", "round": "1", "num": "", "price": "30/75", "err": "", "time": "1920s，现代", "category": "常规"},
    {"name": "中型剑(佩剑、重剑)", "skill": "格斗(刀剑)", "dam": "1D6+1+DB", "tho": 1, "range": "接触", "round": "1", "num": "", "price": "15/100", "err": "", "time": "1920s，现代", "category": "常规"},
    {"name": "轻型剑(花剑、剑杖)", "skill": "格斗(刀剑)", "dam": "1D6+DB", "tho": 1, "range": "接触", "round": "1", "num": "", "price": "25/100", "err": "", "time": "1920s，现代", "category": "常规"},
    {"name": "电棍", "skill": "格斗(斗殴)", "dam": "1D3+晕", "tho": 0, "range": "接触", "round": "1", "num": "", "price": "-/200", "err": "97", "time": "现代", "category": "常规"},
    {"name": "电击枪", "skill": "射击(手枪)", "dam": "1D3+晕", "tho": 0, "range": "5", "round": "1", "num": "3", "price": "-/400", "err": "95", "time": "现代", "category": "常规"},
    {"name": "战斗回力镖", "skill": "投掷", "dam": "1D8+半DB", "tho": 0, "range": "STR/5m", "round": "1", "num": "", "price": "2/4", "err": "", "time": "稀有", "category": "常规"},
    {"name": "伐木斧", "skill": "格斗(斧)", "dam": "1D8+2+DB", "tho": 1, "range": "接触", "round": "1", "num": "", "price": "5/10", "err": "", "time": "1920s，现代", "category": "常规"},
    {"name": "燧发手枪", "skill": "射击(手枪)", "dam": "1D6+1", "tho": 1, "range": "10", "round": "1/4", "num": "1", "price": "30/300", "err": "95", "time": "稀有", "category": "手枪"},
    {"name": ".22(5.6mm)小型自动手枪", "skill": "射击(手枪)", "dam": "1D6", "tho": 1, "range": "10", "round": "1(3)", "num": "6", "price": "25/190", "err": "100", "time": "1920s，现代", "category": "手枪"},
    {"name": ".25(6.35mm)短口手枪(单管)", "skill": "射击(手枪)", "dam": "1D6", "tho": 1, "range": "3", "round": "1", "num": "1", "price": "12/55", "err": "100", "time": "1920s", "category": "手枪"},
    {"name": ".32(7.65mm)左轮", "skill": "射击(手枪)", "dam": "1D8", "tho": 1, "range": "15", "round": "1(3)", "num": "6", "price": "15/200", "err": "100", "time": "1920s，现代", "category": "手枪"},
    {"name": ".32(7.65mm)自动手枪", "skill": "射击(手枪)", "dam": "1D8", "tho": 1, "range": "15", "round": "1(3)", "num": "8", "price": "20/350", "err": "99", "time": "1920s，现代", "category": "手枪"},
    {"name": ".357马格南左轮", "skill": "射击(手枪)", "dam": "1D8+1D4", "tho": 1, "range": "15", "round": "1(3)", "num": "6", "price": "425", "err": "100", "time": "现代", "category": "手枪"},
    {"name": ".38(9mm)左轮", "skill": "射击(手枪)", "dam": "1D10", "tho": 1, "range": "15", "round": "1(3)", "num": "6", "price": "25/200", "err": "100", "time": "1920s，现代", "category": "手枪"},
    {"name": ".38(9mm)自动手枪", "skill": "射击(手枪)", "dam": "1D10", "tho": 1, "range": "15", "round": "1(3)", "num": "8", "price": "30/375", "err": "99", "time": "1920s，现代", "category": "手枪"},
    {"name": "贝瑞塔M9", "skill": "射击(手枪)", "dam": "1D10", "tho": 1, "range": "15", "round": "1(3)", "num": "15", "price": "-/500", "err": "98", "time": "现代", "category": "手枪"},
    {"name": "9mm格洛克17", "skill": "射击(手枪)", "dam": "1D10", "tho": 1, "range": "15", "round": "1(3)", "num": "17", "price": "-/500", "err": "98", "time": "现代", "category": "手枪"},
    {"name": "9mm鲁格P08", "skill": "射击(手枪)", "dam": "1D10", "tho": 1, "range": "15", "round": "1(3)", "num": "8", "price": "75/600", "err": "99", "time": "1920s，现代", "category": "手枪"},
    {"name": ".41(10.4mm)左轮", "skill": "射击(手枪)", "dam": "1D10", "tho": 1, "range": "15", "round": "1(3)", "num": "8", "price": "30/-", "err": "100", "time": "1920s稀有", "category": "手枪"},
    {"name": ".44(11.2mm)马格南左轮", "skill": "射击(手枪)", "dam": "1D10+1D4+2", "tho": 1, "range": "15", "round": "1(3)", "num": "6", "price": "-/475", "err": "100", "time": "现代", "category": "手枪"},
    {"name": ".45(11.43mm)左轮", "skill": "射击(手枪)", "dam": "1D10+2", "tho": 1, "range": "15", "round": "1(3)", "num": "6", "price": "30/300", "err": "100", "time": "1920s，现代", "category": "手枪"},
    {"name": ".45(11.43mm)自动手枪", "skill": "射击(手枪)", "dam": "1D10+2", "tho": 1, "range": "15", "round": "1(3)", "num": "7", "price": "40/375", "err": "100", "time": "1920s，现代", "category": "手枪"},
    {"name": "IMI 沙漠之鹰", "skill": "射击(手枪)", "dam": "1D10+1D6+3", "tho": 1, "range": "15", "round": "1(3)", "num": "7", "price": "-/650", "err": "94", "time": "现代", "category": "手枪"},
    {"name": ".58 (14.7mm)1855年式春田步枪", "skill": "射击(步/霰)", "dam": "1D10+4", "tho": 1, "range": "60", "round": "1/4", "num": "1", "price": "25/350", "err": "95", "time": "稀有", "category": "半自动步枪"},
    {"name": ".22 (5.6mm)栓式枪机步枪", "skill": "射击(步/霰)", "dam": "1D6+1", "tho": 1, "range": "30", "round": "1", "num": "6", "price": "13/70", "err": "99", "time": "1920s，现代", "category": "半自动步枪"},
    {"name": ".30 (7.62mm)杠杆式枪机步枪", "skill": "射击(步/霰)", "dam": "2D6", "tho": 1, "range": "50", "round": "1", "num": "6", "price": "19/150", "err": "98", "time": "1920s，现代", "category": "半自动步枪"},
    {"name": ".45 (11.43mm)马蒂尼-亨利步枪", "skill": "射击(步/霰)", "dam": "1D8+1D6+3", "tho": 1, "range": "80", "round": "1/3", "num": "1", "price": "20/200", "err": "100", "time": "1920s", "category": "半自动步枪"},
    {"name": "莫兰上校的气动步枪③", "skill": "射击(步/霰)", "dam": "2D6+1", "tho": 1, "range": "20", "round": "1/3", "num": "1", "price": "200/-", "err": "88", "time": "1920s", "category": "半自动步枪"},
    {"name": "加兰德M1、M2步枪", "skill": "射击(步/霰)", "dam": "2D6+4", "tho": 1, "range": "110", "round": "1", "num": "8", "price": "-/400", "err": "100", "time": "二战及以后", "category": "半自动步枪"},
    {"name": "SKS半自动步枪(56半)", "skill": "射击(步/霰)", "dam": "2D6+1", "tho": 1, "range": "90", "round": "1(2)", "num": "10", "price": "-/500", "err": "97", "time": "现代", "category": "半自动步枪"},
    {"name": ".303(7.7mm)李-恩菲尔德", "skill": "射击(步/霰)", "dam": "2D6+4", "tho": 1, "range": "110", "round": "1", "num": "5", "price": "50/300", "err": "100", "time": "1920s，现代", "category": "半自动步枪"},
    {"name": ".30-06(7.62mm)手动枪机步枪", "skill": "射击(步/霰)", "dam": "2D6+4", "tho": 1, "range": "110", "round": "1", "num": "5", "price": "75/175", "err": "100", "time": "1920s，现代", "category": "半自动步枪"},
    {"name": ".30-06(7.62mm)半自动步枪", "skill": "射击(步/霰)", "dam": "2D6+4", "tho": 1, "range": "110", "round": "1", "num": "5", "price": "-/275", "err": "100", "time": "现代", "category": "半自动步枪"},
    {"name": ".444(11.28mm)马林步枪", "skill": "射击(步/霰)", "dam": "2D8+4", "tho": 1, "range": "110", "round": "1", "num": "5", "price": "-/400", "err": "98", "time": "现代", "category": "半自动步枪"},
    {"name": "猎象枪(双管)", "skill": "射击(步/霰)", "dam": "3D6+4", "tho": 1, "range": "100", "round": "1或2", "num": "2", "price": "400/1000", "err": "100", "time": "1920s，现代", "category": "半自动步枪"},
    {"name": "AK-47或AKM", "skill": "射击(步/霰)", "dam": "2D6+1", "tho": 1, "range": "100", "round": "1(2)或全自动", "num": "30", "price": "200", "err": "100", "time": "现代", "category": "全自动步枪"},
    {"name": "AK-74", "skill": "射击(步/霰)", "dam": "2D6+1", "tho": 1, "range": "110", "round": "1(2)或全自动", "num": "30", "price": "1000", "err": "97", "time": "现代", "category": "全自动步枪"},
    {"name": "M1918式勃朗宁自动步枪", "skill": "射击(机枪)", "dam": "2D6+4", "tho": 1, "range": "90", "round": "1(2)或全自动", "num": "20", "price": "800/1500", "err": "100", "time": "1920s", "category": "机枪"},
    {"name": "FN FAL 突击步枪", "skill": "射击(步/霰)", "dam": "2D6+4", "tho": 1, "range": "110", "round": "1-3发点射", "num": "20", "price": "1500", "err": "97", "time": "现代", "category": "全自动步枪"},
    {"name": "加利尔突击步枪", "skill": "射击(步/霰)", "dam": "2D6", "tho": 1, "range": "110", "round": "1(2)或全自动", "num": "20", "price": "2000", "err": "98", "time": "现代", "category": "全自动步枪"},
    {"name": "M16A2", "skill": "射击(步/霰)", "dam": "2D6", "tho": 1, "range": "110", "round": "1-3发点射", "num": "30", "price": "", "err": "97", "time": "现代", "category": "全自动步枪"},
    {"name": "M4", "skill": "射击(步/霰)", "dam": "2D6", "tho": 1, "range": "90", "round": "1或3发点射", "num": "30", "price": "", "err": "97", "time": "现代", "category": "全自动步枪"},
    {"name": "斯泰尔AUG", "skill": "射击(步/霰)", "dam": "2D6", "tho": 1, "range": "110", "round": "1(2)或全自动", "num": "30", "price": "1100", "err": "99", "time": "现代", "category": "全自动步枪"},
    {"name": "贝雷塔AR70/90", "skill": "射击(步/霰)", "dam": "2D6", "tho": 1, "range": "110", "round": "1或全自动", "num": "30", "price": "2800", "err": "99", "time": "现代", "category": "全自动步枪"},
    {"name": "20号霰弹枪(双管)", "skill": "射击(步/霰)", "dam": "2D6/1D6/1D3", "tho": 0, "range": "10/20/50", "round": "1或2", "num": "2", "price": "35/稀", "err": "100", "time": "1920s", "category": "霰弹枪"},
    {"name": "16号霰弹枪(双管)", "skill": "射击(步/霰)", "dam": "2D6+2/1D6+1/1D4", "tho": 0, "range": "10/20/50", "round": "1或2", "num": "2", "price": "40/稀", "err": "100", "time": "1920s", "category": "霰弹枪"},
    {"name": "12号霰弹枪(双管)", "skill": "射击(步/霰)", "dam": "4D6/2D6/1D6", "tho": 0, "range": "10/20/50", "round": "1或2", "num": "2", "price": "40/200", "err": "100", "time": "1920s，现代", "category": "霰弹枪"},
    {"name": "12号霰弹枪(手压式)", "skill": "射击(步/霰)", "dam": "4D6/2D6/1D6", "tho": 0, "range": "10/20/50", "round": "1", "num": "5", "price": "45/100", "err": "100", "time": "现代", "category": "霰弹枪"},
    {"name": "12号霰弹枪(半自动)", "skill": "射击(步/霰)", "dam": "4D6/2D6/1D6", "tho": 0, "range": "10/20/50", "round": "1(2)", "num": "5", "price": "45/100", "err": "100", "time": "现代", "category": "霰弹枪"},
    {"name": "12号霰弹枪(双管,锯短)", "skill": "射击(步/霰)", "dam": "4D6/1D6", "tho": 0, "range": "5/10", "round": "1或2", "num": "2", "price": "", "err": "100", "time": "1920s", "category": "霰弹枪"},
    {"name": "10号霰弹枪(双管)", "skill": "射击(步/霰)", "dam": "4D6+2/2D6+1/1D4", "tho": 0, "range": "10/20/50", "round": "1或2", "num": "2", "price": "稀", "err": "100", "time": "1920s稀有", "category": "霰弹枪"},
    {"name": "12号贝里尼M3(折叠式枪托)", "skill": "射击(步/霰)", "dam": "4D6/2D6/1D6", "tho": 0, "range": "10/20/50", "round": "1(2)", "num": "7", "price": "-/895", "err": "100", "time": "现代", "category": "霰弹枪"},
    {"name": "12号SPAS (折叠式枪托)", "skill": "射击(步/霰)", "dam": "4D6/2D6/1D6", "tho": 0, "range": "10/20/50", "round": "1", "num": "8", "price": "-/600", "err": "98", "time": "现代", "category": "霰弹枪"},
    {"name": "MP18I/M现代28II", "skill": "射击(冲锋枪)", "dam": "1D10", "tho": 1, "range": "20", "round": "1(2)或全自动", "num": "20/30/32", "price": "1000/20000", "err": "96", "time": "1920s", "category": "冲锋枪"},
    {"name": "MP5", "skill": "射击(冲锋枪)", "dam": "1D10", "tho": 1, "range": "20", "round": "1(2)或全自动", "num": "15/30", "price": "", "err": "97", "time": "现代", "category": "冲锋枪"},
    {"name": "MAC-11", "skill": "射击(冲锋枪)", "dam": "1D10", "tho": 1, "range": "15", "round": "1(3)或全自动", "num": "32", "price": "-/750", "err": "96", "time": "现代", "category": "冲锋枪"},
    {"name": "蝎式", "skill": "射击(冲锋枪)", "dam": "1D8", "tho": 1, "range": "15", "round": "1(3)或全自动", "num": "20", "price": "", "err": "96", "time": "现代", "category": "冲锋枪"},
    {"name": "汤普森", "skill": "射击(冲锋枪)", "dam": "1D10+2", "tho": 1, "range": "20", "round": "1或全自动", "num": "20/30/50", "price": "1000/20000", "err": "96", "time": "1920s", "category": "冲锋枪"},
    {"name": "乌兹", "skill": "射击(冲锋枪)", "dam": "1D10", "tho": 1, "range": "20", "round": "1(2)或全自动", "num": "32", "price": "-/1000", "err": "98", "time": "现代", "category": "冲锋枪"},
    {"name": "巴雷特M82", "skill": "射击(步/霰)", "dam": "2D10+1D8+6", "tho": 1, "range": "250", "round": "1", "num": "11", "price": "3000", "err": "96", "time": "现代", "category": "狙击步枪"},
    {"name": "PTRD-41反坦克步枪", "skill": "射击(步/霰)", "dam": "2D10+1D10+8", "tho": 1, "range": "230", "round": "1", "num": "5", "price": "1500", "err": "94", "time": "现代", "category": "狙击步枪"},
    {"name": "1882年式加特林", "skill": "射击(机枪)", "dam": "2D6+4", "tho": 1, "range": "100", "round": "全自动", "num": "200", "price": "2000/14000", "err": "96", "time": "1920s稀有", "category": "机枪"},
    {"name": "勃朗宁M1917A1(7.62mm)", "skill": "射击(机枪)", "dam": "2D6+4", "tho": 1, "range": "150", "round": "全自动", "num": "250", "price": "3000/3万", "err": "96", "time": "1920s", "category": "机枪"},
    {"name": "布伦轻机枪", "skill": "射击(机枪)", "dam": "2D6+4", "tho": 1, "range": "110", "round": "1或全自动", "num": "30/100", "price": "3000/5万", "err": "96", "time": "1920s", "category": "机枪"},
    {"name": "路易斯Ⅰ型机枪", "skill": "射击(机枪)", "dam": "2D6+4", "tho": 1, "range": "110", "round": "全自动", "num": "27/97", "price": "3000/2万", "err": "96", "time": "1920s", "category": "机枪"},
    {"name": "GE M134式7.62mm速射机枪", "skill": "射击(机枪)", "dam": "2D6+4", "tho": 1, "range": "200", "round": "全自动", "num": "4000", "price": "", "err": "98", "time": "现代", "category": "机枪"},
    {"name": "FN 米尼米，弹夹/弹带", "skill": "射击(机枪)", "dam": "2D6", "tho": 1, "range": "110", "round": "全自动", "num": "30/200", "price": "", "err": "99", "time": "现代", "category": "机枪"},
    {"name": "维克斯.303机枪", "skill": "射击(机枪)", "dam": "2D6+4", "tho": 1, "range": "110", "round": "全自动", "num": "250", "price": "", "err": "99", "time": "1920s", "category": "机枪"},
    {"name": "莫洛托夫鸡尾酒", "skill": "投掷", "dam": "2D6 烧", "tho": 1, "range": "STRft", "round": "1/2", "num": "一次性", "price": "", "err": "95", "time": "1920s，现代", "category": "其他"},
    {"name": "信号枪(信号弹枪)", "skill": "射击(手枪)", "dam": "1D10+1D3 烧", "tho": 1, "range": "10", "round": "1/2", "num": "1", "price": "15/75", "err": "100", "time": "1920s，现代", "category": "其他"},
    {"name": "M79式40mm榴弹发射器", "skill": "射击(重武器)", "dam": "3D10/2码", "tho": 1, "range": "20", "round": "1/3", "num": "1", "price": "", "err": "99", "time": "现代", "category": "其他"},
    {"name": "炸药棒", "skill": "投掷", "dam": "4D10/3码", "tho": 1, "range": "STRft", "round": "1/2", "num": "一次性", "price": "2/5", "err": "99", "time": "1920s，现代", "category": "其他"},
    {"name": "雷管", "skill": "电气维修", "dam": "2D10/1码", "tho": 1, "range": "", "round": "", "num": "一次性", "price": "20/整盒", "err": "100", "time": "1920s，现代", "category": "其他"},
    {"name": "爆破筒", "skill": "爆破", "dam": "1D10/3码", "tho": 1, "range": "即地", "round": "1", "num": "一次性", "price": "　", "err": "95", "time": "1920s，现代", "category": "其他"},
    {"name": "塑胶炸弹(C-4)，4盎司", "skill": "爆破", "dam": "6D10/3码", "tho": 1, "range": "即地", "round": "1", "num": "一次性", "price": "", "err": "99", "time": "现代", "category": "其他"},
    {"name": "手榴弹", "skill": "投掷", "dam": "4D10/3码", "tho": 1, "range": "STRft", "round": "1/2", "num": "一次性", "price": "", "err": "99", "time": "1920s，现代", "category": "其他"},
    {"name": "81mm迫击炮", "skill": "炮术", "dam": "6D10/6码", "tho": 1, "range": "500", "round": "1", "num": "独立装弹", "price": "", "err": "100", "time": "现代", "category": "其他"},
    {"name": "75mm野战火炮", "skill": "炮术", "dam": "10D10/2码", "tho": 1, "range": "500", "round": "1/4", "num": "独立装弹", "price": "1500/-", "err": "99", "time": "1920s，现代", "category": "其他"},
    {"name": "120mm坦克主炮", "skill": "炮术", "dam": "10D10/2码", "tho": 1, "range": "2000", "round": "1", "num": "独立装弹", "price": "", "err": "100", "time": "现代", "category": "其他"},
    {"name": "5英寸(127mm)舰炮", "skill": "炮术", "dam": "15D10/4码", "tho": 1, "range": "3000", "round": "1", "num": "自动上弹", "price": "", "err": "98", "time": "1920s，现代", "category": "其他"},
    {"name": "反步兵地雷", "skill": "爆破", "dam": "4D10/5码", "tho": 1, "range": "即地", "round": "1", "num": "一次性", "price": "", "err": "99", "time": "现代", "category": "其他"},
    {"name": "阔剑地雷", "skill": "爆破", "dam": "6D6/20码", "tho": 1, "range": "即地", "round": "1", "num": "一次性", "price": "", "err": "99", "time": "现代", "category": "其他"},
    {"name": "火焰喷射器", "skill": "射击(喷射器)", "dam": "2D6+烧", "tho": 1, "range": "25", "round": "1", "num": "至少10", "price": "", "err": "93", "time": "1920s，现代", "category": "其他"},
    {"name": "M72式单发轻型反坦克炮", "skill": "射击(重武器)", "dam": "8D10/1码", "tho": 1, "range": "150", "round": "1", "num": "1", "price": "", "err": "98", "time": "现代", "category": "其他"},
]


# ── 战斗规则 ──────────────────────────────────────────────────────────────

# 成功等级排序（与 dice_runtime 的对抗检定一致，保证两条路径语义相同）
OUTCOME_RANK = {
    "critical_success": 4,
    "hard_success": 3,
    "success": 2,
    "failure": 1,
    "fumble": 0,
}

WEAPON_BY_NAME: dict[str, dict] = {w["name"]: w for w in COC_WEAPONS}

_UNARMED = {"name": "徒手格斗", "skill": "格斗(斗殴)", "dam": "1D3+DB", "tho": 0}

# 近战/远程战斗技能前缀，用于「平手比战斗技能」与 fight_back 默认技能
_FIGHT_PREFIXES = ("格斗", "斗殴")
_SHOOT_PREFIXES = ("射击", "枪")


def lookup_weapon(name: str | None) -> dict | None:
    """在武器表里查武器：精确 → 子串。**查不到返回 None**（区别于 resolve_weapon 的回落徒手）。

    调用方需要区分「这是人类武器表里的东西」与「这是怪物自己的攻击方式（撕咬/触手/爪击）」时用它。
    """
    if not name:
        return None
    n = name.strip()
    if n in WEAPON_BY_NAME:
        return WEAPON_BY_NAME[n]
    for w in COC_WEAPONS:
        if n in w["name"] or w["name"] in n:
            return w
    return None


def resolve_weapon(name: str | None) -> dict:
    """按名字取武器数据：精确 → 子串 → 回落徒手。名字可为 KP 给的通俗名（如「匕首」）。"""
    return lookup_weapon(name) or _UNARMED


def burst_capacity(weapon: str | dict) -> int:
    """一轮最多射击发数：武器 round 字段括号内数字（如 '1(3)'→3）。
    无括号（'1' 单发栓动）或慢速装填（'1/2'）→ 1，即不可连发。"""
    w = resolve_weapon(weapon) if isinstance(weapon, str) else weapon
    m = re.search(r"\((\d+)\)", str(w.get("round") or "1"))
    return int(m.group(1)) if m else 1


def _roll_expr(expr: str) -> tuple[int, list[int]]:
    """掷一个多项骰式（如 '1D8+1D6+3'、'1D8-2'、'2D6'）：返回 (总和, 各骰点)。
    非数字项（燃烧/晕/码 等注记）忽略。用于武器伤害。"""
    expr = (expr or "").replace(" ", "").replace("－", "-")
    total, rolls = 0, []
    for term in re.findall(r"[+-]?[^+-]+", expr):
        sign = -1 if term.startswith("-") else 1
        t = term.lstrip("+-")
        # 取前导 NdM（不用 fullmatch）：武器伤害常把注记粘在骰式后（如「2D6 烧」去空格成「2D6烧」），
        # fullmatch 会整项作废、掷出 0；match 只认前导骰、忽略尾巴。
        m = re.match(r"(\d+)[dD](\d+)", t)
        if m:
            c, s = int(m.group(1)), int(m.group(2))
            r = [random.randint(1, s) for _ in range(c)]
            rolls += r
            total += sign * sum(r)
        elif t.isdigit():
            total += sign * int(t)
        # 其它（DB 已在上层替换掉；剩下的燃烧/晕/码等注记）忽略
    return total, rolls


def _max_dice(expr: str) -> int:
    """骰式的最大点数（贯穿/大成功用）：每个 NdM 记 N*M，flat 记其值，负项相减。"""
    expr = (expr or "").replace(" ", "").replace("－", "-")
    total = 0
    for term in re.findall(r"[+-]?[^+-]+", expr):
        sign = -1 if term.startswith("-") else 1
        t = term.lstrip("+-")
        m = re.match(r"(\d+)[dD](\d+)", t)   # 前导骰，忽略粘在后面的注记
        if m:
            total += sign * int(m.group(1)) * int(m.group(2))
        elif t.isdigit():
            total += sign * int(t)
    return total


def _substitute_db(dam: str, db: str) -> str:
    """把武器伤害公式里的 DB / 半DB 替换成具体角色的伤害加值表达式。
    半DB：掷出 DB 取半（floor），作为定值代入（贯穿/远程小武器用）。"""
    db = (db or "0").strip()
    expr = dam
    if "半DB" in expr:
        half = math.floor(_roll_expr(db)[0] / 2)
        expr = expr.replace("半DB", f"{'+' if half >= 0 else ''}{half}")
    if "DB" in expr:
        expr = expr.replace("DB", f"{'+' if not db.startswith('-') else ''}{db}")
    return expr


def roll_weapon_damage(weapon: str | dict, db: str = "0", *, impale: bool = False) -> dict:
    """掷武器伤害。weapon 可为武器名或武器 dict。impale=贯穿/大成功（贯穿武器）→ 最大骰点 + 再掷一次。

    返回 {total, rolls, notation, flags}。flags 收 燃烧/晕/贯穿 等注记（供叙述），不计入数值。
    霰弹/射程分段公式（'2D6+2/1D6+1/1D4'）取首段（近距离）。伤害不为负。
    """
    w = resolve_weapon(weapon) if isinstance(weapon, str) else weapon
    dam = (w.get("dam") or "1D3").split("/")[0].strip()   # 取首段（近距离）
    flags = [kw for kw in ("燃烧", "烧", "晕") if kw in dam]
    expr = _substitute_db(dam, db)
    total, rolls = _roll_expr(expr)
    if impale and w.get("tho"):
        # 贯穿：加上武器骰的最大点数（DB 不翻）——RAW「max + 掷一次」的可测版
        weapon_only = re.sub(r"([+-])(?![0-9]*[dD])[0-9]+", "", dam)  # 去掉纯定值项，只留骰
        total += _max_dice(_substitute_db(weapon_only, "0"))
        flags = flags + ["贯穿"]   # 供叙述/战斗卡标注「贯穿!」
    return {"total": max(0, total), "rolls": rolls, "notation": dam, "flags": flags}


def combat_skill_value(p: dict) -> int:
    """参战方最高的战斗技能值（格斗/射击/自己的攻击方式），用于先攻平手比较。"""
    skills = p.get("skills") or {}
    vals = [v for k, v in skills.items()
            if any(k.startswith(pre) for pre in _FIGHT_PREFIXES + _SHOOT_PREFIXES)]
    own = _named_attack_skill(skills, p.get("weapon"))   # 怪物的「撕咬 60」也是战斗技能
    if own:
        vals.append(skills[own])
    return max(vals) if vals else 0


_SIDE_RANK = {"player": 0, "ally": 1, "enemy": 2}


def roll_initiative(participants: list[dict]) -> list[dict]:
    """按 (火器优先, DEX 降序, 战斗技能降序, 玩家先于 NPC, id 稳定) 排先攻序。

    participants 每项需含：id, dex, has_firearm(bool), side, skills。返回排好序的新列表。
    """
    def key(p: dict):
        return (
            0 if p.get("has_firearm") else 1,           # 火器组整体在前
            -int(p.get("dex") or 0),                    # DEX 降序
            -combat_skill_value(p),                     # 平手比战斗技能
            _SIDE_RANK.get(p.get("side"), 3),           # 仍平：玩家 < 队友 < 敌
            str(p.get("id") or ""),                     # 再平：稳定
        )
    return sorted(participants, key=key)


def compare_checks(a, b) -> str:
    """比两次检定的成功等级：返回 'a' / 'b' / 'tie'。同级比技能值高者胜，再平则平。"""
    ar, br = OUTCOME_RANK.get(a.outcome, 1), OUTCOME_RANK.get(b.outcome, 1)
    if ar != br:
        return "a" if ar > br else "b"
    if a.skill_value != b.skill_value:
        return "a" if a.skill_value > b.skill_value else "b"
    return "tie"


def _named_attack_skill(skills: dict, weapon: str | dict | None) -> str | None:
    """技能表里有没有与这件武器/攻击方式**同名**的技能项。有则返回该技能名，否则 None。

    CoC 的怪物卡就是这么写的：循声者「撕咬 60%」、猎犬「爪击 45%」——攻击方式名即技能名，
    它们不在人类武器表里，也没有「格斗(斗殴)」这一项。
    """
    name = weapon.get("name") if isinstance(weapon, dict) else weapon
    name = (name or "").strip() if isinstance(name, str) else ""
    return name if name and (skills or {}).get(name) else None


def _fight_skill_of(data: dict) -> str:
    """角色的近战技能名（用于反击/机动默认）：优先与自己攻击方式同名的技能（怪物的「撕咬」），
    再取已有的格斗(X)，否则回落 格斗(斗殴)。攻击方式名由上层塞在 `_weapon` 里（可缺省）。"""
    skills = data.get("skills") or {}
    own = _named_attack_skill(skills, data.get("_weapon"))
    if own:
        return own
    for k in skills:
        if any(k.startswith(pre) for pre in _FIGHT_PREFIXES):
            return k
    return "格斗(斗殴)"


def attack_skill_of(data: dict, weapon: str | dict | None) -> str:
    """这次攻击该掷哪一项技能。

    ① 攻击者技能表里有与这件武器/攻击方式同名的技能 → 用它（怪物的「撕咬」「触手」）；
    ② 武器在人类武器表里 → 用武器登记的技能（格斗(斗殴) / 射击(手枪) …）；
    ③ 都不是 → 回落到攻击者自己的近战技能。

    ①必须排在②前面：怪物的攻击方式查不到武器表，只按②取技能会一路掉成「格斗(斗殴)」，
    而怪物卡上根本没有这一项 → 技能值按 0 结算 → 除非掷出 01 否则永远打不中。
    """
    skills = data.get("skills") or {}
    own = _named_attack_skill(skills, weapon)
    if own:
        return own
    if isinstance(weapon, dict):
        return weapon.get("skill") or _fight_skill_of(data)
    return (lookup_weapon(weapon) or {}).get("skill") or _fight_skill_of(data)


def allowed_reactions(is_firearm: bool, defender_grappled: bool = False) -> list[str]:
    """被攻击者可选的反应。火器不能反击（RAW），只能闪避/扑掩体。

    被擒抱（defender_grappled）者无法闪避/扑掩体：近战只剩反击，火器则无从躲避（空列表，命中即结算）。
    defender_grappled 默认 False，保持向后兼容。
    """
    if defender_grappled:
        return [] if is_firearm else ["fight_back"]
    return ["dodge", "cover"] if is_firearm else ["fight_back", "dodge"]


def resolve_first_aid(
    medic_data: dict, skill: str = "急救", options: CheckConfig | None = None,
) -> dict:
    """一次急救/医学检定。返回 {success, heal, lines}：成功 heal=1（回 1 HP），失败 heal=0。

    纯规则：只算成败与回血量。是否稳住濒死、写回角色卡、标记该处伤已急救，由 service 层决定。
    """
    chk = resolve_skill_check(medic_data, skill, options=options)
    success = chk.outcome in ("critical_success", "hard_success", "success")
    heal = 1 if success else 0
    verb = "成功" if success else "失败"
    lines = [f"{skill}检定{verb}（{chk.description}）" + ("，稳住伤势 +1 HP。" if success else "，未能处理伤势。")]
    return {"success": success, "heal": heal, "lines": lines}


def resolve_observe(
    observer_data: dict, skill: str = "侦查", options: CheckConfig | None = None,
) -> dict:
    """一次观察战场检定（侦查/心理学）。返回 {success, lines}。

    成功不改数值，仅产一条「察觉到破绽/敌情」beat，交子代理据此叙述具体线索。
    """
    chk = resolve_skill_check(observer_data, skill, options=options)
    success = chk.outcome in ("critical_success", "hard_success", "success")
    if success:
        lines = [f"{skill}检定成功（{chk.description}），观察到敌方的破绽/动向。"]
    else:
        lines = [f"{skill}检定失败（{chk.description}），未能看出更多。"]
    return {"success": success, "lines": lines}


def resolve_maneuver(
    attacker_data: dict, defender_data: dict, kind: str = "grapple",
    options: CheckConfig | None = None,
) -> dict:
    """对抗格斗机动（擒抱/缴械）。attacker 的格斗 对抗 defender 的格斗/闪避（取其一），
    用 compare_checks 比成功等级。返回 {success, condition, lines}：

    attacker 严格胜出 → success=True、condition = 'grappled'（擒抱）或 'disarmed'（缴械）；
    平手/败 → success=False、condition=None。不造成伤害。
    """
    atk_skill = _fight_skill_of(attacker_data)
    # 守方用更擅长的一项抵抗（格斗 or 闪避）
    dfn_skill = _fight_skill_of(defender_data)
    dfn_skills = defender_data.get("skills") or {}
    if (dfn_skills.get("闪避") or 0) > (dfn_skills.get(dfn_skill) or 0):
        dfn_skill = "闪避"
    atk = resolve_skill_check(attacker_data, atk_skill, options=options)
    dfn = resolve_skill_check(defender_data, dfn_skill, options=options)
    won = compare_checks(atk, dfn) == "a" and atk.meets_difficulty
    condition = None
    kind_cn = "擒抱" if kind == "grapple" else "缴械"
    if won:
        condition = "grappled" if kind == "grapple" else "disarmed"
        lines = [f"{kind_cn}对抗（{atk.description} vs {dfn.description}）→ 得手。"]
    else:
        lines = [f"{kind_cn}对抗（{atk.description} vs {dfn.description}）→ 未能得手。"]
    return {"success": won, "condition": condition, "lines": lines}


def resolve_attack(
    attacker_data: dict,
    attacker_db: str,
    weapon: str | dict,
    *,
    defender_data: dict | None = None,
    defense: str | None = None,     # None(无反应) | 'dodge' | 'cover' | 'fight_back'
    ranged: bool = False,
    difficulty: str = "normal",
    attacker_disarmed: bool = False,
    bonus: int = 0,
    penalty: int = 0,
    defense_penalty: int = 0,
    options: CheckConfig | None = None,
) -> dict:
    """解析一次攻击。返回结构化结果（谁命中谁、伤害多少、各自检定）。**不改状态、不落库。**

    - 近战 + defense='dodge'/'cover'：攻方武器技能 对抗 守方闪避；攻方成功等级更高才命中（平/低=被闪开）。
    - 近战 + defense='fight_back'：双方各掷格斗，胜方伤害负方（攻→守 或 守→攻反击）。
    - 远程 + defense=None：攻方射击检定，达到要求难度即命中。
    - 远程 + defense='dodge'/'cover'：射手转困难难度（扑向掩体）重掷，命中即伤、无反击。
    命中且为贯穿武器 + 极难/大成功 → 贯穿加伤。

    attacker_disarmed=True：攻方已被缴械 → 武器强制回落徒手格斗。
    bonus>0：给攻方命中检定加奖励骰（瞄准用，透传给 resolve_skill_check）。
    penalty>0：给攻方命中检定加惩罚骰（连发换目标用，透传给 resolve_skill_check）。
    options：本局村规，透传给每一次 resolve_skill_check（大成功/大失败阈值、奖惩骰上限）。
    """
    if attacker_disarmed:
        weapon = _UNARMED
    w = resolve_weapon(weapon) if isinstance(weapon, str) else weapon
    # 注意用原始入参 weapon（可能是「撕咬」这类怪物攻击方式）取技能，不能用回落后的 w——
    # w 已经是徒手格斗了，取出来只会是 格斗(斗殴)。
    atk_skill = attack_skill_of(attacker_data, weapon)
    atk = resolve_skill_check(
        attacker_data, atk_skill, difficulty, bonus=bonus, penalty=penalty, options=options,
    )

    result: dict = {
        "weapon": w.get("name"), "attacker_check": atk, "defender_check": None,
        "hit": False, "damage": None, "damage_to": None, "defense": defense,
    }

    def _damage(tier: str) -> dict:
        impale = bool(w.get("tho")) and tier in ("extreme", "critical")
        return roll_weapon_damage(w, attacker_db, impale=impale)

    # 远程：无反应 → 达标即命中；扑掩体/闪避 → 射手转困难难度（扑向掩体），无反击
    if ranged:
        if defense in ("dodge", "cover"):
            atk = resolve_skill_check(
                attacker_data, atk_skill, "hard", bonus=bonus, penalty=penalty, options=options,
            )
            result["attacker_check"] = atk
        if atk.meets_difficulty:
            result["hit"] = True
            result["damage"] = _damage(atk.tier)
            result["damage_to"] = "defender"
        return result
    if defense is None:
        if atk.meets_difficulty:
            result["hit"] = True
            result["damage"] = _damage(atk.tier)
            result["damage_to"] = "defender"
        return result

    # 近战：需要守方数据
    if defender_data is None:
        # 无守方信息 → 退化为「攻方达标即命中」
        if atk.meets_difficulty:
            result["hit"] = True
            result["damage"] = _damage(atk.tier)
            result["damage_to"] = "defender"
        return result

    if defense in ("dodge", "cover"):
        dfn = resolve_skill_check(
            defender_data, "闪避", "normal", penalty=defense_penalty, options=options,
        )
        result["defender_check"] = dfn
        # 攻方成功等级严格高于守方才命中（平手/守方更高 = 被闪开）
        if compare_checks(atk, dfn) == "a" and atk.meets_difficulty:
            result["hit"] = True
            result["damage"] = _damage(atk.tier)
            result["damage_to"] = "defender"
        return result

    # fight_back：双方格斗，胜方伤害负方
    dfn_skill = _fight_skill_of(defender_data)
    dfn = resolve_skill_check(
        defender_data, dfn_skill, "normal", penalty=defense_penalty, options=options,
    )
    result["defender_check"] = dfn
    winner = compare_checks(atk, dfn)
    if winner == "a" and atk.meets_difficulty:
        result["hit"] = True
        result["damage"] = _damage(atk.tier)
        result["damage_to"] = "defender"
    elif winner == "b" and dfn.meets_difficulty:
        # 守方反击命中攻方（用守方的徒手/格斗武器；此处按徒手估伤，具体武器由上层传入更佳）
        result["hit"] = True
        result["damage"] = roll_weapon_damage(_UNARMED, defender_data.get("_db", "0"),
                                               impale=dfn.tier in ("extreme", "critical") and False)
        result["damage_to"] = "attacker"
    return result


# ── 参战方存活 / 结束判定 / 回合推进 / 启发式 ──────────────────────────

_DOWN_STATUS = {"dead", "dying", "fled"}


def is_active(p: dict) -> bool:
    """仍能行动：状态非 死亡/濒死/逃离，且 HP>0。"""
    return p.get("status") not in _DOWN_STATUS and (p.get("hp") or 0) > 0


def check_combat_end(participants: list[dict]) -> str | None:
    """一方无人能战即结束。返回 'players_win' / 'players_defeated' / 'no_combatants' / None。

    玩家方 = player + ally；敌方 = enemy。
    """
    players = [p for p in participants if p.get("side") in ("player", "ally")]
    enemies = [p for p in participants if p.get("side") == "enemy"]
    p_alive = any(is_active(p) for p in players)
    e_alive = any(is_active(p) for p in enemies)
    if not p_alive and not e_alive:
        return "no_combatants"
    if not e_alive:
        return "players_win"
    if not p_alive:
        return "players_defeated"
    return None


def advance_turn(state: dict) -> dict:
    """推进到下一个「仍能行动」的参战方。走完一圈 → round++、重排先攻、清本轮标记。原地改 state。"""
    order = state.get("initiative") or []
    n = len(order)
    if n == 0:
        return state
    for _ in range(n):
        state["turn_index"] = (state.get("turn_index", 0) + 1) % n
        if state["turn_index"] == 0:
            state["round"] = state.get("round", 1) + 1
            for p in order:
                p["acted_this_round"] = False
                p["dodged_this_round"] = False
                if "mov" in p:                       # 新一轮重置常规移动预算 ⌈mov/2⌉（方格战场）
                    p["move_left"] = (int(p.get("mov") or 8) + 1) // 2
            state["initiative"] = roll_initiative(order)
            order = state["initiative"]
        if is_active(order[state["turn_index"]]):
            return state
    return state  # 无人可动（由 check_combat_end 收尾）


def heuristic_defense(defender: dict, is_firearm: bool, defender_grappled: bool = False) -> str:
    """NPC 防御者的自动防御选择（真人防御走交互，不经此）。
    火器只能闪避；近战：好斗者反击，其余闪避。
    被擒抱（defender_grappled）且近战 → 无法闪避，只能反击。defender_grappled 默认 False 向后兼容。"""
    if is_firearm:
        return "dodge"
    if defender_grappled:
        return "fight_back"
    return "fight_back" if defender.get("combat_ai") == "aggressive" else "dodge"


def heuristic_npc_action(state: dict, actor: dict) -> dict:
    """杂兵启发式：攻击对方阵营中 HP 最低的存活者；HP<25% 且策略为 cautious 则逃跑。
    返回 {action, target_id?, weapon?}。关键 NPC 走子代理（P3），不经此。"""
    hp_ratio = (actor.get("hp") or 0) / max(1, actor.get("max_hp") or 1)
    if actor.get("combat_ai") == "cautious" and hp_ratio < 0.25:
        return {"action": "flee"}
    my_side = actor.get("side")
    foes = [p for p in (state.get("initiative") or [])
            if is_active(p) and (
                (my_side == "enemy" and p.get("side") in ("player", "ally"))
                or (my_side in ("player", "ally") and p.get("side") == "enemy"))]
    if not foes:
        return {"action": "wait"}
    target = min(foes, key=lambda p: (p.get("hp") or 0))
    return {"action": "attack", "target_id": target.get("id"),
            "weapon": actor.get("weapon") or "徒手格斗"}


COMPONENT = Component(
    id="coc7.combat", title="战斗轮",
    requires=("coc7.check", "coc7.wound"),
    provides={"combat": Provide(
        __name__,
        bound=("resolve_attack", "resolve_first_aid", "resolve_observe", "resolve_maneuver"),
        plain=(
            "COC_WEAPONS", "WEAPON_CATEGORY_ORDER", "OUTCOME_RANK", "lookup_weapon",
            "resolve_weapon", "burst_capacity", "roll_weapon_damage", "combat_skill_value",
            "roll_initiative", "compare_checks", "attack_skill_of", "allowed_reactions",
            "is_active", "check_combat_end", "advance_turn", "heuristic_defense",
            "heuristic_npc_action",
        ),
    )},
)
