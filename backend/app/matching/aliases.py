import re
import unicodedata


DOMAIN_ALIAS_GROUPS: tuple[tuple[str, ...], ...] = (
    ("赫茨伯格双因素理论", "双因素理论", "激励-保健理论", "激励保健理论", "保健因素", "激励因素"),
    ("X-Y理论", "X理论与Y理论", "X理论", "Y理论", "麦格雷戈理论", "麦格雷戈X理论与Y理论"),
    ("工作说明书", "职位说明书", "岗位说明书", "职务说明书"),
    ("招募", "招聘", "人员招募"),
    ("甄选", "选拔", "人员甄选"),
    ("录用", "人员录用"),
    ("问卷法", "问卷调查法", "职位分析问卷法"),
    ("德尔菲法", "专家预测法", "专家意见法"),
    ("经验判断法", "管理人员判断法", "主观判断法"),
    ("内部人力资源供给预测", "内部供给预测", "内部供给"),
    ("外部人力资源供给预测", "外部供给预测", "外部供给"),
    ("人力资源供给总量", "总供给", "供需平衡"),
    ("人力资源管理发展历程", "人力资源管理发展阶段", "人事管理发展", "行业发展简史"),
    ("6W1H", "六何分析法", "六何分析"),
)


TOPIC_QUERY_EXPANSIONS: tuple[tuple[tuple[str, ...], tuple[str, ...]], ...] = (
    (("内部供给", "外部供给", "总供给"), (
        "内部人力资源供给预测",
        "外部人力资源供给预测",
        "人力资源供给总量 供需平衡计算",
    )),
    (("德尔菲", "经验判断"), ("德尔菲法 专家预测法", "经验判断法 主观判断法")),
    (("工作说明书", "6w1h"), ("工作说明书 职位说明书 岗位说明书", "6W1H 六何分析")),
    (("招募", "甄选", "人员配置"), ("员工招募 招聘", "员工甄选 选拔", "人员配置 录用 人岗匹配")),
    (("转化率", "录用率", "招聘成本"), ("招聘转化率", "人员录用率", "招聘成本效用 招聘评估")),
    (("问卷法", "基础方法"), ("问卷调查法", "访谈法", "观察法", "工作日志法", "关键事件法")),
    (("各模块", "核心考点"), (
        "人力资源规划",
        "员工招聘 招募甄选",
        "员工培训 培训开发",
        "绩效管理",
        "薪酬管理",
        "员工关系 劳动关系",
    )),
    (("经典", "核心理论"), (
        "泰勒科学管理理论",
        "马斯洛需求层次理论",
        "麦格雷戈X理论与Y理论",
        "赫茨伯格双因素理论",
    )),
    (("行业发展简史",), ("人力资源管理发展历程 人事管理 科学管理 人际关系运动 战略性人力资源管理",)),
)


def canonicalize_query(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value)
    normalized = re.sub(r"[‐‑‒–—―−﹣－]+", "-", normalized)
    normalized = normalized.replace("／", "/")
    return re.sub(r"\s+", " ", normalized).strip()


def normalize_query_key(value: str) -> str:
    return re.sub(r"[^\w\u4e00-\u9fff]+", "", canonicalize_query(value), flags=re.UNICODE).casefold()


def alias_queries(value: str) -> tuple[str, ...]:
    key = normalize_query_key(value)
    expanded: list[str] = []
    for group in DOMAIN_ALIAS_GROUPS:
        if any(normalize_query_key(alias) in key or key in normalize_query_key(alias) for alias in group if key):
            expanded.extend(group)
    return _unique(expanded)


def configured_subqueries(value: str) -> tuple[str, ...]:
    key = normalize_query_key(value)
    expanded: list[str] = []
    for required_terms, queries in TOPIC_QUERY_EXPANSIONS:
        if all(normalize_query_key(term) in key for term in required_terms):
            expanded.extend(queries)
    return _unique(expanded)


def _unique(values: list[str]) -> tuple[str, ...]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        cleaned = canonicalize_query(value)
        key = normalize_query_key(cleaned)
        if not key or key in seen:
            continue
        seen.add(key)
        result.append(cleaned)
    return tuple(result)
