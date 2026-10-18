from dataclasses import dataclass, field

import pytest

from app.rules import describe, resolve, validate_payload


@dataclass
class R:
    kind: str
    payload: dict
    scope: str = "global"
    category: str | None = None
    hardness: str = "soft"
    source: str = "user"
    confidence: float = 1.0
    active: bool = True
    id: int | None = None


_ids = iter(range(1, 10_000))


def rule(**kw) -> R:
    return R(id=next(_ids), **kw)


def test_category_rule_beats_global():
    rules = [rule(kind="dietary", payload={"tags": ["VEGAN"]}),
             rule(kind="dietary", payload={"tags": ["LAC_FREE"]}, category="leite")]
    assert resolve(rules, "leite", 1).dietary == ["LAC_FREE"]
    assert resolve(rules, "cafe", 1).dietary == ["VEGAN"]


def test_list_scope_beats_category_global():
    rules = [rule(kind="brand_chain", payload={"order": ["Ypê"]}, category="detergente"),
             rule(kind="brand_chain", payload={"order": ["Limpol"]}, scope="7")]
    # lista sobre global vem antes de categoria sobre nula
    assert resolve(rules, "detergente", 7).brand_chain == ["Limpol"]
    assert resolve(rules, "detergente", 8).brand_chain == ["Ypê"]


def test_source_breaks_ties():
    rules = [rule(kind="brand_chain", payload={"order": ["Pilão"]}, category="cafe", source="learned"),
             rule(kind="brand_chain", payload={"order": ["Melitta"]}, category="cafe", source="chat"),
             rule(kind="brand_chain", payload={"order": ["3 Corações"]}, category="cafe", source="user")]
    assert resolve(rules, "cafe", None).brand_chain == ["3 Corações"]
    assert resolve(rules[:2], "cafe", None).brand_chain == ["Melitta"]


def test_newest_wins_on_full_tie():
    a = rule(kind="variety", payload={"terms": ["integral"]}, category="arroz")
    b = rule(kind="variety", payload={"terms": ["parboilizado"]}, category="arroz")
    assert resolve([a, b], "arroz", None).variety_terms == ["parboilizado"]


def test_inactive_and_other_list_ignored():
    rules = [rule(kind="dietary", payload={"tags": ["VEGAN"]}, active=False),
             rule(kind="dietary", payload={"tags": ["ORGANIC"]}, scope="99")]
    assert resolve(rules, "leite", 1).dietary == []


def test_notes_and_blocks_accumulate():
    rules = [rule(kind="note", payload={"text": "banana mais verde"}, category="banana"),
             rule(kind="note", payload={"text": "cacho pequeno"}, category="banana"),
             rule(kind="brand_block", payload={"brands": ["X"]}),
             rule(kind="brand_block", payload={"brands": ["Y"]}, category="banana")]
    r = resolve(rules, "banana", None)
    assert sorted(r.notes) == ["banana mais verde", "cacho pequeno"]
    assert r.blocked_brands == {"X", "Y"}
    assert len(r.applied_rule_ids) == 4


def test_blocked_brand_leaves_chain():
    rules = [rule(kind="brand_chain", payload={"order": ["Ypê", "Limpol"]}, category="detergente"),
             rule(kind="brand_block", payload={"brands": ["ype"]})]
    r = resolve(rules, "Detergente", None)
    assert r.brand_chain == ["Limpol"]
    assert r.is_blocked_brand("Ypê")


def test_category_match_ignores_case_and_accents():
    rules = [rule(kind="variety", payload={"terms": ["integral"]}, category="Pão", hardness="hard")]
    r = resolve(rules, "pao", None)
    assert r.variety_terms == ["integral"] and r.variety_hard


def test_brand_position():
    r = resolve([rule(kind="brand_chain", payload={"order": ["Ypê", "Limpol"], "allow_others": False})], "x", None)
    assert r.brand_position("ype") == 0
    assert r.brand_position("LIMPOL") == 1
    assert r.brand_position("Minuano") is None
    assert r.allow_other_brands is False


def test_payload_validation():
    assert validate_payload("dietary", {"tags": ["VEGAN"]}) == {"tags": ["VEGAN"]}
    with pytest.raises(Exception):
        validate_payload("dietary", {"tags": ["CARNIVORO"]})
    with pytest.raises(ValueError):
        validate_payload("inexistente", {})


def test_describe():
    assert describe("brand_chain", {"order": ["Ypê", "Limpol"], "allow_others": True}, "detergente") == \
        "Detergente: Ypê, depois Limpol"
