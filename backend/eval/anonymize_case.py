# Copyright © 2026 XbhbxZty
# 本文件为「尽调智核」迭代中新增，不含原课程项目代码。
"""把真实案例包改名成虚构主体，保留全部数据结构与数值

## 为什么要做这件事

十二个案例包是真实上市公司的公开材料，数据真实、结构完整、行文自然——
比任何手写的虚构语料都像真材料。但要验「数据充分时的报告质量与决策效果」，
必须补上语料里没有的司法/工商/招投标数据，而那部分只能是编的。

**给真实、具名的上市公司编造司法记录，然后产出一份写着「建议授信 X 万元」
的报告，是不能做的。** 改名之后这个问题消失：编造的记录属于一个虚构主体。

## 改名必须彻底，否则比不改更糟

只改主体名、留着关联方与人名，会得到一个**看起来虚构、实际一眼可辨**的
公司，而它现在还挂着编造的司法记录。所以：

- 词根替换（`康美` → `晟康`）自动覆盖绝大多数变体：
  `康美药业股份有限公司`、`康美实业投资控股有限公司`、
  `广东康美新澳医药有限公司` 一次全改
- 股票代码、统一社会信用代码、法定代表人、控股股东、
  高频关联方，逐个列出替换
- **替换完扫残留，非零就拒绝写出**

## 做不到的部分要说出来

行业与地域信息保留（玻璃制造、医药流通、零售）。完全去标识做不到，
也不是目标——目标是**不把编造的记录挂到一个真实具名主体上**。
产出物在性质上等同于一份匿名化的教学案例。

用法：
    python eval/anonymize_case.py --case case_11 --dry-run
    python eval/anonymize_case.py --all
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Tuple

BACKEND = Path(__file__).resolve().parents[1]
CASES = BACKEND / "eval" / "real_cases_processed"
OUT_ROOT = BACKEND / "eval" / "anonymized_cases"

#: 每个案例的替换表。**顺序有意义**——长串必须排在其子串前面，
#: 否则 `康美` 先命中会把 `康美药业股份有限公司` 拆成半改的样子。
#: 词根放最后兜底。
SUBSTITUTIONS: Dict[str, Dict[str, Any]] = {
    "case_04": {
        # 浙江鼎力机械 —— 健康的中型制造企业，比大盘股更接近真实授信客户
        "fictional_name": "浙江骏昇机械股份有限公司",
        "fictional_short": "骏昇机械",
        "credit_code": "91330500MA2FIC0004",
        "pairs": [
            ("ZHEJIANG DINGLI", "ZHEJIANG JUNSHENG"),
            ("DINGLI", "JUNSHENG"),
            ("dingli", "junsheng"),
            ("Dingli", "Junsheng"),
            ("CMEC", "OVMC"),
            ("Magni", "Marnи"),
            ("上海鼎策融资租赁有限公司", "上海骏策融资租赁有限公司"),
            ("鼎策", "骏策"),
            ("浙江绿色动力机械有限公司", "浙江青能动力机械有限公司"),
            ("cndingli", "cnjunsheng"),
            ("许树根", "许树声"),
            ("603338", "603004"),
            ("德清", "临溪"),          # 小县城，与行业组合具识别力
            ("鼎力", "骏昇"),          # 字号兜底，保留其余结构让别名可派生
        ],
    },
    "case_12": {
        # 江苏中利 —— 高难，真实的困境样本
        "fictional_name": "江苏泓瑞集团股份有限公司",
        "fictional_short": "泓瑞集团",
        "credit_code": "913205007317610000",
        # 词根派生会把「船用电缆」当成标识串，但那是**产品品类**不是公司名。
        # 与 GENERIC_CORPORATE_WORDS 同一性质的放行，只是它按案例而定。
        "generic_terms": ["船用电缆", "光伏技术"],
        "pairs": [
            ("Talesun", "Tengyu"),
            ("TALESUN", "TENGYU"),
            ("zhongli", "hongrui"),
            ("ZHONGLI", "HONGRUI"),
            ("Zhongli", "Hongrui"),
            ("苏州腾晖光伏技术有限公司", "苏州腾煜光伏技术有限公司"),
            ("常州船用电缆有限责任公司", "常州舟缆电气有限责任公司"),
            ("中利科技集团股份有限公司", "泓瑞科技集团股份有限公司"),
            ("913205007317618904", "913205007317610000"),
            ("王柏兴", "王柏成"),
            ("王伟峰", "王伟锋"),
            ("002309", "002003"),
            ("常熟", "澄江"),
            ("腾晖", "腾煜"),
            ("中利", "泓瑞"),
        ],
    },
    "case_11": {
        "fictional_name": "晟康药业股份有限公司",
        "fictional_short": "晟康药业",
        "credit_code": "91440000MA2FIC0011",
        "pairs": [
            ("Kangmei Pharmaceutical", "Shengkang Pharmaceutical"),
            ("kangmei", "shengkang"),
            ("Kangmei", "Shengkang"),
            ("KANGMEI", "SHENGKANG"),
            ("KMYY", "SKYY"),
            ("揭阳", "岐阳"),
            ("大地参", "沃土参"),
            ("普宁", "樟宁"),
            ("亳州", "淮陵"),
            ("神农氏", "神稷"),
            ("大地参业", "沃土参业"),
            ("新澳", "新奥"),
            ("康美实业投资控股有限公司", "晟康实业投资控股有限公司"),
            ("广东康美新澳医药有限公司", "广东晟康新澳医药有限公司"),
            ("人保康美（北京）健康科技股份有限公司", "人保晟康（北京）健康科技股份有限公司"),
            ("新华康美健康智库股份有限公司", "新华晟康健康智库股份有限公司"),
            ("广东神农氏企业管理合伙企业", "广东神稷企业管理合伙企业"),
            ("康美药业股份有限公司", "晟康药业股份有限公司"),
            ("集安大地参业有限公司", "集安沃土参业有限公司"),
            ("赖志坚", "赖志强"),
            ("马兴田", "马兴发"),
            ("600518", "600002"),
            ("康美", "晟康"),
        ],
    },
}


#: 公司形式的通用词。它们不是主体标识，扫到了也不算残留。
#:
#: 不排除它们，正则会把「…集团股份有限公司」拆成两段，
#: 后半段「股份」+「有限公司」被当成一个实体名，于是每份年报
#: 都报 200+ 条假残留——**恒亮的告警灯不是灯**，真残留会跟着被忽略。
#: （本项目第六次守卫误伤。）
#: 监管机构、交易所、公开信息平台的**光杆域名**。它们不标识任何主体——
#: 一份年报引用上交所是常态。带路径的完整 URL 仍然会被改写（路径才泄露），
#: 这里只放行没有路径的裸域名。
#:
#: 不放行的后果与「股份有限公司」那次一样：每份语料都报几十条假残留，
#: 检查变成恒亮的灯，真残留跟着被忽略。
NON_IDENTIFYING_HOSTS = {
    "www.sse.com.cn", "www.szse.cn", "www.cninfo.com.cn",
    "static.cninfo.com.cn", "www.csrc.gov.cn", "www.hkexnews.hk",
    "www.court.gov.cn", "www.gsxt.gov.cn", "www.creditchina.gov.cn",
    # 财经媒体：报道主体不等于标识主体，一份年报引用证券时报是常态
    "www.cnstock.com", "cnstock.com", "www.stcn.com", "stcn.com",
    "www.sina.com.cn", "finance.sina.com.cn", "www.eastmoney.com",
}


GENERIC_CORPORATE_WORDS = {
    "股份有限公司", "有限责任公司", "有限公司", "集团", "合伙企业",
    "股份公司", "控股集团", "投资集团", "本公司", "子公司", "母公司",
    "分公司", "公司集团",
}


def _is_generic(name: str) -> bool:
    """通用词，或去掉公司形式后剩不下东西的，都不是标识串。"""
    if name in GENERIC_CORPORATE_WORDS:
        return True
    stem = name
    for suffix in ("股份有限公司", "有限责任公司", "有限公司",
                   "合伙企业", "集团"):
        if stem.endswith(suffix):
            stem = stem[: -len(suffix)]
            break
    stem = stem.strip("（）() 　")
    # 剩下不足两个字，或本身还是公司形式用词 → 不具备识别力
    return len(stem) < 2 or stem in {"股份", "有限", "责任", "本", "该", "公司"}


def _manifest_identifiers(case_id: str) -> List[str]:
    """从 manifest 里把主体标识串全捞出来，作为残留检查的额外靶子。

    只靠手写的替换表会漏——manifest 里的字段是另一份独立记录，
    拿它做交叉检查，比我自己回忆全不全可靠。
    """
    path = CASES / case_id / "manifest.json"
    if not path.exists():
        return []
    blob = json.dumps(json.loads(path.read_text(encoding="utf-8")),
                      ensure_ascii=False)
    out = set()
    for m in re.findall(r"[一-龥]{2,20}?(?:股份有限公司|有限责任公司|"
                        r"有限公司|集团|合伙企业)", blob):
        if not _is_generic(m):
            out.add(m)
    for m in re.findall(r'"(?:stock_code|A_share_code|H_share_code|'
                        r'a_share_code|h_share_code)"\s*:\s*"([0-9]{4,6})"', blob):
        out.add(m)
    return sorted(out)


#: 正文里出现的 URL 一律改写成这个存档域。
#:
#: ⚠️ 不做「域名映射」而做整体改写：**路径本身也泄露**——
#: `money.finance.sina.com.cn/...?id=8152984` 里的 id 就是那份
#: 真实公告的编号，只换域名等于没换。
FICTIONAL_ARCHIVE = "https://archive.example"

_URL_RE = re.compile(r"https?://[^\s\u4e00-\u9fa5）)】\]，,；;\"']+")


def rewrite_urls(text: str, case_id: str) -> str:
    """把每个 URL 换成稳定的虚构存档地址。

    用原 URL 的哈希做后缀，保证**不同来源仍然是不同 URL**——
    全部换成同一个地址会让证据附录里的来源互相塌缩，
    那等于伪造了「多个来源相互印证」。
    """
    def repl(m: "re.Match[str]") -> str:
        digest = hashlib.sha1(m.group(0).encode("utf-8")).hexdigest()[:12]
        return f"{FICTIONAL_ARCHIVE}/{case_id}/{digest}"

    return _URL_RE.sub(repl, text)


#: 地名前缀与公司形式后缀，用于从全称里剥出判别性词根。
_GEO_PREFIX = re.compile(
    r"^(?:北京|上海|天津|重庆|广东|广州|深圳|江苏|南京|苏州|浙江|杭州|"
    r"福建|福州|厦门|山东|青岛|河南|湖北|武汉|湖南|长沙|四川|成都|陕西|"
    r"西安|安徽|合肥|江西|云南|贵州|广西|河北|山西|辽宁|吉林|黑龙江|"
    r"内蒙古|新疆|甘肃|宁夏|青海|西藏|海南|集安|曲靖|常州|东莞)(?:省|市)?")

_CORP_SUFFIX = re.compile(
    r"(?:股份有限公司|有限责任公司|有限公司|企业管理合伙企业|合伙企业|"
    r"企业管理|投资控股|控股集团|集团|公司)$")


def distinctive_stem(name: str) -> str:
    """从全称里剥出判别性词根。

    `杭州灏月企业管理有限公司` → `灏月`

    没有这一步，残留检查就只能发现替换表里已经列出来的那些串——
    而漏写的恰恰是没列出来的变体（实测漏了 5 种）。
    """
    stem = name
    for _ in range(3):                       # 后缀可能叠加
        new = _CORP_SUFFIX.sub("", stem)
        if new == stem:
            break
        stem = new
    stem = _GEO_PREFIX.sub("", stem)
    return stem.strip("（）() 　")


def apply_pairs(text: str, pairs: List[Tuple[str, str]]) -> str:
    for old, new in pairs:
        text = text.replace(old, new)
    return text


def residue_check(texts: List[str], case_id: str,
                  pairs: List[Tuple[str, str]]) -> Dict[str, int]:
    """扫残留。**非零就不许写出**。

    检查两组靶子：替换表左侧的原串，以及 manifest 里独立记录的标识串。
    后者是为了抓「我没想到要替换的那些」。
    """
    blob = "\n".join(texts)
    targets = {old for old, _ in pairs}
    targets |= set(_manifest_identifiers(case_id))
    # ⚠️ 域名维度。只扫中文实体名与股票代码时，`www.fuyaogroup.com`
    #    × 460 与 `www.suning.cn` × 374 整个维度都在检查范围之外——
    #    而 URL 是最直接的再识别通道。
    #    任何非虚构存档域的 http(s) 地址都算残留。
    for url in _URL_RE.findall(blob):
        if not url.startswith(FICTIONAL_ARCHIVE):
            targets.add(url.split("?")[0][:80])
    # 邮箱：`kangmei@kangmei.com.cn` 里的 local part 与域名都带主体标识，
    # 而它既不是 URL（无 scheme）也不是 www. 开头 —— 实测整类漏网。
    for mail in re.findall(r"[A-Za-z0-9._%+\-]{2,40}@[A-Za-z0-9.\-]{3,40}", blob):
        targets.add(mail)
    # 裸域名（不限 www. 前缀）。`kangmei.com.cn`、`zhongli.com` 同样漏过。
    for dom in re.findall(
            r"(?<![/@\w.])[A-Za-z0-9\-]{3,30}\.(?:com|cn|net|org|hk)"
            r"(?:\.[a-z]{2,3})?", blob):
        label = dom.split(".")[0].lower()
        # `www.cn` 是 PDF 把 `www.cnstock.com` 断开后的碎片：
        # label 恰好是 www/http 这类前缀词，不具识别力。
        if label in {"www", "http", "https", "com", "cn", "net"}:
            continue
        if not any(dom == h or h.endswith(dom) or dom.endswith(h.replace("www.", ""))
                   for h in NON_IDENTIFYING_HOSTS):
            targets.add(dom)
    # 裸域名：`www.fuyaogroup.com` 没有 http:// 前缀，URL 正则不匹配，
    # 实测漏了 12 处。
    for host in re.findall(r"(?<![/\w.])www\.[A-Za-z0-9.\-]{3,60}", blob):
        bare = host.rstrip(".")
        # PDF 会把 `www.cnstock.com` 断成 `www.cn stock.com`，截出的
        # `www.cn` 是碎片而非域名——不过滤就每份语料报一批假残留。
        if len(bare) - len("www.") < 4:
            continue
        # PDF 抽取会把域名断开（`www.cninfo. com.cn`），截出的片段不等于
        # 放行表里的整串。用前缀匹配，否则每份语料都报一批假残留。
        if any(bare == h or h.startswith(bare) for h in NON_IDENTIFYING_HOSTS):
            continue
        targets.add(bare)
    # ⚠️ 自动派生词根。只查替换表里列出的全称，就只能发现我已经想到的
    #    那部分——实测「杭州灏月企业管理有限公司」被换掉了，
    #    而光杆的「灏月」还在正文里，检查却报「残留为零」。
    for old, _new in pairs:
        stem = distinctive_stem(old)
        if len(stem) >= 2 and stem not in {new for _, new in pairs}:
            targets.add(stem)
    # 放行：本案例声明的通用行业词（词根派生会撞上产品品类），
    # 以及**替换后的新串本身**——把自己的替换结果报成残留是纯噪声。
    spec = SUBSTITUTIONS.get(case_id) or {}
    targets -= set(spec.get("generic_terms") or [])
    for _old, new in pairs:
        targets = {t for t in targets if t not in new and new not in t}
    # 替换后的新串本身不算残留
    targets -= {new for _, new in pairs}
    return {t: blob.count(t) for t in sorted(targets) if blob.count(t) > 0}


def run_case(case_id: str, dry: bool) -> int:
    spec = SUBSTITUTIONS.get(case_id)
    if not spec:
        print(f"⛔ {case_id} 没有替换表，跳过")
        return 1
    src = CASES / case_id / "corpus" / "chunks.jsonl"
    if not src.exists():
        print(f"⛔ {case_id} 找不到 chunks.jsonl")
        return 1

    rows = [json.loads(l) for l in src.read_text(encoding="utf-8").splitlines()
            if l.strip()]
    pairs = spec["pairs"]

    before = [str(r.get("content") or "") for r in rows]
    after = [rewrite_urls(apply_pairs(t, pairs), case_id) for t in before]
    changed = sum(1 for a, b in zip(before, after) if a != b)

    print(f"=== {case_id} → {spec['fictional_name']}")
    print(f"    分片 {len(rows)}，其中 {changed} 片被改写 "
          f"({changed / max(1, len(rows)):.0%})")

    residue = residue_check(after, case_id, pairs)
    if residue:
        print(f"    ⛔ **残留 {len(residue)} 种**，不予写出：")
        for k, v in list(residue.items())[:12]:
            print(f"         {k!r} × {v}")
        print(f"    → 把这些补进 SUBSTITUTIONS['{case_id}']['pairs'] 再跑")
        return 2

    print("    ✅ 残留为零")
    if dry:
        print("    （--dry-run：不写出）")
        return 0

    out_dir = OUT_ROOT / case_id
    out_dir.mkdir(parents=True, exist_ok=True)
    with (out_dir / "chunks.jsonl").open("w", encoding="utf-8") as fh:
        for row, text in zip(rows, after):
            row = dict(row)
            row["content"] = text
            # 每片都带虚构标记——与既有 mock 语料同一纪律，
            # 让「这不是真实主体的材料」在分片级别就可见。
            row["fictional"] = True
            row["fictional_subject"] = spec["fictional_name"]
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")

    (out_dir / "anonymization.json").write_text(json.dumps({
        "source_case": case_id,
        "fictional_name": spec["fictional_name"],
        "fictional_short": spec["fictional_short"],
        "credit_code": spec["credit_code"],
        "chunks": len(rows),
        "chunks_changed": changed,
        "substitutions": [{"from": a, "to": b} for a, b in pairs],
        "note": ("真实上市公司公开材料改名而来。数值与结构保留，"
                 "主体标识全部替换。行业与地域信息保留——完全去标识"
                 "做不到，也不是目标；目标是不把编造的司法/工商记录"
                 "挂到一个真实具名主体上。"),
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"    → 写出 {out_dir}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--case", default="")
    parser.add_argument("--all", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    ids = sorted(SUBSTITUTIONS) if args.all else ([args.case] if args.case else [])
    if not ids:
        print("指定 --case 或 --all")
        return 1
    rc = 0
    for cid in ids:
        rc = run_case(cid, args.dry_run) or rc
        print()
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
