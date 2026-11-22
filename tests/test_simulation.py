from harbor.simulation import simulate, report


def test_reproducible_paired_assumptions():
    first = simulate(73, 1000)
    assert first == simulate(73, 1000)
    assert first["reminder_no_shows"] <= first["baseline_no_shows"]
    assert first["refills"] <= first["eligible_offers"] <= first["cancellations"]
    assert first["attended_denominator"] + first["cancellations"] == 1000


def test_report_labels_assumptions_not_effectiveness():
    result = report([1, 2], 100)
    assert result["kind"] == "synthetic_assumed_effect_simulation"
    assert "assumptions" in result
