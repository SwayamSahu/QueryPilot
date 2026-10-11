from querypilot.eval.scoring import execution_match, result_match


def test_column_order_and_extra_columns():
    gold = [["West", 0.25], ["APAC", 0.21]]
    pred_same = [[0.21, "APAC"], [0.25, "West"]]
    pred_extra = [["APAC", 0.21, 0.30, -0.09], ["West", 0.25, 0.33, -0.08]]
    assert execution_match(gold, pred_same) and result_match(gold, pred_same)
    assert not execution_match(gold, pred_extra) and result_match(gold, pred_extra)


def test_order_enforced_only_when_needed():
    gold = [["a", 3], ["b", 2]]
    pred = [["b", 2], ["a", 3]]
    assert result_match(gold, pred, ordered=False)
    assert not result_match(gold, pred, ordered=True)


def test_numeric_tolerance_and_mismatch():
    assert result_match([[1 / 3]], [[0.33333333333]])
    assert not result_match([[0.3333]], [[0.3334]])
    assert not result_match([["a", 1]], [["a", 1], ["b", 2]])
