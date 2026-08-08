#!/usr/bin/env python3
"""情酱写作skill的成稿检查器。只报警，不自动改文。

用法：
    python scripts/check_qingj.py 稿件.md              默认按模式A检查
    python scripts/check_qingj.py 稿件.md --mode B     知乎问答
    python scripts/check_qingj.py 稿件.md --mode C     推特/小红书
    python scripts/check_qingj.py 稿件.md --mode D     改稿（只查AI痕迹，不查情酱风格规则）
    cat 稿件.md | python scripts/check_qingj.py -

退出码 0 表示无硬性失败，1 表示有失败项，2 表示读取出错。

失败项对应 SKILL.md 的绝对禁区，必须清零才能交稿。
提醒项需要回到材料和说话位置人工判断，脚本只负责发现形状。
"""

from __future__ import annotations

import argparse
import collections
import re
import sys
from pathlib import Path

# ---------------------------------------------------------------- 词表

BANNED_PHRASES = (
    "说白了", "说穿了", "先说结论",
    "意味着什么", "这意味着", "本质上", "换句话说", "不可否认",
    "综上所述", "总的来说", "归根结底",
    "值得注意的是", "需要指出的是", "不难发现",
    "让我们来看看", "接下来让我们", "让我们拭目以待",
    "从某种意义上说", "更微妙的是", "还有一层", "只说对了一半",
)

BUSINESS_JARGON = (
    "赋能", "抓手", "商业闭环", "价值闭环", "能力沉淀", "拉通",
    "底层逻辑", "顶层设计", "认知跃迁", "价值释放", "能力建设",
    "降本增效", "内容矩阵", "全链路", "组合拳", "打开想象空间",
    "结构性机会", "关键命题", "深层逻辑", "技术底座", "认知增量",
    "迭代闭环", "颗粒度",
)

INFLATED_MEANING = (
    "标志着", "见证了", "不可磨灭", "奠定了坚实基础", "奠定基础",
    "关键时刻", "关键转折点", "不断演变的格局", "深深植根于",
    "极其重要的", "至关重要", "意义非凡", "意义重大", "前所未有",
    "开启了新篇章", "新纪元", "范式转移",
)

PROMOTIONAL = (
    "充满活力的", "令人叹为观止", "开创性的", "叹为观止",
    "迷人的", "必游之地", "自然之美", "坐落于",
    "深刻影响", "深刻改变", "彰显", "淋漓尽致",
)

VAGUE_ATTRIBUTION = (
    "专家认为", "专家指出", "行业报告显示", "研究表明", "数据显示",
    "业内人士", "观察者指出", "有分析认为", "多个来源", "据报道",
)

SELF_MEDIA_SLOP = (
    "保姆级", "一文读懂", "干货满满", "绝绝子", "家人们", "宝子们",
    "谁懂啊", "狠狠", "封神", "yyds",
)

# 只提醒，不判失败。写具体事物时是好词，给抽象概念穿衣服时才是问题
LYRIC_WORDS = (
    "安放", "抵达", "微光", "褶皱", "丰盈", "滚烫",
    "轻盈", "赤裸", "剥开", "锋利", "坍塌", "浪潮",
)

# 无来源就是假细节，越具体AI味越重
FAKE_DETAIL = (
    "凌晨三点", "凌晨两点", "第三根烟", "冷咖啡", "窗外的雨",
    "烟头", "冷馒头", "深夜的屏幕", "泡杯茶", "老铁", "兄弟们", "谢邀",
)

CONJUNCTIONS = (
    "因为", "所以", "但是", "然而", "同时", "此外",
    "而且", "并且", "因此", "不仅", "与此同时",
)

# 情酱推荐口语词组。用来测密度，堆太密就是演活人
COLLOQUIAL = (
    "坦率的讲", "说真的", "我是真的觉得", "反正我觉得", "怎么说呢",
    "其实吧", "你想想看", "回到", "顺着上面", "写着写着突然想到",
    "我有时候觉得", "我一直觉得", "我自己的感受是", "说实话我也不确定",
    "我自己也还在摸索", "这个事儿我也踩过坑", "这种感觉太爽了",
    "我当时就愣住了", "想想就觉得兴奋", "太离谱了", "给我整不会了",
    "你敢信", "救命", "很多朋友可能不知道", "大家也都知道", "可能有小伙伴",
)

NOMINALIZATION = (
    re.compile(r"进行(?:了|一次|一场|着)?[^。，！？\n]{0,10}"
               r"(?:调整|优化|升级|分析|讨论|沟通|梳理|复盘|迭代|探索|尝试|思考|规划|布局)"),
    re.compile(r"实现了?[^。，！？\n]{0,14}的?[^。，！？\n]{0,6}(?:提升|增长|突破|转变|跃升|落地)"),
    re.compile(r"完成了?对[^。，！？\n]{0,16}的"),
    re.compile(r"起到了?[^。，！？\n]{0,12}的?作用"),
    re.compile(r"具有[^。，！？\n]{0,10}(?:意义|价值)"),
)

# 翻案腔。禁的是修辞动作，换套字继续做同一个动作仍然算命中
PIVOT_HARD = (
    re.compile(r"(?:并)?不(?:是|仅仅是|只是)[^。！？\n]{0,90}而是"),
    re.compile(r"并非[^。！？\n]{0,90}而是"),
    re.compile(r"不在于[^。！？\n]{0,90}而在于"),
    re.compile(r"与其说[^。！？\n]{0,90}(?:不如|毋宁|倒不如)"),
    re.compile(r"[。！？!?]\s*而是"),
    re.compile(r"表面(?:上)?[^。！？\n]{0,90}(?:其实|实际上|实则)"),
    re.compile(r"看似[^。！？\n]{0,90}(?:其实|实际上|实则)"),
    re.compile(r"这不是[^。！？\n]{0,60}这是"),
    re.compile(r"(?:并)?不是[^。！？\n]{1,40}，(?:更|才)?是[^，。！？\n]"),
    re.compile(r"从来(?:都)?(?:不是|与[^。！？，\n]{1,12}无关)"),
    re.compile(r"[^，。！？\n]{1,12}不重要，(?:重要|要紧)的是"),
    re.compile(r"答案(?:是否定的|恰恰相反)|恰恰相反"),
)

PIVOT_SOFT = (
    re.compile(r"(?:总|一直|曾|都)?以为[^！？\n]{2,60}?(?:其实|才发现|才明白|才知道|后来才)"),
    re.compile(r"回头(?:看|一看)?才(?:发现|明白|知道)"),
    re.compile(r"真正[^，。！？\n]{0,16}的(?:，)?是"),
    re.compile(r"真正(?:变了|改变)[^，。！？\n]{0,10}的"),
    re.compile(r"不只(?:是)?[^。！？\n]{0,90}(?:还|也)"),
)

BIG_WORDS = ("时代", "文明", "未来", "世界", "历史", "奇迹", "所有人", "全人类")

FIXED_TAIL_MARKERS = ("谢谢你看我的文章", "点个赞", "在看", "转发三连", "星标",
                      "作者：情酱", "投稿或交流", "让更多人看到", "评论区见")


# ---------------------------------------------------------------- 工具

def han_count(text: str) -> int:
    return len(re.findall(r"[一-鿿]", text))


def line_number(text: str, position: int) -> int:
    return text.count("\n", 0, position) + 1


def excerpt(value: str, width: int = 46) -> str:
    value = re.sub(r"\s+", " ", value).strip()
    return value if len(value) <= width else value[: width - 1] + "…"


def mask_non_prose(text: str) -> str:
    """屏蔽 frontmatter、代码、网址和链接，保留字符位置与换行。"""

    def mask(match: re.Match) -> str:
        return "".join("\n" if ch == "\n" else " " for ch in match.group())

    patterns = (
        re.compile(r"\A---\s*\n.*?\n---\s*(?:\n|\Z)", re.DOTALL),
        re.compile(r"```.*?```", re.DOTALL),
        re.compile(r"`[^`\n]*`"),
        re.compile(r"\]\([^\n)]*\)"),
        re.compile(r"https?://[^\s)>]+"),
        re.compile(r"<[^>\n]+>"),
        re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+"),
    )
    for pattern in patterns:
        text = pattern.sub(mask, text)
    return text


def find_terms(text: str, terms: tuple) -> list:
    """不重叠地找出词表命中，长词优先。"""
    hits, occupied = [], []
    for term in sorted(terms, key=len, reverse=True):
        for match in re.finditer(re.escape(term), text):
            start, end = match.span()
            if any(start < oe and end > os_ for os_, oe in occupied):
                continue
            hits.append((start, term))
            occupied.append((start, end))
    return sorted(hits)


def find_patterns(text: str, patterns: tuple) -> list:
    out, occupied = [], []
    for pattern in patterns:
        for match in pattern.finditer(text):
            if any(match.start() < oe and match.end() > os_ for os_, oe in occupied):
                continue
            out.append(match)
            occupied.append(match.span())
    return sorted(out, key=lambda m: m.start())


def is_tail_line(line: str) -> bool:
    return any(marker in line for marker in FIXED_TAIL_MARKERS)


def mask_fixed_tail(text: str) -> str:
    """屏蔽固定尾部和引用行。模板自带的冒号不算作者的问题。"""
    out = []
    for raw in text.split("\n"):
        stripped = raw.strip()
        if is_tail_line(raw) or stripped.startswith(">"):
            out.append(" " * len(raw))
        else:
            out.append(raw)
    return "\n".join(out)


def sentence_cv(text: str):
    lengths = [
        han_count(m.group())
        for m in re.finditer(r"[^。！？!?\n]+[。！？!?]", text)
        if han_count(m.group()) >= 4
    ]
    if len(lengths) < 12:
        return None
    mean = sum(lengths) / len(lengths)
    if mean == 0:
        return None
    var = sum((v - mean) ** 2 for v in lengths) / len(lengths)
    return (var ** 0.5) / mean, len(lengths)


def anaphora_runs(text: str, minimum: int = 3) -> list:
    """同一句里三个以上小句用同一个开头的排比。"""
    found = []
    for sentence in re.finditer(r"[^。！？!?\n]+(?:[。！？!?]|$)", text):
        clauses = [
            c.strip() for c in re.split(r"[，、；,;]", sentence.group())
            if han_count(c) >= 3
        ]
        if len(clauses) < minimum:
            continue
        run = 1
        for prev, cur in zip(clauses, clauses[1:]):
            if prev[:2] == cur[:2] and re.match(r"[一-鿿]{2}", cur):
                run += 1
                if run >= minimum:
                    found.append(sentence)
                    break
            else:
                run = 1
    return found


def prose_lines(text: str) -> list:
    """返回 (行号, 起始位置, 内容) 的正文行，跳过标题、引用、列表和空行。"""
    out = []
    position = 0
    for index, raw in enumerate(text.split("\n"), start=1):
        line = raw.strip()
        start = position
        position += len(raw) + 1
        if not line or line.startswith(("#", ">", "```", "![", "|")):
            continue
        if han_count(line) < 4:
            continue
        out.append((index, start, line))
    return out


# ---------------------------------------------------------------- 主检查

def main() -> int:
    parser = argparse.ArgumentParser(description="情酱写作skill成稿检查器")
    parser.add_argument("path", help="Markdown 或文本路径，- 表示标准输入")
    parser.add_argument("--mode", default="A", choices=list("ABCDabcd"),
                        help="A 公众号长文，B 知乎问答，C 短内容，D 改稿")
    args = parser.parse_args()
    mode = args.mode.upper()

    try:
        text = sys.stdin.read() if args.path == "-" else Path(args.path).read_text(encoding="utf-8")
    except (OSError, UnicodeError) as error:
        print(f"无法读取稿件。{error}", file=sys.stderr)
        return 2

    prose = mask_non_prose(text)
    total = han_count(prose)
    if total == 0:
        print("没有检测到汉字。", file=sys.stderr)
        return 2

    fails: list[str] = []
    warns: list[str] = []

    def fail(label: str, position: int, sample: str = "") -> None:
        tail = f"，{sample}" if sample else ""
        fails.append(f"{label}，第 {line_number(text, position)} 行{tail}")

    # ---- 标点禁令。模式D不查，改稿保留作者自己的标点习惯
    if mode != "D":
        body = mask_fixed_tail(prose)
        for symbol, label in (("：", "中文冒号"), (":", "英文冒号"),
                              ("——", "破折号"), ("—", "破折号"), ("–", "连接号")):
            for match in re.finditer(re.escape(symbol), body):
                if symbol == "—" and body[match.start() - 1: match.start() + 2] == "——":
                    continue
                fail(f"禁用标点{label}", match.start())

        # 双引号。模式C的推特可保留
        if mode != "C":
            for match in re.finditer(r"[“”「」『』]", body):
                fail("禁用引号（用加粗替代）", match.start())

        # 段末句号
        for index, start, line in prose_lines(prose):
            if is_tail_line(line):
                continue
            if line.endswith("。") and not line.endswith("。。。"):
                fails.append(f"段末句号，第 {index} 行，{excerpt(line[-18:])}")

    # ---- 词表禁令
    for label, table in (("套话/AI味词", BANNED_PHRASES),
                         ("商业黑话", BUSINESS_JARGON),
                         ("夸大意义", INFLATED_MEANING),
                         ("宣传性语言", PROMOTIONAL),
                         ("模糊归因", VAGUE_ATTRIBUTION),
                         ("自媒体腔", SELF_MEDIA_SLOP)):
        for position, term in find_terms(prose, table):
            fail(label, position, term)

    # ---- 翻案腔
    hard_pivots = find_patterns(prose, PIVOT_HARD)
    for match in hard_pivots:
        fail("翻案腔", match.start(), f"“{excerpt(match.group())}”")

    occupied = [m.span() for m in hard_pivots]
    soft_pivots = []
    for match in find_patterns(prose, PIVOT_SOFT):
        if any(match.start() < e and match.end() > s for s, e in occupied):
            continue
        soft_pivots.append(match)
        occupied.append(match.span())
    for match in soft_pivots:
        warns.append(
            f"疑似翻案腔变形，第 {line_number(text, match.start())} 行，"
            f"“{excerpt(match.group())}”。先立误解再推翻就改成正面陈述，正常用法保留。"
        )

    # ---- 结构提醒
    for match in anaphora_runs(prose)[:4]:
        warns.append(
            f"三连以上同构排比，第 {line_number(text, match.start())} 行，"
            f"“{excerpt(match.group())}”。留两项，第三项换说法或删掉。"
        )

    for match in find_patterns(prose, NOMINALIZATION)[:4]:
        warns.append(
            f"名词化句式，第 {line_number(text, match.start())} 行，"
            f"“{excerpt(match.group(), 34)}”。还原成直接的动词。"
        )

    conj = find_terms(prose, CONJUNCTIONS)
    if total >= 600 and len(conj) * 1000 / total > 7:
        top = "、".join(f"{t} {c} 次" for t, c in
                        collections.Counter(t for _, t in conj).most_common(4))
        warns.append(
            f"连词密度偏高，每千字 {len(conj) * 1000 // total} 个。{top}。"
            "中文小句靠语序和事理相接，删掉一半试试。"
        )

    cv = sentence_cv(prose)
    if cv and cv[0] < 0.42:
        warns.append(
            f"全文 {cv[1]} 个句子长度过于接近（变异系数 {cv[0]:.2f}）。"
            "人写的段落里十个字的句子会挨着四十个字的句子，放开几句，压短几句。"
        )

    lyric = find_terms(prose, LYRIC_WORDS)
    if len(lyric) >= 2:
        samples = "、".join(dict.fromkeys(t for _, t in lyric))
        warns.append(f"模型偏爱的抒情词 {len(lyric)} 处。{samples}。"
                     "写具体事物时保留，给抽象概念穿衣服时删掉。")

    fake = find_terms(prose, FAKE_DETAIL)
    if fake:
        samples = "、".join(dict.fromkeys(t for _, t in fake))
        warns.append(f"疑似假细节或论坛服装 {len(fake)} 处。{samples}。"
                     "没有来源、也不改变后文的细节，删掉。")

    big = [t for _, t in find_terms(prose, BIG_WORDS)]
    if big and total >= 800:
        tail_zone = prose[int(len(prose) * 0.85):]
        tail_big = [w for w in BIG_WORDS if w in tail_zone]
        if tail_big:
            warns.append(f"结尾出现大词 {'、'.join(dict.fromkeys(tail_big))}。"
                         "正文没有持续处理这个尺度，就回到具体事实或当前判断。")

    # ---- 情酱专属：硬凹检测
    colloquial = find_terms(prose, COLLOQUIAL)
    distinct = len(set(t for _, t in colloquial))
    if total >= 500:
        density = len(colloquial) * 1000 / total
        if density > 16:
            warns.append(
                f"口语词组密度 每千字 {density:.0f} 个（共 {len(colloquial)} 处）。"
                "堆太密会从活人变成演活人，删掉后信息和语气都不缺的那些就是凹出来的。"
            )
        elif mode == "A" and distinct < 8:
            warns.append(f"不同口语词组只有 {distinct} 种，模式A建议 8 种以上。")
        elif mode == "B" and distinct < 5:
            warns.append(f"不同口语词组只有 {distinct} 种，模式B建议 5 种以上。")

    emotion_marks = len(re.findall(r"。。。|？？？", prose))
    if total >= 500 and emotion_marks * 1000 / total > 5:
        warns.append(f"情绪标点 {emotion_marks} 处，密度偏高。"
                     "它要来自对眼前材料的真实反应，不能按固定间隔投放。")

    # ---- 段落形状
    lines = prose_lines(prose)
    if len(lines) >= 10:
        single = sum(1 for _, _, l in lines if len(re.findall(r"[。！？!?]", l)) <= 1)
        ratio = single / len(lines)
        if ratio >= 0.8:
            warns.append(f"{ratio:.0%} 的段落只有一句话，可能形成统一的短段鼓点。"
                         "一句话成段要有实际停顿价值，不能连续排成口号。")

        streak = 0
        for index, _, line in lines:
            if han_count(line) <= 24 and len(re.findall(r"[。！？!?]", line)) <= 1:
                streak += 1
                if streak >= 5:
                    warns.append(f"第 {index} 行附近连续 {streak} 个短促单句段，"
                                 "检查是否在排队喊结论。")
                    break
            else:
                streak = 0

    # ---- 模式专属格式
    if mode == "A":
        for index, raw in enumerate(text.split("\n"), start=1):
            if re.match(r"^#{2,6}\s", raw.strip()):
                warns.append(f"第 {index} 行出现小标题。模式A靠口语化转场衔接，"
                             "分条目方法论文章除外。")
                break
        bullets = 0
        for raw in text.split("\n"):
            if re.match(r"^\s*(?:[-+*]|\d+[.、])\s", raw):
                bullets += 1
                if bullets > 3:
                    warns.append("连续超过 3 个列表项。模式A改散文叙述。")
                    break
            elif raw.strip():
                bullets = 0

    for match in re.finditer(r"\*\*([^*\n]{40,})\*\*", text):
        warns.append(f"第 {line_number(text, match.start())} 行有超长加粗"
                     f"（{len(match.group(1))} 字）。超过 2 行的加粗几乎肯定是过度结构化。")
        break

    # ---- 篇幅
    ranges = {"A": (4000, 8000), "B": (500, 3000), "C": (50, 800)}
    if mode in ranges:
        low, high = ranges[mode]
        if total < low:
            warns.append(f"正文 {total} 字，低于模式{mode}建议下限 {low} 字。"
                         "材料不够时短一点是对的，别用重复解释填满。")
        elif total > high:
            warns.append(f"正文 {total} 字，高于模式{mode}建议上限 {high} 字。")

    # ---------------------------------------------------------------- 输出
    print(f"模式 {mode}　汉字数 {total}")
    print(f"翻案腔 {len(hard_pivots)}（疑似变形 {len(soft_pivots)}）　"
          f"口语词组 {len(colloquial)} 处 / {distinct} 种　"
          f"情绪标点 {emotion_marks}")

    if fails:
        print(f"\n需要修改（{len(fails)} 项）")
        for item in fails[:40]:
            print(f"- {item}")
        if len(fails) > 40:
            print(f"- 另有 {len(fails) - 40} 项未列出")

    if warns:
        print(f"\n需要人工判断（{len(warns)} 项）")
        for item in warns:
            print(f"- {item}")

    if not fails and not warns:
        print("\n未发现这份检查器覆盖的问题。")

    print("\n脚本只负责发现形状，判断不了文章有没有人。"
          "材料关、说话位置和推进检查仍要人工过。")

    return 1 if fails else 0


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    raise SystemExit(main())
