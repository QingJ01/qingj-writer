#!/usr/bin/env python3
"""情酱写作skill的成稿检查器。只报警，不自动改文。

用法：
    python scripts/check_qingj.py 稿件.md              默认按模式A检查
    python scripts/check_qingj.py 稿件.md --mode B     知乎问答
    python scripts/check_qingj.py 稿件.md --mode C     推特/小红书
    python scripts/check_qingj.py 稿件.md --mode D     改稿（只报候选，不判失败，不查情酱风格规则）
    python scripts/check_qingj.py 稿件.md --mode E --minutes 10   B站视频脚本，只查口播
    python scripts/check_qingj.py 稿件.md --mode F --minutes 10   公众号文章改视频脚本
    cat 稿件.md | python scripts/check_qingj.py -

模式E/F会跳过【屏幕】这类制作备注、引用行，以及标题含屏上呈现总则、拍摄建议、
核验清单、发布包、取舍清单、改编核对、质检报告、分镜的整节，整份交付稿可以直接检查。

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
    "值得注意的是", "不难发现",
    "让我们来看看", "接下来让我们", "让我们拭目以待",
)

# 洞察路标只在句首给段落抬价时判失败，"楼上还有一层"这类本义不算
ROAD_SIGN_PATTERNS = (
    re.compile(r"(?:^|[。！？!?]\s*)更微妙的是[^。！？!?\n]{0,24}", re.MULTILINE),
    re.compile(r"(?:^|[。！？!?]\s*)还有一层(?=(?:更|原因|问题|意思|考虑|变化|逻辑|价值|"
               r"作用|风险|影响|值得|很少|不容易|常被|往往))[^。！？!?\n]{0,24}", re.MULTILINE),
    *(re.compile(rf"(?:^|[。！？!?]\s*){phrase}[^。！？!?\n]{{0,24}}", re.MULTILINE)
      for phrase in ("只说对了一半", "需要指出的是", "从某种意义上说")),
)

# 与 references/human_writing_craft.md 第十一节的绝对禁词保持一致
BUSINESS_JARGON = (
    "赋能", "抓手", "商业闭环", "价值闭环", "能力沉淀", "拉通",
    "底层逻辑", "顶层设计", "认知跃迁", "价值释放", "能力建设",
    "降本增效", "内容矩阵", "全链路", "组合拳", "打开想象空间",
    "结构性机会", "关键命题", "深层逻辑", "技术底座", "公共底座",
    "技术主权", "单点风险", "主脊柱", "材料锚点", "认知增量", "迭代闭环",
)

# 语境判断词，本义时保留，给普通事情抬价时改写。只提醒
CONTEXT_JARGON = (
    "沉淀", "颗粒度", "对齐", "协同", "链路", "生态位", "心智",
    "范式", "方法论", "核心变量", "打法", "想象空间", "闭环",
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
    "轻盈", "赤裸", "剥开", "锋利", "坚硬", "柔软", "坍塌", "浪潮",
)

# 短距离混用三套以上借喻系统时提醒，先全部还原成本义
METAPHOR_FIELDS = {
    "温度": ("降温", "升温", "冷却", "余温", "温度最高"),
    "生死战争": ("杀死", "死因", "枪响", "开火", "战场", "引爆", "弹药"),
    "建筑灾害": ("坍塌", "崩塌", "地基", "砖头", "支柱", "废墟"),
    "仓储租赁": ("仓库", "库房", "租金", "取货", "入库", "库存"),
    "道路竞赛": ("赛道", "跑道", "岔路", "十字路口", "终点线", "门票"),
    "机器器官": ("齿轮", "引擎", "发动机", "血管", "骨架", "肌肉"),
    "海洋航行": ("蓝海", "浪潮", "潮水", "航船", "灯塔", "彼岸"),
}

# 情酱已经硬禁的本质上、换句话说等不在这里重复
SOFT_MARKERS = ("真正", "更深层次", "核心是", "关键在于")

REPEATED_OPENERS = (
    "其实", "不过", "当然", "所以", "但是", "后来", "当时",
    "很多人", "问题是", "更重要的是", "说到这里",
)

# 主干来得太晚的长前置成分
LEFT_BRANCH = (
    re.compile(r"(?:^|[。！？]\s*)在[^，。！？\n]{12,70}(?:以后|之后|之前|以前|过程中|情况下|背景下)，", re.M),
    re.compile(r"(?:^|[。！？]\s*)那些[^，。！？\n]{10,60}的[^，。！？\n]{2,30}[，。]", re.M),
    re.compile(r"(?:^|[。！？]\s*)(?:真正|最终|最后)让[^，。！？\n]{8,70}的，是", re.M),
)

# 无来源就是假细节，越具体AI味越重
FAKE_DETAIL = (
    "凌晨三点", "凌晨两点", "第三根烟", "冷咖啡", "窗外的雨",
    "烟头", "冷馒头", "深夜的屏幕", "泡杯茶", "老铁", "兄弟们", "谢邀",
    "我有个朋友",
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
                      "作者：情酱", "投稿或交流", "让更多人看到", "评论区见",
                      "一键三连", "我是情酱，陪你在AI时代")

# ---------------------------------------------------------------- 视频脚本（模式E/F）

# 标题含这些词的整节是给制作和发布看的，不念出来
VIDEO_APPENDIX = ("屏上呈现总则", "拍摄建议", "核验清单", "事实核验", "发布包",
                  "取舍清单", "改编取舍", "改编核对", "质检报告", "分镜")

# 【屏幕】【纯口播】【出处】【验收】这类独立成行的制作备注
CUE_LINE = re.compile(r"^\s*【[^】\n]{1,8}】")
SCREEN_CUE = re.compile(r"^\s*【(?:屏幕|纯口播)】", re.M)

# 公众号尾部出现在视频口播里就是没换平台
WECHAT_LEFTOVER = re.compile(r"在看[、，]|[、，]在看|星标|转发三连|谢谢你看我的文章|"
                             r"投稿或交流|作者：情酱|阅读原文")

# 阅读才有的指代，念出来观众找不到
READING_DEIXIS = re.compile(r"如[上下]图|[上下]图(?:所示|中|里)|上文(?:提到|说|讲|里|中)|"
                            r"前文(?:提到|说|讲)|下文(?:会|将|再)|文末|"
                            r"本文(?:中|里|讲|会|将|提到)|往下翻")

SPOKEN_SYMBOLS = re.compile(r"[→←↑↓≈≠×&]|(?<=[一-鿿])/|/(?=[一-鿿])")
SPOKEN_PAREN = re.compile(r"[（(][^）)\n]*[一-鿿][^）)\n]*[）)]")
TIMESTAMP = re.compile(r"(?<![\d.])\d{1,2}[:：]\d{2}(?![\d.])")
TIME_PROMISE = re.compile(r"[一二三四五六七八九十两几\d]+分钟(?:内|里)?"
                          r"(?:讲清楚|讲明白|讲透|带你|看懂|搞懂|学会|上手)")

# 情酱聊天语速，每分钟口播字数
SPEECH_RATE = 240
SPEECH_RATE_RANGE = (200, 280)
LONG_CLAUSE = 26


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
        re.compile(r"<!--.*?-->", re.DOTALL),
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


def mask_video_extras(text: str) -> str:
    """模式E/F只留口播。屏蔽制作备注、引用行和附录整节，保留字符位置与换行。"""
    out, skip_level = [], None
    for raw in text.split("\n"):
        heading = re.match(r"^\s*(#{1,6})\s+(.*)", raw)
        if heading:
            level, title = len(heading.group(1)), heading.group(2)
            if skip_level is not None and level <= skip_level:
                skip_level = None
            if skip_level is None and any(key in title for key in VIDEO_APPENDIX):
                skip_level = level
        if skip_level is not None or CUE_LINE.match(raw) or raw.strip().startswith(">"):
            out.append(" " * len(raw))
        else:
            out.append(raw)
    return "\n".join(out)


def long_clauses(text: str, limit: int = LONG_CLAUSE) -> list:
    """两个停顿之间字数太多，照着念要憋一口长气。"""
    return [
        m for m in re.finditer(r"[^，。！？；、,!?;：:…\n]+", text)
        if han_count(m.group()) > limit
    ]


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


def metaphor_cluster(text: str, distance: int = 800):
    hits = sorted(
        (m.start(), field, word)
        for field, words in METAPHOR_FIELDS.items()
        for word in words
        for m in re.finditer(re.escape(word), text)
    )
    for index, (start, _, _) in enumerate(hits):
        window = [hit for hit in hits[index:] if hit[0] - start <= distance]
        fields = {hit[1] for hit in window}
        if len(fields) >= 3:
            return window, fields
    return None


def heavy_de_sentences(text: str) -> list:
    """主干可能被四个以上"的"压到后面的长句。"""
    return [
        m for m in re.finditer(r"[^。！？!?\n]+(?:[。！？!?]|$)", text)
        if han_count(m.group()) >= 38 and m.group().count("的") >= 4
    ]


# ---------------------------------------------------------------- 主检查

def main() -> int:
    parser = argparse.ArgumentParser(description="情酱写作skill成稿检查器")
    parser.add_argument("path", help="Markdown 或文本路径，- 表示标准输入")
    parser.add_argument("--mode", default="A", choices=list("ABCDEFabcdef"),
                        help="A 公众号长文，B 知乎问答，C 短内容，D 改稿，"
                             "E B站视频脚本，F 公众号文章改视频脚本")
    parser.add_argument("--minutes", type=float, default=None,
                        help="模式E/F的目标时长（分钟），用来核对口播字数")
    args = parser.parse_args()
    mode = args.mode.upper()
    video = mode in "EF"
    if args.minutes is not None and args.minutes <= 0:
        print("--minutes 需要是正数。", file=sys.stderr)
        return 2

    try:
        text = sys.stdin.read() if args.path == "-" else Path(args.path).read_text(encoding="utf-8")
    except (OSError, UnicodeError) as error:
        print(f"无法读取稿件。{error}", file=sys.stderr)
        return 2

    prose = mask_non_prose(text)
    if video:
        prose = mask_video_extras(prose)
    total = han_count(prose)
    if total == 0:
        print("没有检测到汉字。" + ("模式E/F只统计口播，检查一下口播是不是都被写成了备注或附录。"
                                   if video else ""), file=sys.stderr)
        return 2

    fails: list[str] = []
    warns: list[str] = []

    def fail(label: str, position: int, sample: str = "") -> None:
        tail = f"，{sample}" if sample else ""
        if mode == "D":
            # 改稿时同一个词可能是术语、引文或真实论证，按作用判断，不按命中改
            advice = ("默认连同归属保留论断，正文后注明缺来源，不能只删归属。"
                      if label == "模糊归因" else
                      "看它在句子里承担什么作用再决定改不改。")
            warns.append(f"候选 {label}，第 {line_number(text, position)} 行{tail}。{advice}")
            return
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

    for match in find_patterns(prose, ROAD_SIGN_PATTERNS):
        fail("洞察路标", match.start(), f"“{excerpt(match.group().lstrip('。！？!? \n'))}”")

    hard_spans = [(p, p + len(t)) for p, t in find_terms(prose, BUSINESS_JARGON)]
    context = [
        (p, t) for p, t in find_terms(prose, CONTEXT_JARGON)
        if not any(p < e and p + len(t) > s for s, e in hard_spans)
    ]
    if context:
        samples = "、".join(dict.fromkeys(t for _, t in context))
        lines_hit = "、".join(dict.fromkeys(str(line_number(text, p)) for p, _ in context[:8]))
        warns.append(f"语境判断词 {len(context)} 处，第 {lines_hit} 行，{samples}。"
                     "本义准确时保留（闭环控制、排版对齐），给普通事情抬价时改写。")

    # ---- 翻案腔
    hard_pivots = find_patterns(prose, PIVOT_HARD)
    for match in hard_pivots:
        if mode == "D":
            warns.append(f"候选 翻案腔，第 {line_number(text, match.start())} 行，"
                         f"“{excerpt(match.group())}”。真实对比和论证骨架保留，"
                         "只清没有信息的那半句。")
            continue
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

    # 句长、连词、口语、段落这些阈值只适用于情酱自己的叙事文本。
    # 改稿时作者原有节奏不归脚本管，文档类文本的连词密度天然偏高
    style = mode != "D"

    conj = find_terms(prose, CONJUNCTIONS)
    if style and total >= 600 and len(conj) * 1000 / total > 7:
        top = "、".join(f"{t} {c} 次" for t, c in
                        collections.Counter(t for _, t in conj).most_common(4))
        warns.append(
            f"连词密度偏高，每千字 {len(conj) * 1000 // total} 个。{top}。"
            "中文小句靠语序和事理相接，删掉一半试试。"
        )

    cv = sentence_cv(prose)
    if style and cv and cv[0] < 0.42:
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
    if fake and style:
        samples = "、".join(dict.fromkeys(t for _, t in fake))
        warns.append(f"疑似假细节或论坛服装 {len(fake)} 处。{samples}。"
                     "没有来源、也不改变后文的细节，删掉。")

    big = [t for _, t in find_terms(prose, BIG_WORDS)]
    if style and big and total >= 800:
        tail_zone = prose[int(len(prose) * 0.85):]
        tail_big = [w for w in BIG_WORDS if w in tail_zone]
        if tail_big:
            warns.append(f"结尾出现大词 {'、'.join(dict.fromkeys(tail_big))}。"
                         "正文没有持续处理这个尺度，就回到具体事实或当前判断。")

    metaphors = metaphor_cluster(prose)
    if metaphors:
        window, fields = metaphors
        samples = "、".join(dict.fromkeys(hit[2] for hit in window))
        warns.append(f"八百字内混用 {len(fields)} 套借喻，{'、'.join(sorted(fields))}，"
                     f"例词 {samples}。先全部还原成本义，意思清楚了一个也不用放回。")

    # ---- 中文词序与洞察路标密度
    if style:
        markers = find_terms(prose, SOFT_MARKERS)
        marker_limit = max(2, total // 900)
        if len(markers) > marker_limit:
            samples = "、".join(dict.fromkeys(t for _, t in markers))
            warns.append(f"洞察路标 {len(markers)} 处，提醒线 {marker_limit} 处，{samples}。"
                         "检查是不是在给普通判断抬价。")

        left = find_patterns(prose, LEFT_BRANCH)
        if len(left) > max(2, total // 1200):
            samples = "；".join(f"第 {line_number(text, m.start())} 行“{excerpt(m.group(), 30)}”"
                               for m in left[:3])
            warns.append(f"长前置成分 {len(left)} 处，主干可能来得太晚。{samples}")

        dense = heavy_de_sentences(prose)
        if len(dense) > max(1, total // 1500):
            samples = "；".join(f"第 {line_number(text, m.start())} 行“{excerpt(m.group(), 30)}”"
                               for m in dense[:3])
            warns.append(f"{len(dense)} 个长句带四个以上“的”，先交代人和动作。{samples}")

    # ---- 情酱专属：硬凹检测
    colloquial = find_terms(prose, COLLOQUIAL)
    distinct = len(set(t for _, t in colloquial))
    if style and total >= 500:
        density = len(colloquial) * 1000 / total
        if density > 16:
            warns.append(
                f"口语词组密度 每千字 {density:.0f} 个（共 {len(colloquial)} 处）。"
                "堆太密会从活人变成演活人，删掉后信息和语气都不缺的那些就是凹出来的。"
            )
        elif mode == "A" and distinct < 8:
            warns.append(f"不同口语词组只有 {distinct} 种，模式A建议 8 种以上。")
        elif mode in "BEF" and distinct < 5:
            warns.append(f"不同口语词组只有 {distinct} 种，模式{mode}建议 5 种以上。")

    emotion_marks = len(re.findall(r"。。。|？？？", prose))
    if style and total >= 500 and emotion_marks * 1000 / total > 5:
        warns.append(f"情绪标点 {emotion_marks} 处，密度偏高。"
                     "它要来自对眼前材料的真实反应，不能按固定间隔投放。")

    # ---- 段落形状
    lines = prose_lines(prose)
    if style and len(lines) >= 10:
        single = sum(1 for _, _, l in lines if len(re.findall(r"[。！？!?]", l)) <= 1)
        ratio = single / len(lines)
        # 口播稿一句一行很正常，只看下面的连续短促段
        if ratio >= 0.8 and not video:
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

        openers = collections.Counter()
        first_seen = {}
        for index, _, line in lines:
            value = line.lstrip("“‘\"（(*")
            for opener in REPEATED_OPENERS:
                if value.startswith(opener):
                    openers[opener] += 1
                    first_seen.setdefault(opener, index)
                    break
        repeated = [(o, c) for o, c in openers.items() if c >= 4]
        if repeated:
            details = "、".join(f"{o} {c} 次" for o, c in repeated)
            first = min(first_seen[o] for o, _ in repeated)
            warns.append(f"段落开场重复，从第 {first} 行附近开始，{details}。")

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

    if video:
        for match in WECHAT_LEFTOVER.finditer(prose):
            fails.append(f"公众号尾部残留，第 {line_number(text, match.start())} 行，"
                         f"{match.group()}。换成B站固定尾部")

        deixis = list(READING_DEIXIS.finditer(prose))
        if deixis:
            samples = "；".join(f"第 {line_number(text, m.start())} 行“{m.group()}”"
                               for m in deixis[:4])
            warns.append(f"阅读指代 {len(deixis)} 处，{samples}。"
                         "观众看不到上文和下图，改成刚才说到、你看画面上。")

        clauses = long_clauses(prose)
        if clauses:
            samples = "；".join(f"第 {line_number(text, m.start())} 行“{excerpt(m.group(), 30)}”"
                               for m in clauses[:3])
            warns.append(f"{len(clauses)} 处两个停顿之间超过 {LONG_CLAUSE} 个字，"
                         f"念的时候没地方换气。{samples}")

        parens = list(SPOKEN_PAREN.finditer(prose))
        if parens:
            samples = "；".join(f"第 {line_number(text, m.start())} 行“{excerpt(m.group(), 24)}”"
                               for m in parens[:3])
            warns.append(f"口播里有 {len(parens)} 处括号，念出来没有括号。"
                         f"变成一句话，或者只放屏上。{samples}")

        symbols = list(SPOKEN_SYMBOLS.finditer(prose))
        if symbols:
            lines_hit = "、".join(dict.fromkeys(str(line_number(text, m.start()))
                                                for m in symbols[:8]))
            warns.append(f"口播里有 {len(symbols)} 个念不出来的符号，第 {lines_hit} 行。"
                         "改成话，或者只放屏上。")

        for match in list(TIMESTAMP.finditer(prose))[:1]:
            warns.append(f"第 {line_number(text, match.start())} 行像是时间戳。"
                         "脚本不标时间，按成片剪辑结果再加。")
        for match in list(TIME_PROMISE.finditer(prose))[:1]:
            warns.append(f"第 {line_number(text, match.start())} 行有时长承诺，"
                         f"“{match.group()}”。节奏让观众自己感受。")

        intro = prose.find("我是情酱")
        if intro < 0 or han_count(prose[:intro]) > 150:
            warns.append("开头三句内没有找到“我是情酱”。B站口播的自我介绍放在前两三句。")
        if "一键三连" not in prose:
            warns.append("没有找到B站固定尾部。")
        if not SCREEN_CUE.search(text):
            warns.append("没有【屏幕】或【纯口播】备注。逐段告诉作者屏幕上放什么，"
                         "不需要画面的段落标纯口播。")
        if "屏上呈现总则" not in text:
            warns.append("没有屏上呈现总则。脚本开头先列画面来源和段落到主画面的映射。")

    # ---- 篇幅
    ranges = {"A": (4000, 8000), "B": (500, 3000), "C": (50, 800)}
    if video:
        if args.minutes:
            low, high = (round(rate * args.minutes) for rate in SPEECH_RATE_RANGE)
            target = f"目标 {args.minutes:g} 分钟"
        else:
            low, high = 1000, 5200
            target = "没给 --minutes，按五到二十分钟的宽范围"
        estimate = total / SPEECH_RATE
        if total < low:
            warns.append(f"口播 {total} 字，约 {estimate:.1f} 分钟，{target}，少于 {low} 字。"
                         "材料不够就做短，别用重复解释撑时长。")
        elif total > high:
            warns.append(f"口播 {total} 字，约 {estimate:.1f} 分钟，{target}，超过 {high} 字。"
                         "先删背景铺垫和次要案例，结论和最具体的例子留下。")
    elif mode in ranges:
        low, high = ranges[mode]
        if total < low:
            warns.append(f"正文 {total} 字，低于模式{mode}建议下限 {low} 字。"
                         "材料不够时短一点是对的，别用重复解释填满。")
        elif total > high:
            warns.append(f"正文 {total} 字，高于模式{mode}建议上限 {high} 字。")

    # ---------------------------------------------------------------- 输出
    if video:
        print(f"模式 {mode}　口播汉字数 {total}　按每分钟 {SPEECH_RATE} 字约 {total / SPEECH_RATE:.1f} 分钟")
    else:
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
