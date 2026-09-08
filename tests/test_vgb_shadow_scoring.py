from __future__ import annotations

import math

import pytest

from scripts.analyze_vgb_shadow_scoring import (
    aggregate_scores,
    direct_nonlinear_summary,
    error_mapping,
    field_score,
    rank_correlation,
    score_mapping,
    score_task,
)


@pytest.mark.parametrize(
    ("mapping", "expected"),
    [
        ("linear", [1.0, 0.0, 0.0]),
        ("exponential", [1.0, 0.5, 0.25]),
        ("exponential_p05", [1.0, 0.5, 2 ** (-math.sqrt(2))]),
        ("exponential_p2", [1.0, 0.5, 0.0625]),
        ("rational", [1.0, 0.5, 1 / 3]),
        ("rational_p05", [1.0, 0.5, 1 / (1 + math.sqrt(2))]),
        ("power2", [1.0, 0.25, 1 / 9]),
        ("logistic", [1.0, 0.5, 1 / 6]),
    ],
)
def test_error_mapping_boundaries(mapping: str, expected: list[float]) -> None:
    actual = [error_mapping(value, mapping) for value in (0.0, 1.0, 2.0)]
    assert actual == pytest.approx(expected)


def test_linear_mapping_clips_but_tail_mappings_retain_large_error_information() -> None:
    assert error_mapping(4.0, "linear") == 0.0
    assert error_mapping(4.0, "exponential") > error_mapping(8.0, "exponential") > 0.0


def test_direct_nonlinear_tails_are_monotone_and_nonzero_for_large_finite_errors() -> None:
    for mapping in (
        "exponential",
        "exponential_p05",
        "exponential_p2",
        "rational",
        "rational_p05",
        "power2",
        "logistic",
    ):
        values = [error_mapping(error, mapping) for error in (0.0, 0.25, 1.0, 4.0, 16.0)]
        assert values == sorted(values, reverse=True)
        assert all(value > 0.0 for value in values)


@pytest.mark.parametrize("transform", ["square", "sqrt", "complement_square", "complement_sqrt", "log1p9"])
def test_score_space_transforms_are_bounded(transform: str) -> None:
    values = [score_mapping(value, transform) for value in (0.0, 0.25, 0.5, 1.0)]
    assert values[0] == pytest.approx(0.0)
    assert values[-1] == pytest.approx(1.0)
    assert all(0.0 <= value <= 1.0 for value in values)
    assert values == sorted(values)


def test_score_space_cannot_restore_collapsed_zero_scores() -> None:
    transforms = ["square", "sqrt", "complement_square", "complement_sqrt", "log1p9"]
    assert all(score_mapping(0.0, transform) == 0.0 for transform in transforms)


def test_aggregation_variants() -> None:
    assert aggregate_scores([0.25, 0.75], "arithmetic_mean") == pytest.approx(0.5)
    assert aggregate_scores([0.25, 0.75], "geometric_mean") == pytest.approx(math.sqrt(0.1875))
    assert aggregate_scores([0.25, 0.75], "product") == pytest.approx(0.1875)


def test_rank_correlation_preserves_identical_order() -> None:
    result = rank_correlation([0.0, 0.5, 1.0], [10.0, 20.0, 30.0])
    assert result["spearman"] == pytest.approx(1.0)
    assert result["kendall_tau_b"] == pytest.approx(1.0)


def test_rank_correlation_handles_reversed_order() -> None:
    result = rank_correlation([0.0, 0.5, 1.0], [30.0, 20.0, 10.0])
    assert result["spearman"] == pytest.approx(-1.0)
    assert result["kendall_tau_b"] == pytest.approx(-1.0)


def test_rank_correlation_uses_tau_b_for_ties() -> None:
    result = rank_correlation([0.0, 0.0, 1.0], [10.0, 20.0, 30.0])
    assert result["spearman"] == pytest.approx(0.8660254038)
    assert result["kendall_tau_b"] == pytest.approx(0.8164965809)


def test_numeric_field_rejects_wrong_unit_and_non_positive_log10() -> None:
    profile = {
        "type": "numeric_gold",
        "unit": "eV",
        "lower_tolerance": 1.0,
        "upper_tolerance": 1.0,
        "value_transform": "log10",
    }
    gold = {"value": 10.0, "unit": "eV"}
    assert field_score({"value": 10.0, "unit": "kcal/mol"}, gold, profile)[0] == 0.0
    assert field_score({"value": 0.0, "unit": "eV"}, gold, profile)[0] == 0.0


def test_absolute_transform_uses_magnitude_for_signed_energy() -> None:
    profile = {
        "type": "numeric_gold",
        "unit": "kcal/mol",
        "lower_tolerance": 2.0,
        "upper_tolerance": 2.0,
        "value_transform": "absolute",
    }
    score, error, field_type = field_score(
        {"value": -10.0, "unit": "kcal/mol"},
        {"value": -10.0, "unit": "kcal/mol"},
        profile,
    )
    assert score == pytest.approx(1.0)
    assert error == pytest.approx(0.0)
    assert field_type == "numeric"


def test_unordered_numeric_group_uses_best_assignment() -> None:
    task = {
        "requested_properties": [
            {"name": "frequency_1", "comparison_group": "top_two", "value_type": "number"},
            {"name": "frequency_2", "comparison_group": "top_two", "value_type": "number"},
        ],
        "gold_answers": [
            {"property": "frequency_1", "value": 100.0, "unit": "cm^-1", "scoring_profile": "p1"},
            {"property": "frequency_2", "value": 200.0, "unit": "cm^-1", "scoring_profile": "p2"},
        ],
        "scoring": {"comparison_groups": [{"id": "top_two", "mode": "unordered_numeric"}]},
    }
    profiles = {
        name: {"type": "numeric_gold", "unit": "cm^-1", "lower_tolerance": 10.0, "upper_tolerance": 10.0}
        for name in ("p1", "p2")
    }
    score, fields, groups = score_task(
        task,
        profiles,
        {
            "frequency_1": {"value": 200.0, "unit": "cm^-1"},
            "frequency_2": {"value": 100.0, "unit": "cm^-1"},
        },
    )
    assert score == pytest.approx(1.0)
    assert all(item["score"] == 0.0 for item in fields)
    assert groups[0]["score"] == pytest.approx(1.0)


def test_direct_nonlinear_summary_keeps_only_arithmetic_error_candidates() -> None:
    reports = [
        {
            "candidate": {"candidate_id": "error_exponential_arithmetic", "kind": "error_space", "aggregation": "arithmetic_mean", "field_mapping": "exponential", "formula": "2^-e"},
            "pair_metrics": [
                    {"track": "advanced", "group": "skill-on", "mean_signed_gap": 0.2, "tie_count_below_0_01": 1, "tie_with_answer_distance_ge_1_count": 0, "ranking_reversal_count": 0}
            ],
        },
        {
            "candidate": {"candidate_id": "error_exponential_geometric", "kind": "error_space", "aggregation": "geometric_mean", "field_mapping": "exponential", "formula": "2^-e"},
            "pair_metrics": [
                    {"track": "advanced", "group": "skill-on", "mean_signed_gap": 0.9, "tie_count_below_0_01": 0, "tie_with_answer_distance_ge_1_count": 0, "ranking_reversal_count": 0}
            ],
        },
    ]
    summary = direct_nonlinear_summary(reports)
    assert [item["candidate_id"] for item in summary] == ["error_exponential_arithmetic"]
