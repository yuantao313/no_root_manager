"""根据姓名生成候选用户名。

规则：
- 单姓（如张三丰）：zhangsanfeng / sanfengzhang / zhangsf / sfzhang
  （姓全拼+名全拼、名全拼+姓全拼、姓全拼+名首字母、名首字母+姓全拼）
- 复姓（如诸葛孔明）：zhugekongming / kongmingzhuge / zhugekm / kmzhuge
- 复姓姓名同时提供单姓拆分（姓取首字，名取其余），不强制判定为复姓
- 多音字按全部读音做排列组合
"""

from itertools import product

from pypinyin import Style, pinyin

# 常见复姓（前两字命中时额外按复姓生成，同时仍提供单姓情况）
COMPOUND_SURNAMES = {
    "欧阳",
    "太史",
    "端木",
    "上官",
    "司马",
    "东方",
    "独孤",
    "南宫",
    "万俟",
    "闻人",
    "夏侯",
    "诸葛",
    "尉迟",
    "公羊",
    "赫连",
    "澹台",
    "皇甫",
    "宗政",
    "濮阳",
    "公冶",
    "太叔",
    "申屠",
    "公孙",
    "慕容",
    "仲孙",
    "钟离",
    "长孙",
    "宇文",
    "司徒",
    "鲜于",
    "司空",
    "闾丘",
    "子车",
    "亓官",
    "司寇",
    "巫马",
    "公西",
    "颛孙",
    "壤驷",
    "公良",
    "漆雕",
    "乐正",
    "宰父",
    "谷梁",
    "拓跋",
    "夹谷",
    "轩辕",
    "令狐",
    "段干",
    "百里",
    "呼延",
    "东郭",
    "南门",
    "羊舌",
    "微生",
    "梁丘",
    "西门",
    "左丘",
    "即墨",
    "第五",
}


def _pinyin_choices(char: str) -> list[str]:
    """返回单字的所有读音（含多音字）。"""
    return pinyin(char, heteronym=True, style=Style.NORMAL)[0]


def _join_all(parts: list[list[str]]) -> list[str]:
    """对每个字的所有读音做笛卡尔积拼接，返回去重后的全拼列表。"""
    if not parts:
        return [""]
    return ["".join(combo) for combo in product(*parts)]


def _initials(parts: list[list[str]]) -> list[str]:
    """对每个字的所有读音取首字母（去重排序）再笛卡尔积，返回首字母串列表。"""
    if not parts:
        return [""]
    letters = ["".join(sorted({p[0] for p in ps})) for ps in parts]
    return ["".join(combo) for combo in product(*letters)]


def _generate(surname: str, given: str) -> list[str]:
    """给定姓与名生成四类组合（含多音字排列）。"""
    results = []
    sur_full = _join_all([_pinyin_choices(c) for c in surname])
    giv_full = _join_all([_pinyin_choices(c) for c in given])
    giv_init = _initials([_pinyin_choices(c) for c in given])

    for s in sur_full:
        for g in giv_full:
            results.append(s + g)  # 姓全拼 + 名全拼
            results.append(g + s)  # 名全拼 + 姓全拼
        for gi in giv_init:
            results.append(s + gi)  # 姓全拼 + 名首字母
            results.append(gi + s)  # 名首字母 + 姓全拼
    return results


def split_name(name: str) -> tuple[str, str, bool]:
    """拆分为 (姓, 名, 是否复姓)。名字 >=3 字且前两字命中复姓库时按复姓处理。"""
    name = name.strip()
    if not name:
        return "", "", False
    if len(name) >= 3 and name[:2] in COMPOUND_SURNAMES:
        return name[:2], name[2:], True
    return name[:1], name[1:], False


def generate_usernames(name: str) -> list[str]:
    """生成候选用户名列表（去重、保持顺序）。

    单姓拆分（姓=首字，名=其余）总是提供；姓名前两字命中复姓库时额外提供
    复姓拆分（姓=前两字，名=其余），两者并列返回，不强制判定为复姓。
    """
    groups = generate_username_groups(name)
    return groups["suggestions"]


def _english_username(name: str) -> str:
    """英文名用户名规则：纯英文+空格时 name_words[0][0] + name_words[-1] 小写。

    如 "John Smith" -> "js"；"Alice Bob Carol" -> "acarol"。
    """
    words = [w for w in name.split(" ") if w]
    if not words:
        return ""
    return (words[0][0] + words[-1]).lower()


def generate_username_groups(name: str) -> dict:
    """返回分组结果，供接口使用。

    返回结构：
    {
        "name": 原姓名,
        "is_compound_surname": 是否命中复姓库,
        "single_surname": 单姓拆分建议列表,
        "compound_surname": 复姓拆分建议列表（非复姓时为空列表）,
        "suggestions": 两者去重合并后的全部建议,
    }
    """
    name = name.strip()
    empty = {
        "name": name,
        "is_compound_surname": False,
        "single_surname": [],
        "compound_surname": [],
        "suggestions": [],
    }
    if not name:
        return empty

    # 英文名（纯 ASCII 字母 + 空格）：用英文规则，不走拼音
    if all(ch.isascii() and (ch.isalpha() or ch == " ") for ch in name):
        eng = _english_username(name)
        if eng:
            return {
                "name": name,
                "is_compound_surname": False,
                "single_surname": [eng],
                "compound_surname": [],
                "suggestions": [eng],
            }

    # 单姓拆分（对复姓姓名即"姓取首字"的单姓情况）
    single = list(dict.fromkeys(r for r in _generate(name[0], name[1:]) if r))

    # 复姓拆分（额外提供）
    compound = []
    is_compound = len(name) >= 3 and name[:2] in COMPOUND_SURNAMES
    if is_compound:
        compound = list(dict.fromkeys(r for r in _generate(name[:2], name[2:]) if r))

    return {
        "name": name,
        "is_compound_surname": is_compound,
        "single_surname": single,
        "compound_surname": compound,
        "suggestions": list(dict.fromkeys(single + compound)),
    }
