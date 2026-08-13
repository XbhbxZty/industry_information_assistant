"""
数据源适配层（v0.7-B）

`register_all()` 是唯一的登记入口。适配器**必须**经它登记后才能产出
`structured_adapter` 身份——注册表是 v0.6a 闭集的守门人（BC-32）。

新增适配器时只需在 `_ADAPTERS` 里加一行；`apply_all()` 与评测会自动覆盖它。
"""
from typing import Any, Dict, List

try:
    from service.datasource.mock.relation_registry import RelationRegistryAdapter
except ImportError:  # 兼容以 app 为包根的导入方式
    from app.service.datasource.mock.relation_registry import RelationRegistryAdapter

# 当前启用的适配器。mock 实现默认启用，真实适配器需付费 Key，
# 按同一基类实现后加入此处即可。
_ADAPTERS = [RelationRegistryAdapter]

_INSTANCES: List[Any] = []


def register_all() -> List[Any]:
    """构造并登记全部适配器。重复调用安全（注册表按 id 覆盖）。"""
    global _INSTANCES
    if not _INSTANCES:
        _INSTANCES = [cls() for cls in _ADAPTERS]
    for a in _INSTANCES:
        a.register()
    return _INSTANCES


def apply_all(
    company: Dict[str, Any],
    field_checks: List[Dict[str, Any]],
    evidence_store: Dict[str, Dict],
) -> Dict[str, Dict[str, str]]:
    """
    依次施加全部适配器，返回 {adapter_id: {field_id: 结果}}。

    ⚠️ 顺序不代表优先级：每个适配器只处理**尚未核实**的字段，
    先跑的先落定。覆盖已有结论需要显式的证据替代授权（BC-40），
    "我后跑"不构成替代理由。
    """
    out: Dict[str, Dict[str, str]] = {}
    for a in register_all():
        out[a.adapter_id] = a.apply(company, field_checks, evidence_store)
    return out
