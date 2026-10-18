from app.pipeline.optimize import Candidate, Item, Merchant, Problem, solve, solve_with_alternatives

A = Merchant("A", "Mercado A", 8.0, 30.0)
B = Merchant("B", "Mercado B", 7.0, 30.0)
Z = Merchant("Z", "Sem mínimo", 5.0, 0.0)


def c(sku, item, m, price, size=1.0, **kw):
    return Candidate(sku_id=sku, item_id=item, merchant_id=m, name=sku, brand=None, price=price, size=size, **kw)


def test_single_merchant_when_split_does_not_pay():
    p = Problem(items=[Item(1, 2, 50), Item(2, 5000, 80), Item(3, 1, 40)], merchants=[A, B],
                candidates=[c("a1", 1, "A", 5), c("b1", 1, "B", 4.5), c("a2", 2, "A", 27, 5000),
                            c("b2", 2, "B", 9, 1000), c("a3", 3, "A", 20), c("b3", 3, "B", 30)], max_merchants=2)
    sols = solve_with_alternatives(p, split_min_savings=5)
    best = sols[0]
    assert best.merchants == ["A"]
    assert {(x.sku_id, x.packs) for x in best.picks} == {("a1", 2), ("a2", 1), ("a3", 1)}
    assert best.estimated_total == 65.0
    # a alternativa usa outro conjunto de mercados
    assert sols[1].merchants == ["B"]


def test_split_when_savings_exceed_threshold():
    p = Problem(items=[Item(1, 1, 100), Item(2, 1, 100)], merchants=[A, B],
                candidates=[c("a1", 1, "A", 30), c("b1", 1, "B", 60), c("a2", 2, "A", 60), c("b2", 2, "B", 30)],
                max_merchants=2)
    best = solve_with_alternatives(p, split_min_savings=5)[0]
    assert best.merchants == ["A", "B"]
    assert best.estimated_total == 75.0
    assert best.extra["single_estimated_total"] == 97.0
    # com tolerância alta, a divisão não compensa
    assert solve_with_alternatives(p, split_min_savings=30)[0].merchants in (["A"], ["B"])


def test_packs_cover_quantity_without_overbuying():
    p = Problem(items=[Item(1, 5000, 100)], merchants=[A],
                candidates=[c("kg", 1, "A", 6.0, 1000), c("5kg", 1, "A", 31.0, 5000)], max_merchants=1)
    sol = solve(p)
    pick = sol.picks[0]
    assert pick.sku_id == "kg" and pick.packs == 5


def test_minimum_order_forces_other_merchant_or_missing():
    cheap = Merchant("C", "Mínimo alto", 0.0, 200.0)
    p = Problem(items=[Item(1, 1, 50)], merchants=[cheap, A],
                candidates=[c("c1", 1, "C", 5), c("a1", 1, "A", 31)], max_merchants=1)
    sol = solve(p)
    assert sol.merchants == ["A"]


def test_missing_item_when_too_expensive():
    p = Problem(items=[Item(1, 1, 10), Item(2, 1, 100)], merchants=[A],
                candidates=[c("a1", 1, "A", 500), c("a2", 2, "A", 40)], max_merchants=1)
    sol = solve(p)
    assert sol.missing == [1]
    assert [x.sku_id for x in sol.picks] == ["a2"]


def test_penalties_shift_choice():
    p = Problem(items=[Item(1, 1, 100)], merchants=[Z],
                candidates=[c("generico", 1, "Z", 10, substitution_penalty=5), c("preferida", 1, "Z", 12)],
                max_merchants=1)
    assert solve(p).picks[0].sku_id == "preferida"


def test_quality_alpha():
    cands = [c("barato", 1, "Z", 10, quality_penalty=4), c("bom", 1, "Z", 12)]
    eco = Problem(items=[Item(1, 1, 100)], merchants=[Z], candidates=cands, alpha=0.0, max_merchants=1)
    qual = Problem(items=[Item(1, 1, 100)], merchants=[Z], candidates=cands, alpha=1.0, max_merchants=1)
    assert solve(eco).picks[0].sku_id == "barato"
    assert solve(qual).picks[0].sku_id == "bom"


def test_unused_merchant_has_no_fee():
    p = Problem(items=[Item(1, 1, 100)], merchants=[A, B], candidates=[c("a1", 1, "A", 31)], max_merchants=2)
    sol = solve(p)
    assert sol.merchants == ["A"] and sol.delivery_cost == 8.0
