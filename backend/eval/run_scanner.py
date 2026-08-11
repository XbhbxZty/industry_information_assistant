"""
确定性扫描器评测（不调 LLM，秒级，零方差）

复用 critic_cases.json 的 28 个用例：注入组应被扫出，对照组应放行。
与 run_critic.py 的区别是这里的结果**完全确定**——同样的输入永远同样的输出，
因此可以进 CI、可以每次改动都跑。

用法：
    cd backend && python eval/run_scanner.py
    cd backend && python eval/run_scanner.py -v      # 打印每例细节
"""
import argparse
import json
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, "..", "app"))

from config.dd_checklist import build_field_checks  # noqa: E402
from service.claim_scanner import scan_report  # noqa: E402
from service.company_profile import fill_field_checks, profile_to_facts  # noqa: E402

EVAL_DATA = os.path.join(_HERE, "..", "app", "data", "companies_eval.json")
CASES = os.path.join(_HERE, "critic_cases.json")


def _load(p):
    with open(p, encoding="utf-8") as f:
        return json.load(f)


def _checks_for(company, section_id):
    checks = build_field_checks(checked_at="2026-08-10T00:00:00")
    fill_field_checks(company, profile_to_facts(company), checks)
    if section_id == "sec_8":
        return checks
    return [c for c in checks if c.get("section_id") == section_id]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args()

    data = _load(EVAL_DATA)
    cases = _load(CASES)
    companies = {c["company_id"]: c for c in data["companies"]}

    print("=" * 76)
    print("确定性扫描器评测（无 LLM，零方差）")
    print("=" * 76)

    # —— 注入组：应被扫出 ——
    print("\n注入组（应检出显式违规）")
    print("-" * 76)
    inj_hit = 0
    inj_miss = []
    for c in cases["injection_cases"]:
        company = companies[c["based_on"]]
        checks = _checks_for(company, c["section"])
        found = scan_report(checks, c["text"])
        # 用例数据显式声明由谁负责检测（scannable=false 的隐含断言交给 LLM）
        expected_scannable = c.get("scannable", True)
        hit = bool(found)
        if expected_scannable:
            inj_hit += 1 if hit else 0
            if not hit:
                inj_miss.append(c["id"])
        mark = "✓" if hit else ("—" if not expected_scannable else "✗")
        note = "" if expected_scannable else "（隐含类，交由 LLM）"
        print(f"  {mark} {c['id']}  {c['violation']:<28} {len(found)} 条{note}")
        if args.verbose and found:
            for f in found[:2]:
                print(f"        {f['field_name']}｜命中「{f['matched_claim']}」｜{f['sentence'][:46]}")

    scannable_total = sum(1 for c in cases["injection_cases"] if c.get("scannable", True))

    # —— 对照组：应放行 ——
    print("\n对照组（表述正确，应零误报）")
    print("-" * 76)
    fp_cases = []
    for c in cases["clean_cases"]:
        company = companies[c["based_on"]]
        checks = _checks_for(company, c["section"])
        found = scan_report(checks, c["text"])
        if found:
            fp_cases.append(c["id"])
            print(f"  ✗ {c['id']}  误报 {len(found)} 条  {c['exercises'][:40]}")
            for f in found[:2]:
                print(f"        {f['field_name']}｜命中「{f['matched_claim']}」｜{f['sentence'][:52]}")
        else:
            print(f"  ✓ {c['id']}  {c['exercises'][:52]}")

    n_clean = len(cases["clean_cases"])
    print("\n" + "=" * 76)
    print("汇总")
    print("=" * 76)
    print(f"  显式违规检出   {inj_hit}/{scannable_total}"
          + (f"  漏检：{', '.join(inj_miss)}" if inj_miss else ""))
    print(f"  对照组误报     {len(fp_cases)}/{n_clean}"
          + (f"  误报：{', '.join(fp_cases)}" if fp_cases else ""))
    ok = inj_hit == scannable_total and not fp_cases
    print(f"\n结果：{'通过' if ok else '未通过'}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
