import re
from collections.abc import Iterable


_QUANTITATIVE_DATA_PATTERN = re.compile(
    r"(?:"
    r"\d[\d,\.]*\s*(?:万亿元|亿元|万元|元|万户|户|万家|家|万人次|人次|万人|人|万笔|笔|万单|单|次|%|个百分点|倍|项)"
    r"|"
    r"(?:同比|增长|下降|提升|减少|新增|累计|服务|覆盖|支持|授信|贷款|融资|发放|交易|撮合|落地|办理|申请|触达|惠及)"
    r"[^。；\n]{0,12}\d[\d,\.]*"
    r")"
)


def source_materials_have_quantitative_data(materials: Iterable[object]) -> bool:
    for item in materials:
        if not isinstance(item, dict):
            continue
        title = str(item.get("title", "") or "")
        text = str(item.get("text", "") or "")
        if _QUANTITATIVE_DATA_PATTERN.search(f"{title}\n{text}"):
            return True
    return False
