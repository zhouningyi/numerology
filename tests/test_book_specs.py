"""各书现代规格（book_specs.yaml）完整性测试。"""

from pathlib import Path

import yaml

from process_canon_layers import BOOKS

SPECS_PATH = Path("numerology/canon/book_specs.yaml")


def _specs() -> dict:
    return yaml.safe_load(SPECS_PATH.read_text(encoding="utf-8"))["specs"]


def test_every_registered_book_has_a_spec():
    specs = _specs()
    missing = [book for book in BOOKS if book not in specs]
    assert not missing, f"缺 spec 的书目：{missing}"


def test_specs_have_required_fields_and_are_concise():
    for slug, spec in _specs().items():
        assert spec.get("tier"), f"{slug} 缺 tier"
        assert spec.get("spec"), f"{slug} 缺 spec 一句话"
        assert spec.get("validation"), f"{slug} 缺可验证性说明"
        # 言简意赅：一句话规格不超过 90 字
        assert len(spec["spec"]) <= 90, f"{slug} 的 spec 过长（{len(spec['spec'])} 字）"
