# Copyright © 2026 XbhbxZty
# 本文件为「尽调智核」迭代中新增，不含原课程项目代码。
import json
import re
from pathlib import Path


BACKEND_DIR = Path(__file__).resolve().parents[1]
CASES_PATH = BACKEND_DIR / "eval" / "critic_holdout_v2.json"
COMPANIES_PATH = BACKEND_DIR / "app" / "data" / "companies_eval.json"


def _load_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def test_holdout_v2_top_level_structure():
    data = _load_json(CASES_PATH)

    assert set(data) == {"_meta", "injection_cases", "clean_cases"}
    assert isinstance(data["_meta"], dict)
    assert len(data["injection_cases"]) >= 10
    assert len(data["clean_cases"]) >= 12


def test_holdout_v2_case_structure_and_unique_ids():
    data = _load_json(CASES_PATH)
    injection_cases = data["injection_cases"]
    clean_cases = data["clean_cases"]

    for case in injection_cases:
        assert {"id", "based_on", "section", "violation", "text"} <= set(case)
        assert re.fullmatch(r"BLD2-INJ-\d{2}", case["id"])
        assert all(isinstance(case[key], str) and case[key] for key in (
            "based_on", "section", "violation", "text"
        ))

    for case in clean_cases:
        assert {"id", "based_on", "section", "text"} <= set(case)
        assert re.fullmatch(r"BLD2-CLN-\d{2}", case["id"])
        assert all(isinstance(case[key], str) and case[key] for key in (
            "based_on", "section", "text"
        ))

    ids = [case["id"] for case in injection_cases + clean_cases]
    assert len(ids) == len(set(ids))


def test_holdout_v2_company_references_are_valid():
    cases = _load_json(CASES_PATH)
    companies = _load_json(COMPANIES_PATH)
    valid_company_ids = {company["company_id"] for company in companies["companies"]}

    referenced_ids = {
        case["based_on"]
        for case in cases["injection_cases"] + cases["clean_cases"]
    }
    assert referenced_ids <= valid_company_ids
