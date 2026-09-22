"""Fixed syllabus knowledge points for Math I and 408 review analytics."""

from typing import Iterable

from .subjects import normalize_subject

UNCLASSIFIED = "待确认"


# Each tuple is (syllabus section, knowledge point). Points are intentionally
# specific enough for review, while remaining stable for long-term statistics.
SYLLABUS: dict[str, tuple[tuple[str, str], ...]] = {
    "math_calculus": (
        ("函数、极限、连续", "函数、极限、连续"), ("一元函数微分学", "导数与微分"),
        ("一元函数微分学", "中值定理与导数应用"), ("一元函数积分学", "不定积分"),
        ("一元函数积分学", "定积分及其应用"), ("向量代数和空间解析几何", "向量代数与空间解析几何"),
        ("多元函数微分学", "多元函数微分"), ("多元函数积分学", "二重积分"),
        ("多元函数积分学", "三重积分"), ("多元函数积分学", "曲线积分"), ("多元函数积分学", "曲面积分"),
        ("无穷级数", "无穷级数"), ("常微分方程", "常微分方程"),
    ),
    "math_linear": (
        ("行列式", "行列式"), ("矩阵", "矩阵"), ("向量", "向量组与线性相关"),
        ("线性方程组", "线性方程组"), ("矩阵的特征值和特征向量", "特征值与特征向量"),
        ("二次型", "二次型"),
    ),
    "math_probability": (
        ("随机事件和概率", "随机事件和概率"), ("随机变量及其分布", "一维随机变量及其分布"),
        ("多维随机变量及其分布", "二维随机变量及其分布"), ("随机变量的数字特征", "随机变量的数字特征"),
        ("大数定律和中心极限定理", "大数定律和中心极限定理"), ("数理统计", "样本及抽样分布"),
        ("数理统计", "参数估计"), ("数理统计", "假设检验"),
    ),
    "ds": (
        ("数据结构基本概念", "数据结构基本概念"), ("算法分析", "算法复杂度"), ("线性表", "线性表"), ("栈、队列和数组", "栈、队列和数组"),
        ("串", "串"), ("树与二叉树", "树与二叉树"), ("图", "图"), ("查找", "查找"), ("排序", "排序"),
    ),
    "coa": (
        ("计算机系统概述", "计算机系统概述"), ("数据的表示和运算", "数据表示与运算"),
        ("存储系统", "存储系统"), ("指令系统", "指令系统"), ("中央处理器", "中央处理器"),
        ("总线", "总线"), ("输入输出系统", "输入输出系统"),
    ),
    "os": (
        ("操作系统概述", "操作系统概述"), ("进程与线程", "进程与线程"), ("处理机调度", "处理机调度"),
        ("同步与互斥", "进程同步"), ("死锁", "死锁"), ("内存管理", "内存管理"),
        ("虚拟内存", "虚拟内存"), ("文件管理", "文件管理"), ("输入输出管理", "输入输出管理"),
    ),
    "cn": (
        ("计算机网络体系结构", "网络体系结构"), ("物理层", "物理层"), ("数据链路层", "数据链路层"),
        ("网络层", "网络层"), ("传输层", "传输层"), ("应用层", "应用层"),
    ),
}

_ALIASES = {
    "极限": "函数、极限、连续", "连续": "函数、极限、连续", "洛必达": "函数、极限、连续",
    "导数": "导数与微分", "微分": "导数与微分", "单调性": "中值定理与导数应用", "极值": "中值定理与导数应用",
    "不定积分": "不定积分", "定积分": "定积分及其应用", "二重积分": "二重积分", "三重积分": "三重积分",
    "曲线积分": "曲线积分", "曲面积分": "曲面积分", "级数": "无穷级数", "微分方程": "常微分方程",
    "偏导": "多元函数微分", "全微分": "多元函数微分", "多元函数": "多元函数微分", "空间解析几何": "向量代数与空间解析几何",
    "线性相关": "向量组与线性相关", "特征值": "特征值与特征向量", "特征向量": "特征值与特征向量",
    "逆矩阵": "矩阵", "伴随矩阵": "矩阵",
    "概率": "随机事件和概率", "一维随机变量": "一维随机变量及其分布", "二维随机变量": "二维随机变量及其分布",
    "期望": "随机变量的数字特征", "方差": "随机变量的数字特征", "抽样": "样本及抽样分布", "估计": "参数估计", "检验": "假设检验",
    "链表": "线性表", "栈": "栈、队列和数组", "队列": "栈、队列和数组", "二叉树": "树与二叉树", "图的遍历": "图", "最短路径": "图", "最小生成树": "图", "拓扑排序": "图", "查找": "查找", "排序": "排序", "复杂度": "算法复杂度",
    "缓存": "存储系统", "存储器": "存储系统", "指令": "指令系统", "流水线": "中央处理器", "cpu": "中央处理器", "总线": "总线", "io": "输入输出系统",
    "调度": "处理机调度", "同步": "进程同步", "互斥": "进程同步", "死锁": "死锁", "文件": "文件管理", "虚拟内存": "虚拟内存",
    "链路层": "数据链路层", "网络层": "网络层", "传输层": "传输层", "应用层": "应用层",
    "tcp": "传输层", "udp": "传输层", "http": "应用层", "dns": "应用层", "路由": "网络层", "子网": "网络层", "信道": "物理层", "编码与调制": "物理层",
}

# The stored knowledge point is intentionally stable for filtering and exams.
# The home page can use these narrower labels to make weak areas actionable
# without changing the canonical syllabus value in the database.
_DETAIL_RULES: dict[str, tuple[tuple[str, tuple[str, ...]], ...]] = {
    "函数、极限、连续": (
        ("洛必达法则", ("洛必达",)), ("极限", ("极限",)),
        ("连续性", ("连续",)), ("函数基础", ("函数",)),
    ),
    "导数与微分": (
        ("导数定义", ("导数定义", "导数的定义")), ("微分", ("微分",)),
        ("隐函数与参数方程", ("隐函数", "参数方程")), ("导数计算", ("导数",)),
    ),
    "中值定理与导数应用": (
        ("中值定理", ("中值定理", "罗尔", "拉格朗日定理", "柯西定理")),
        ("单调性与极值", ("单调性", "极值", "最值")), ("泰勒公式", ("泰勒",)),
    ),
    "定积分及其应用": (
        ("定积分计算", ("定积分", "积分计算")), ("定积分应用", ("面积", "体积", "弧长")),
    ),
    "二重积分": (
        ("极坐标变换", ("极坐标",)), ("积分次序交换", ("换序", "交换积分次序")),
        ("对称性", ("对称",)), ("直角坐标积分", ("直角坐标",)),
    ),
    "树与二叉树": (
        ("树的遍历", ("遍历", "先序", "中序", "后序", "层序")),
        ("平衡树", ("平衡", "avl", "红黑树")), ("堆与哈夫曼树", ("堆", "哈夫曼")),
    ),
    "图": (
        ("图的遍历", ("遍历", "深度优先", "广度优先", "dfs", "bfs")),
        ("最短路径", ("最短路径", "dijkstra", "floyd")),
        ("最小生成树", ("最小生成树", "prim", "kruskal")),
        ("拓扑排序", ("拓扑",)), ("关键路径", ("关键路径",)),
    ),
    "排序": (
        ("交换类排序", ("冒泡", "快速排序", "快排")), ("插入类排序", ("插入",)),
        ("选择类排序", ("选择", "堆排序")), ("归并与基数排序", ("归并", "基数")),
    ),
    "存储系统": (
        ("Cache", ("cache", "缓存")), ("虚拟存储", ("虚拟存储",)),
        ("主存与辅存", ("主存", "辅存", "存储器")),
    ),
    "进程同步": (
        ("信号量与锁", ("信号量", "锁")), ("管程", ("管程",)),
        ("生产者消费者", ("生产者", "消费者")),
    ),
    "网络层": (
        ("路由与转发", ("路由", "转发")), ("IP 与子网", ("ip", "子网", "子网掩码")),
        ("网络层协议", ("icmp", "ospf", "rip")),
    ),
    "传输层": (
        ("TCP", ("tcp",)), ("UDP", ("udp",)), ("可靠传输", ("可靠传输", "拥塞控制")),
    ),
    "不定积分": (
        ("换元积分", ("换元", "变量代换")), ("分部积分", ("分部积分",)),
        ("有理函数积分", ("有理函数",)), ("基本积分公式", ("积分公式", "原函数")),
    ),
    "多元函数微分": (
        ("偏导数", ("偏导", "偏微分")), ("全微分与链式法则", ("全微分", "链式法则")),
        ("多元函数极值", ("多元极值", "极值", "最值")),
    ),
    "三重积分": (
        ("柱坐标与球坐标", ("柱坐标", "球坐标")), ("积分区域与次序", ("换序", "区域")),
        ("三重积分对称性", ("对称",)),
    ),
    "无穷级数": (
        ("正项级数判敛", ("正项", "比较判别", "比值判别", "根值判别")),
        ("交错级数", ("交错", "莱布尼茨")), ("幂级数", ("幂级数", "收敛半径")),
        ("泰勒级数", ("泰勒级数", "展开")),
    ),
    "常微分方程": (
        ("一阶微分方程", ("一阶", "可分离", "一阶线性")),
        ("二阶及高阶方程", ("二阶", "高阶")), ("线性微分方程", ("线性微分", "常系数")),
    ),
    "行列式": (
        ("行列式计算", ("行列式计算", "展开", "化为三角形")),
        ("行列式性质", ("行列式性质",)), ("克拉默法则", ("克拉默",)),
    ),
    "矩阵": (
        ("矩阵运算", ("矩阵乘法", "矩阵运算")), ("逆矩阵", ("逆矩阵", "伴随矩阵")),
        ("矩阵的秩", ("矩阵的秩", "秩")), ("初等变换", ("初等变换", "阶梯形")),
    ),
    "线性方程组": (
        ("高斯消元", ("高斯消元", "消元", "阶梯形")), ("解的判定", ("有解", "无解", "解的结构")),
        ("齐次方程组", ("齐次",)),
    ),
    "特征值与特征向量": (
        ("特征值计算", ("特征多项式", "特征值")), ("特征向量", ("特征向量",)),
        ("相似对角化", ("相似", "对角化")),
    ),
    "随机事件和概率": (
        ("条件概率与独立性", ("条件概率", "独立")), ("全概率与贝叶斯", ("全概率", "贝叶斯")),
        ("事件运算", ("事件运算",)),
    ),
    "一维随机变量及其分布": (
        ("离散型随机变量", ("离散型", "分布列")), ("连续型随机变量", ("连续型", "密度")),
        ("常见分布", ("二项分布", "泊松", "正态分布", "均匀分布")),
    ),
    "随机变量的数字特征": (
        ("数学期望", ("数学期望", "期望")), ("方差与协方差", ("方差", "协方差")),
        ("相关系数", ("相关系数", "相关性")),
    ),
    "参数估计": (
        ("点估计", ("点估计", "矩估计", "最大似然")), ("区间估计", ("区间估计", "置信区间")),
    ),
    "假设检验": (
        ("假设检验基本概念", ("原假设", "显著性", "拒绝域")), ("正态总体检验", ("均值检验", "方差检验")),
    ),
    "算法复杂度": (
        ("时间复杂度", ("时间复杂度", "渐进")), ("空间复杂度", ("空间复杂度",)),
    ),
    "线性表": (
        ("顺序表", ("顺序表", "数组")), ("链表", ("链表", "单链表", "双链表", "循环链表")),
    ),
    "查找": (
        ("顺序与折半查找", ("顺序查找", "折半查找", "二分查找")),
        ("树表与散列查找", ("二叉排序树", "平衡树", "散列表", "哈希")),
    ),
    "进程与线程": (
        ("进程状态与切换", ("进程状态", "上下文切换")), ("线程与进程", ("线程",)),
    ),
    "处理机调度": (
        ("调度算法", ("先来先服务", "短作业", "优先级", "时间片", "调度算法")),
    ),
    "内存管理": (
        ("连续分配", ("连续分配", "分区")), ("分页与分段", ("分页", "分段")),
    ),
    "虚拟内存": (
        ("页面置换", ("页面置换", "fifo", "lru", "最佳置换")), ("地址转换与TLB", ("tlb", "地址转换")),
    ),
    "网络体系结构": (
        ("分层模型", ("osi", "tcp/ip", "分层")), ("协议与封装", ("封装", "协议")),
    ),
    "物理层": (
        ("信道与编码", ("信道", "编码", "奈奎斯特", "香农")), ("调制与复用", ("调制", "复用")),
    ),
    "数据链路层": (
        ("差错检测与纠正", ("crc", "奇偶校验", "差错")), ("介质访问控制", ("csma", "mac", "令牌")),
        ("滑动窗口与可靠传输", ("滑动窗口", "停止等待")),
    ),
    "应用层": (
        ("HTTP与Web", ("http", "web")), ("DNS", ("dns", "域名")),
        ("电子邮件与文件传输", ("smtp", "ftp", "邮件")),
    ),
}

_DETAIL_TAG_BLACKLIST = {
    "易错", "重点", "难点", "粗心", "待确认", "未知", "无",
    "概念理解", "方法选择", "计算与符号", "条件与边界", "审题与信息提取",
    "推理与论证", "记忆与规范", "代码与实现",
}


def syllabus_points(subject: str) -> tuple[str, ...]:
    return tuple(point for _, point in SYLLABUS.get(normalize_subject(subject), ()))


def syllabus_section(subject: str, point: str) -> str:
    for section, candidate in SYLLABUS.get(normalize_subject(subject), ()):
        if candidate == point:
            return section
    return ""


def primary_knowledge_point(subject: str, values: Iterable[object] | None, evidence: str = "") -> str:
    """Return one stable point from the selected subject's syllabus."""
    points = syllabus_points(subject)
    if not points:
        return UNCLASSIFIED
    if isinstance(values, str):
        values = [values]
    items = [str(value).strip() for value in values or () if str(value).strip()]
    # Preserve a deliberate pending classification rather than inventing a topic.
    if items == [UNCLASSIFIED]:
        return UNCLASSIFIED
    for item in items:
        if item in points:
            return item
    point_aliases = {point: point for point in points if len(point) > 1}
    # Specific phrases beat generic syllabus labels. This matters for legacy
    # rows whose chapter was inferred too broadly from a previous classifier.
    for text in (" ".join(items).lower(), evidence.lower()):
        for alias in sorted(_ALIASES, key=len, reverse=True):
            point = _ALIASES[alias]
            if alias.lower() in text and point in points:
                return point
        for alias in sorted(point_aliases, key=len, reverse=True):
            point = point_aliases[alias]
            if alias.lower() in text and point in points:
                return point
    return UNCLASSIFIED


def normalize_knowledge(data: dict) -> None:
    """Share one classification rule across AI, saved questions and migration."""
    point = primary_knowledge_point(
        data.get("subject", "unknown"), data.get("knowledge_points"),
        " ".join(str(data.get(key) or "") for key in ("chapter", "question_text", "question_latex", "correct_approach")),
    )
    data["knowledge_points"] = [point]
    data["chapter"] = syllabus_section(data.get("subject", "unknown"), point)


def detail_knowledge_point(subject: str, point: str, evidence: str = "", tags: Iterable[object] | None = None) -> str:
    """Return a narrower display label while keeping ``point`` as the filter key."""
    rules = _DETAIL_RULES.get(point)
    tag_values = [str(value).strip() for value in (tags or ()) if str(value).strip()]
    text = " ".join((*tag_values, evidence)).lower()
    if rules:
        for label, markers in rules:
            if any(marker.lower() in text for marker in markers):
                return label
    # Tags are deliberately used only as a display subdivision. The canonical
    # point remains unchanged, so filters and exams continue to use ``point``.
    for tag in tag_values:
        if tag not in _DETAIL_TAG_BLACKLIST and len(tag) <= 24 and tag not in {point}:
            return tag
    return point


def syllabus_catalog() -> dict[str, list[dict[str, str]]]:
    return {subject: [{"chapter": chapter, "name": point} for chapter, point in entries]
            for subject, entries in SYLLABUS.items()}


def syllabus_prompt() -> str:
    lines = []
    for subject, entries in SYLLABUS.items():
        points = "；".join(point for _, point in entries)
        lines.append(f"{subject}：{points}")
    return "\n".join(lines)
