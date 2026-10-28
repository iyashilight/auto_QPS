from qps.trading.advanced import PortfolioRiskModel


def test_quality_score_is_not_direct_capital_weight() -> None:
    result = PortfolioRiskModel().evaluate(positions={"BTC": 1})
    assert "model_version" in result
    assert "weight" not in result

