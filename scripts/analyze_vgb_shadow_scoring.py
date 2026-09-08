#!/usr/bin/env python
"""Compare diagnostic nonlinear score mappings for a VGB comparison report.

The official v0.9.1 verifier remains authoritative. This script only reads the
saved comparison report and pinned wheel resources, then writes independent
shadow-scoring artifacts.
"""

from __future__ import annotations

import argparse
import csv
import itertools
import json
import math
import re
import statistics
import sys
import zipfile
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from benchmarking.runtime import paths as runtime_paths  # noqa: E402
from benchmarking.runtime.vgb_bridge import (  # noqa: E402
    ReleaseConfig,
    load_public_reference_answers,
    load_release_config,
    sha256_file,
    validate_runtime_files,
)

ATOM_IDENTITY_RE = re.compile(r"(?:(?P<index>0|[1-9][0-9]*) )?(?P<element>[A-Z][a-z]?)")
REPORT_GLOB = "benchmark-rescore-reports/*/comparison.json"
EPSILON = 1e-9


@dataclass(frozen=True)
class Candidate:
    candidate_id: str
    kind: str
    formula: str
    description: str
    field_mapping: str | None = None
    aggregation: str | None = None
    score_transform: str | None = None


@dataclass(frozen=True)
class TrackContext:
    tasks: dict[str, dict[str, Any]]
    profiles: dict[str, dict[str, Any]]
    public_answers: dict[str, dict[str, Any]]


@dataclass(frozen=True)
class ScoringContext:
    release: dict[str, str]
    tracks: dict[str, TrackContext]


def candidates() -> tuple[Candidate, ...]:
    score_space = (
        Candidate("official_linear", "official", "s", "Pinned v0.9.1 linear score."),
        Candidate("score_square", "score_space", "s^2", "Square the completed task score.", score_transform="square"),
        Candidate("score_sqrt", "score_space", "sqrt(s)", "Square root of the completed task score.", score_transform="sqrt"),
        Candidate(
            "score_complement_square",
            "score_space",
            "1-(1-s)^2",
            "Complement-square transform of the completed task score.",
            score_transform="complement_square",
        ),
        Candidate(
            "score_complement_sqrt",
            "score_space",
            "1-sqrt(1-s)",
            "Complement-square-root transform of the completed task score.",
            score_transform="complement_sqrt",
        ),
        Candidate(
            "score_log1p9",
            "score_space",
            "log1p(9s)/log(10)",
            "Logarithmic expansion of the completed task score.",
            score_transform="log1p9",
        ),
    )
    error_space: list[Candidate] = []
    for mapping, formula, description in (
        ("linear", "max(0,1-e)", "Pinned linear decay in normalized error."),
        ("exponential", "2^-e", "Exponential tail with half-score at one tolerance width."),
        ("exponential_p05", "2^(-sqrt(e))", "Generalized exponential tail with p=0.5; half-score at one tolerance width."),
        ("exponential_p2", "2^(-e^2)", "Generalized exponential tail with p=2; half-score at one tolerance width."),
        ("rational", "1/(1+e)", "Rational tail in normalized error."),
        ("rational_p05", "1/(1+sqrt(e))", "Generalized rational tail with p=0.5; half-score at one tolerance width."),
        ("power2", "(1+e)^-2", "Quadratic rational tail in normalized error."),
        ("logistic", "1.5/(1+0.5*4^e)", "Normalized logistic tail with score 0.5 at one tolerance width."),
    ):
        for aggregation in ("arithmetic_mean", "geometric_mean", "product"):
            suffix = "arithmetic" if aggregation == "arithmetic_mean" else aggregation.removesuffix("_mean")
            error_space.append(
                Candidate(
                    f"error_{mapping}_{suffix}",
                    "error_space",
                    f"{formula}; {aggregation}",
                    f"{description} Task aggregation: {aggregation}.",
                    field_mapping=mapping,
                    aggregation=aggregation,
                )
            )
    return score_space + tuple(error_space)


def finite_number(value: object) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(float(value))


def transform_numeric(value: object, profile: dict[str, Any]) -> float | None:
    if not finite_number(value):
        return None
    number = float(value)
    transform = profile.get("value_transform", "identity")
    if transform == "absolute":
        return abs(number)
    if transform == "log10":
        return math.log10(number) if number > 0 else None
    if transform == "identity":
        return number
    raise ValueError(f"Unsupported value_transform: {transform}")


def normalized_error(value: object, gold: object, profile: dict[str, Any]) -> float | None:
    transformed_value = transform_numeric(value, profile)
    transformed_gold = transform_numeric(gold, profile)
    if transformed_value is None or transformed_gold is None:
        return None
    tolerance_key = "lower_tolerance" if transformed_value < transformed_gold else "upper_tolerance"
    tolerance = float(profile[tolerance_key])
    if tolerance <= 0:
        raise ValueError("Numeric profile tolerance must be positive")
    return abs(transformed_value - transformed_gold) / tolerance


def error_mapping(error: float | None, mapping: str) -> float:
    if error is None:
        return 0.0
    if error < 0 or not math.isfinite(error):
        raise ValueError("Normalized error must be finite and non-negative")
    if mapping == "linear":
        return max(0.0, 1.0 - error)
    if mapping == "exponential":
        return 2.0 ** (-error)
    if mapping == "exponential_p05":
        return 2.0 ** (-math.sqrt(error))
    if mapping == "exponential_p2":
        return 2.0 ** (-(error**2))
    if mapping == "rational":
        return 1.0 / (1.0 + error)
    if mapping == "rational_p05":
        return 1.0 / (1.0 + math.sqrt(error))
    if mapping == "power2":
        return (1.0 + error) ** -2
    if mapping == "logistic":
        exponent = error * math.log(4.0)
        if exponent > 700.0:
            return 0.0
        return 1.5 / (1.0 + 0.5 * math.exp(exponent))
    raise ValueError(f"Unsupported error mapping: {mapping}")


def score_mapping(score: float, transform: str | None) -> float:
    if not finite_number(score) or not 0.0 <= float(score) <= 1.0:
        raise ValueError("Task score must be in [0, 1]")
    value = float(score)
    if transform is None:
        return value
    if transform == "square":
        return value**2
    if transform == "sqrt":
        return math.sqrt(value)
    if transform == "complement_square":
        return 1.0 - (1.0 - value) ** 2
    if transform == "complement_sqrt":
        return 1.0 - math.sqrt(1.0 - value)
    if transform == "log1p9":
        return math.log1p(9.0 * value) / math.log(10.0)
    raise ValueError(f"Unsupported score transform: {transform}")


def aggregate_scores(values: Iterable[float], aggregation: str) -> float:
    numbers = [float(value) for value in values]
    if not numbers:
        raise ValueError("Cannot aggregate an empty score list")
    if aggregation == "arithmetic_mean":
        return sum(numbers) / len(numbers)
    if aggregation == "geometric_mean":
        return math.prod(numbers) ** (1.0 / len(numbers))
    if aggregation == "product":
        return math.prod(numbers)
    raise ValueError(f"Unsupported aggregation: {aggregation}")


def atom_identity_score(value: object, gold: object, profile: dict[str, Any]) -> float:
    submitted_match = ATOM_IDENTITY_RE.fullmatch(value) if isinstance(value, str) else None
    gold_match = ATOM_IDENTITY_RE.fullmatch(gold) if isinstance(gold, str) else None
    if submitted_match is None or gold_match is None:
        return 0.0
    if submitted_match.groups() == gold_match.groups():
        return 1.0
    if submitted_match["element"] == gold_match["element"]:
        return float(profile.get("element_partial_score", 0.0))
    return 0.0


def exact_string_score(value: object, gold: object, profile: dict[str, Any]) -> float:
    if not isinstance(value, str):
        return 0.0
    if value == gold:
        return 1.0
    return float(profile.get("partial_scores", {}).get(value, 0.0))


def field_score(
    submitted: dict[str, Any] | None,
    gold: dict[str, Any],
    profile: dict[str, Any],
    *,
    mapping: str = "linear",
) -> tuple[float, float | None, str]:
    if not isinstance(submitted, dict) or submitted.get("unit") != gold.get("unit"):
        return 0.0, None, "invalid"
    value = submitted.get("value")
    profile_type = profile.get("type")
    if profile_type == "numeric_gold":
        error = normalized_error(value, gold.get("value"), profile)
        if error is None:
            return 0.0, None, "invalid"
        return error_mapping(error, mapping), error, "numeric"
    if profile_type == "exact_string":
        score = exact_string_score(value, gold.get("value"), profile)
        return score, 0.0 if score == 1.0 else 1.0, "categorical"
    if profile_type == "atom_identity":
        score = atom_identity_score(value, gold.get("value"), profile)
        return score, 0.0 if score == 1.0 else 1.0, "categorical"
    raise ValueError(f"Unsupported scoring profile type: {profile_type}")


def public_reference_fields(reference: dict[str, Any]) -> dict[str, dict[str, Any]]:
    if isinstance(reference.get("answers"), list):
        return {
            str(answer["property"]): {
                "value": answer.get("value"),
                "unit": answer.get("unit"),
            }
            for answer in reference["answers"]
        }
    property_name = reference.get("property")
    if not property_name:
        raise ValueError(f"Public reference lacks property: {reference}")
    return {
        str(property_name): {
            "value": reference.get("answer"),
            "unit": reference.get("unit"),
        }
    }


def load_yaml_from_wheel(wheel_path: Path, member: str) -> dict[str, Any]:
    with zipfile.ZipFile(wheel_path) as archive:
        try:
            payload = archive.read(member)
        except KeyError as exc:
            raise FileNotFoundError(f"Pinned wheel lacks resource: {member}") from exc
    loaded = yaml.safe_load(payload)
    if not isinstance(loaded, dict):
        raise ValueError(f"Expected mapping resource: {member}")
    return loaded


def load_context(config: ReleaseConfig) -> ScoringContext:
    validate_runtime_files(config)
    track_paths = {
        "property_calculation_advanced": (
            "property_calculation_advanced/tasks.yaml",
            "property_calculation_advanced/scoring.yaml",
        ),
        "property_calculation_basic": (
            "property_calculation_basic/tasks.yaml",
            "property_calculation_basic/scoring.yaml",
        ),
    }
    contexts: dict[str, TrackContext] = {}
    for track_name, (task_member, scoring_member) in track_paths.items():
        task_payload = load_yaml_from_wheel(
            config.wheel_path, f"verifier_grounded_benchmark/task/packs/{task_member}"
        )
        scoring_payload = load_yaml_from_wheel(
            config.wheel_path, f"verifier_grounded_benchmark/task/packs/{scoring_member}"
        )
        task_by_id = {str(task["task_id"]): dict(task) for task in task_payload.get("tasks", [])}
        scoring_tasks = {str(task["task_id"]): dict(task) for task in scoring_payload.get("tasks", [])}
        if set(task_by_id) != set(scoring_tasks):
            raise ValueError(f"Task/scoring inventory mismatch for {track_name}")
        merged_tasks: dict[str, dict[str, Any]] = {}
        for task_id, task in task_by_id.items():
            merged = dict(task)
            merged.update(scoring_tasks[task_id])
            merged_tasks[task_id] = merged
        public_answers = {
            str(item["task_id"]): item
            for item in load_public_reference_answers(track_name, release_config=config)
        }
        expected_ids = list(config.tracks[track_name]["task_ids"])
        if list(merged_tasks) != expected_ids or list(public_answers) != expected_ids:
            raise ValueError(f"Pinned task order mismatch for {track_name}")
        for task_id in expected_ids:
            hidden = {
                str(item["property"]): {"value": item.get("value"), "unit": item.get("unit")}
                for item in merged_tasks[task_id].get("gold_answers", [])
            }
            public = public_reference_fields(public_answers[task_id])
            if hidden != public:
                raise ValueError(f"Public/hidden gold mismatch for {track_name}/{task_id}")
        contexts[track_name] = TrackContext(
            tasks=merged_tasks,
            profiles=dict(scoring_payload.get("scoring_profiles") or {}),
            public_answers=public_answers,
        )
    return ScoringContext(release=config.identity, tracks=contexts)


def task_track(row: dict[str, Any]) -> str:
    track = str(row.get("track") or "")
    if track not in {"property_calculation_advanced", "property_calculation_basic"}:
        raise ValueError(f"Unsupported comparison track: {track}")
    return track


def row_model(row: dict[str, Any]) -> str:
    run_id = str(row.get("run_id") or "")
    if "-gpt-" in run_id:
        return "GPT"
    if "-qwen3-8-flash-" in run_id:
        return "Qwen"
    raise ValueError(f"Cannot infer model from run id: {run_id}")


def row_group(row: dict[str, Any]) -> str:
    group_id = str(row.get("group_id") or "")
    if group_id == "single_llm_skills_on":
        return "skill-on"
    if group_id == "single_llm_skills_off":
        return "skill-off"
    raise ValueError(f"Unsupported comparison group: {group_id}")


def submitted_answers(row: dict[str, Any]) -> dict[str, dict[str, Any]]:
    details = ((row.get("latest_evaluation") or {}).get("details") or {})
    properties = details.get("properties") or {}
    submitted = properties.get("submitted_answers")
    return dict(submitted) if isinstance(submitted, dict) else {}


def gold_answers(task: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        str(item["property"]): {
            "value": item.get("value"),
            "unit": item.get("unit"),
            "scoring_profile": item.get("scoring_profile"),
        }
        for item in task.get("gold_answers", [])
    }


def requested_by_name(task: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {str(item["name"]): dict(item) for item in task.get("requested_properties", [])}


def score_task(
    task: dict[str, Any],
    profiles: dict[str, dict[str, Any]],
    submitted: dict[str, dict[str, Any]],
    *,
    mapping: str = "linear",
    aggregation: str = "arithmetic_mean",
) -> tuple[float, list[dict[str, Any]], list[dict[str, Any]]]:
    gold = gold_answers(task)
    requested = requested_by_name(task)
    field_results: dict[str, tuple[float, float | None, str]] = {}
    field_payload: list[dict[str, Any]] = []
    for name, _definition in requested.items():
        gold_definition = gold[name]
        profile = profiles[gold_definition["scoring_profile"]]
        score, error, field_type = field_score(
            submitted.get(name), gold_definition, profile, mapping=mapping
        )
        field_results[name] = (score, error, field_type)
        field_payload.append(
            {
                "property": name,
                "score": score,
                "normalized_error": error,
                "field_type": field_type,
                "submitted_value": (submitted.get(name) or {}).get("value"),
                "gold_value": gold_definition.get("value"),
                "unit": gold_definition.get("unit"),
            }
        )
    group_payload: list[dict[str, Any]] = []
    for group in task["scoring"]["comparison_groups"]:
        group_id = str(group["id"])
        members = [
            name for name, definition in requested.items()
            if definition["comparison_group"] == group_id
        ]
        if group["mode"] == "unordered_numeric":
            assignments: list[float] = []
            for gold_order in itertools.permutations(members):
                assignment = []
                for submitted_name, gold_name in zip(members, gold_order, strict=True):
                    profile = profiles[gold[gold_name]["scoring_profile"]]
                    assignment.append(
                        field_score(
                            submitted.get(submitted_name),
                            gold[gold_name],
                            profile,
                            mapping=mapping,
                        )[0]
                    )
                assignments.append(min(assignment))
            group_score = max(assignments)
        else:
            group_score = min(field_results[name][0] for name in members)
        group_payload.append(
            {"group": group_id, "mode": group["mode"], "members": members, "score": group_score}
        )
    return aggregate_scores([item["score"] for item in group_payload], aggregation), field_payload, group_payload


def official_task_score(
    task: dict[str, Any], profiles: dict[str, dict[str, Any]], submitted: dict[str, dict[str, Any]]
) -> tuple[float, list[dict[str, Any]], list[dict[str, Any]]]:
    return score_task(task, profiles, submitted)


def apply_candidate(
    candidate: Candidate,
    task: dict[str, Any],
    profiles: dict[str, dict[str, Any]],
    submitted: dict[str, dict[str, Any]],
    official_score: float,
) -> tuple[float, list[dict[str, Any]], list[dict[str, Any]]]:
    if candidate.kind in {"official", "score_space"}:
        return score_mapping(official_score, candidate.score_transform), [], []
    score, fields, groups = score_task(
        task,
        profiles,
        submitted,
        mapping=str(candidate.field_mapping),
        aggregation=str(candidate.aggregation),
    )
    return score, fields, groups


def _pearson(values_a: list[float], values_b: list[float]) -> float | None:
    if len(values_a) != len(values_b) or len(values_a) < 2:
        return None
    mean_a = statistics.mean(values_a)
    mean_b = statistics.mean(values_b)
    centered_a = [value - mean_a for value in values_a]
    centered_b = [value - mean_b for value in values_b]
    denominator = math.sqrt(sum(value * value for value in centered_a) * sum(value * value for value in centered_b))
    if denominator == 0:
        return 1.0 if values_a == values_b else 0.0
    return sum(a * b for a, b in zip(centered_a, centered_b, strict=True)) / denominator


def _rank(values: list[float]) -> list[float]:
    result = [0.0] * len(values)
    ordered = sorted(range(len(values)), key=lambda index: values[index])
    position = 0
    while position < len(ordered):
        end = position + 1
        while end < len(ordered) and values[ordered[end]] == values[ordered[position]]:
            end += 1
        rank = statistics.mean(range(position + 1, end + 1))
        for offset in range(position, end):
            result[ordered[offset]] = rank
        position = end
    return result


def rank_correlation(values_a: list[float], values_b: list[float]) -> dict[str, float | None]:
    ranks_a = _rank(values_a)
    ranks_b = _rank(values_b)
    concordant = discordant = ties_a_only = ties_b_only = 0
    for left in range(len(values_a)):
        for right in range(left + 1, len(values_a)):
            diff_a = values_a[left] - values_a[right]
            diff_b = values_b[left] - values_b[right]
            if diff_a == 0 and diff_b == 0:
                continue
            if diff_a == 0:
                ties_a_only += 1
                continue
            if diff_b == 0:
                ties_b_only += 1
                continue
            product = diff_a * diff_b
            if product > 0:
                concordant += 1
            elif product < 0:
                discordant += 1
    denominator = math.sqrt(
        (concordant + discordant + ties_a_only)
        * (concordant + discordant + ties_b_only)
    )
    kendall = (concordant - discordant) / denominator if denominator else 1.0
    return {"spearman": _pearson(ranks_a, ranks_b), "kendall_tau_b": kendall}


def pair_answer_distance(
    task: dict[str, Any],
    profiles: dict[str, dict[str, Any]],
    left: dict[str, dict[str, Any]],
    right: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    gold = gold_answers(task)
    requested = requested_by_name(task)
    distances: list[float] = []
    exact = True
    for group in task["scoring"]["comparison_groups"]:
        members = [name for name, definition in requested.items() if definition["comparison_group"] == group["id"]]
        if group["mode"] == "unordered_numeric":
            assignment_costs: list[tuple[float, tuple[int, ...]]] = []
            for permutation in itertools.permutations(range(len(members))):
                costs: list[float] = []
                for index, gold_index in enumerate(permutation):
                    submitted_name = members[index]
                    gold_name = members[gold_index]
                    profile = profiles[gold[gold_name]["scoring_profile"]]
                    left_value = (left.get(submitted_name) or {}).get("value")
                    right_value = (right.get(submitted_name) or {}).get("value")
                    left_t = transform_numeric(left_value, profile)
                    right_t = transform_numeric(right_value, profile)
                    gold_t = transform_numeric(gold[gold_name].get("value"), profile)
                    tolerance = float(profile.get("upper_tolerance", 1.0))
                    if left_t is None or right_t is None or gold_t is None:
                        costs.append(0.0 if left_value == right_value else 1.0)
                    else:
                        costs.append(abs(left_t - right_t) / tolerance)
                assignment_costs.append((sum(costs), permutation))
            _, best = min(assignment_costs, key=lambda item: item[0])
            for index, gold_index in enumerate(best):
                submitted_name = members[index]
                gold_name = members[gold_index]
                profile = profiles[gold[gold_name]["scoring_profile"]]
                left_value = (left.get(submitted_name) or {}).get("value")
                right_value = (right.get(submitted_name) or {}).get("value")
                left_t = transform_numeric(left_value, profile)
                right_t = transform_numeric(right_value, profile)
                gold_t = transform_numeric(gold[gold_name].get("value"), profile)
                tolerance = float(profile.get("upper_tolerance", 1.0))
                distance = 0.0 if left_t is None or right_t is None or gold_t is None else abs(left_t - right_t) / tolerance
                distances.append(distance)
        else:
            for name in members:
                profile = profiles[gold[name]["scoring_profile"]]
                left_value = (left.get(name) or {}).get("value")
                right_value = (right.get(name) or {}).get("value")
                if profile.get("type") == "numeric_gold":
                    left_t = transform_numeric(left_value, profile)
                    right_t = transform_numeric(right_value, profile)
                    gold_t = transform_numeric(gold[name].get("value"), profile)
                    tolerance = float(profile.get("upper_tolerance", 1.0))
                    distance = 0.0 if left_t is None or right_t is None or gold_t is None else abs(left_t - right_t) / tolerance
                else:
                    distance = 0.0 if left_value == right_value else 1.0
                distances.append(distance)
    exact = all(distance == 0.0 for distance in distances)
    return {
        "answer_distance_mean": statistics.mean(distances) if distances else 0.0,
        "answer_distance_max": max(distances) if distances else 0.0,
        "answer_exact": exact,
        "answer_distance_ge_0_25": sum(distance >= 0.25 for distance in distances),
        "answer_distance_ge_1": sum(distance >= 1.0 for distance in distances),
        "answer_distance_ge_2": sum(distance >= 2.0 for distance in distances),
    }


def percentile(values: list[float], fraction: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int((len(ordered) - 1) * fraction))]


def direct_nonlinear_summary(candidate_reports: list[dict[str, Any]]) -> list[dict[str, Any]]:
    summary: list[dict[str, Any]] = []
    for report_item in candidate_reports:
        candidate = report_item["candidate"]
        if candidate.get("kind") != "error_space" or candidate.get("aggregation") != "arithmetic_mean":
            continue
        pairs = report_item["pair_metrics"]
        absolute_gaps = [abs(float(item["mean_signed_gap"])) for item in pairs]
        summary.append(
            {
                "candidate_id": candidate["candidate_id"],
                "formula": candidate["formula"],
                "mapping": candidate["field_mapping"],
                "average_absolute_model_gap": statistics.mean(absolute_gaps) if absolute_gaps else None,
                "maximum_absolute_model_gap": max(absolute_gaps) if absolute_gaps else None,
                "total_ties_below_0_01": sum(int(item["tie_count_below_0_01"]) for item in pairs),
                "total_ties_with_answer_distance_ge_1": sum(
                    int(item["tie_with_answer_distance_ge_1_count"]) for item in pairs
                ),
                "total_ranking_reversals": sum(int(item["ranking_reversal_count"]) for item in pairs),
                "slices": {
                    f"{item['track']}::{item['group']}": {
                        "signed_gap": item["mean_signed_gap"],
                        "absolute_gap": abs(float(item["mean_signed_gap"])),
                    }
                    for item in pairs
                },
            }
        )
    return sorted(
        summary,
        key=lambda item: float(item["average_absolute_model_gap"] or 0.0),
        reverse=True,
    )


def analyze_comparison(payload: dict[str, Any], context: ScoringContext) -> dict[str, Any]:
    candidates_list = candidates()
    source_rows = payload.get("results")
    if not isinstance(source_rows, list):
        raise ValueError("comparison.json results must be a list")
    records: list[dict[str, Any]] = []
    official_rows: list[dict[str, Any]] = []
    official_reconstruction_errors: list[float] = []
    for row in source_rows:
        latest_evaluation = row.get("latest_evaluation")
        if not isinstance(latest_evaluation, dict):
            continue
        track = task_track(row)
        task_id = str(row.get("latest_task_id") or "")
        task = context.tracks[track].tasks.get(task_id)
        if task is None:
            raise ValueError(f"Unknown pinned task: {track}/{task_id}")
        submitted = submitted_answers(row)
        official_score, official_fields, official_groups = official_task_score(
            task, context.tracks[track].profiles, submitted
        )
        persisted_score = row.get("latest_score")
        if not finite_number(persisted_score) or abs(float(persisted_score) - official_score) > EPSILON:
            raise ValueError(
                f"Official score reconstruction mismatch for {task_id}: "
                f"persisted={persisted_score!r}, reconstructed={official_score!r}"
            )
        official_reconstruction_errors.append(abs(float(persisted_score) - official_score))
        model = row_model(row)
        group = row_group(row)
        record_key = f"{model}:{track}:{group}:{task_id}"
        record = {
            "record_key": record_key,
            "model": model,
            "track": track,
            "group": group,
            "task_id": task_id,
            "official_score": official_score,
            "submitted_answers": submitted,
            "fields": official_fields,
            "groups": official_groups,
            "candidate_scores": {},
        }
        for candidate in candidates_list:
            score, fields, groups = apply_candidate(
                candidate,
                task,
                context.tracks[track].profiles,
                submitted,
                official_score,
            )
            record["candidate_scores"][candidate.candidate_id] = score
            if candidate.kind == "error_space":
                record.setdefault("error_space", {})[candidate.candidate_id] = {
                    "fields": fields,
                    "groups": groups,
                }
        records.append(record)
        official_rows.append(row)

    pair_rows: dict[tuple[str, str, str], dict[str, dict[str, Any]]] = defaultdict(dict)
    for record in records:
        pair_rows[(record["track"], record["group"], record["task_id"])][record["model"]] = record
    pair_payloads: list[dict[str, Any]] = []
    for (track, group, task_id), models in sorted(pair_rows.items()):
        if set(models) != {"GPT", "Qwen"}:
            continue
        task = context.tracks[track].tasks[task_id]
        answer_metrics = pair_answer_distance(
            task,
            context.tracks[track].profiles,
            models["GPT"]["submitted_answers"],
            models["Qwen"]["submitted_answers"],
        )
        pair_payloads.append(
            {
                "track": track,
                "group": group,
                "task_id": task_id,
                "gpt_official_score": models["GPT"]["official_score"],
                "qwen_official_score": models["Qwen"]["official_score"],
                "official_score_gap": models["GPT"]["official_score"] - models["Qwen"]["official_score"],
                "models": models,
                **answer_metrics,
            }
        )

    candidate_reports: list[dict[str, Any]] = []
    for candidate in candidates_list:
        model_metrics: list[dict[str, Any]] = []
        for model in ("GPT", "Qwen"):
            for track in ("property_calculation_advanced", "property_calculation_basic"):
                for group in ("skill-on", "skill-off"):
                    subset = [
                        record for record in records
                        if record["model"] == model and record["track"] == track and record["group"] == group
                    ]
                    values = [record["candidate_scores"][candidate.candidate_id] for record in subset]
                    official = [record["official_score"] for record in subset]
                    correlations = rank_correlation(official, values)
                    model_metrics.append(
                        {
                            "model": model,
                            "track": track,
                            "group": group,
                            "record_count": len(values),
                            "average_score": statistics.mean(values) if values else None,
                            "median_score": statistics.median(values) if values else None,
                            "zero_rate": sum(value == 0.0 for value in values) / len(values) if values else None,
                            "high_score_rate": sum(value >= 0.95 for value in values) / len(values) if values else None,
                            "interior_rate": sum(0.0 < value < 1.0 for value in values) / len(values) if values else None,
                            "spearman_vs_official": correlations["spearman"],
                            "kendall_tau_b_vs_official": correlations["kendall_tau_b"],
                        }
                    )
        candidate_pairs: list[dict[str, Any]] = []
        for track in ("property_calculation_advanced", "property_calculation_basic"):
            for group in ("skill-on", "skill-off"):
                pairs = [item for item in pair_payloads if item["track"] == track and item["group"] == group]
                gaps = [abs(item["models"]["GPT"]["candidate_scores"][candidate.candidate_id] - item["models"]["Qwen"]["candidate_scores"][candidate.candidate_id]) for item in pairs]
                signed = [item["models"]["GPT"]["candidate_scores"][candidate.candidate_id] - item["models"]["Qwen"]["candidate_scores"][candidate.candidate_id] for item in pairs]
                official_signs = [item["official_score_gap"] for item in pairs]
                reversal_count = sum(
                    official_gap * candidate_gap < 0
                    for official_gap, candidate_gap in zip(official_signs, signed, strict=True)
                )
                candidate_pairs.append(
                    {
                        "track": track,
                        "group": group,
                        "pair_count": len(pairs),
                        "gpt_paired_average_score": statistics.mean([item["models"]["GPT"]["candidate_scores"][candidate.candidate_id] for item in pairs]) if pairs else None,
                        "qwen_paired_average_score": statistics.mean([item["models"]["Qwen"]["candidate_scores"][candidate.candidate_id] for item in pairs]) if pairs else None,
                        "gpt_full_average_score": next((item["average_score"] for item in model_metrics if item["model"] == "GPT" and item["track"] == track and item["group"] == group), None),
                        "qwen_full_average_score": next((item["average_score"] for item in model_metrics if item["model"] == "Qwen" and item["track"] == track and item["group"] == group), None),
                        "mean_signed_gap": statistics.mean(signed) if signed else None,
                        "mean_absolute_gap": statistics.mean(gaps) if gaps else None,
                        "median_absolute_gap": statistics.median(gaps) if gaps else None,
                        "p90_absolute_gap": percentile(gaps, 0.9),
                        "tie_count_below_0_01": sum(gap < 0.01 for gap in gaps),
                        "answer_exact_count": sum(item["answer_exact"] for item in pairs),
                        "answer_distance_mean": statistics.mean([item["answer_distance_mean"] for item in pairs]) if pairs else None,
                        "answer_distance_median": statistics.median([item["answer_distance_mean"] for item in pairs]) if pairs else None,
                        "answer_distance_p90": percentile([item["answer_distance_mean"] for item in pairs], 0.9),
                        "answer_distance_max_mean": statistics.mean([item["answer_distance_max"] for item in pairs]) if pairs else None,
                        "answer_distance_ge_0_25_count": sum(item["answer_distance_max"] >= 0.25 for item in pairs),
                        "answer_distance_ge_1_count": sum(item["answer_distance_max"] >= 1.0 for item in pairs),
                        "answer_distance_ge_2_count": sum(item["answer_distance_max"] >= 2.0 for item in pairs),
                        "tie_with_answer_distance_ge_1_count": sum(item["answer_distance_max"] >= 1.0 and abs(item["models"]["GPT"]["candidate_scores"][candidate.candidate_id] - item["models"]["Qwen"]["candidate_scores"][candidate.candidate_id]) < 0.01 for item in pairs),
                        "ranking_reversal_count": reversal_count,
                    }
                )
        examples: list[dict[str, Any]] = []
        for track in ("property_calculation_advanced", "property_calculation_basic"):
            for group in ("skill-on", "skill-off"):
                pairs = [item for item in pair_payloads if item["track"] == track and item["group"] == group]
                if not pairs:
                    continue
                def candidate_value(
                    item: dict[str, Any], candidate_id: str = candidate.candidate_id
                ) -> float:
                    return abs(
                        item["models"]["GPT"]["candidate_scores"][candidate_id]
                        - item["models"]["Qwen"]["candidate_scores"][candidate_id]
                    )
                near_tie = sorted(
                    [item for item in pairs if abs(item["official_score_gap"]) < 0.01],
                    key=lambda item: item["answer_distance_max"],
                    reverse=True,
                )
                close_answers = sorted(pairs, key=lambda item: (item["answer_distance_max"], candidate_value(item)))
                changed_order = sorted(
                    [item for item in pairs if item["official_score_gap"] * (item["models"]["GPT"]["candidate_scores"][candidate.candidate_id] - item["models"]["Qwen"]["candidate_scores"][candidate.candidate_id]) < 0],
                    key=candidate_value,
                    reverse=True,
                )
                selected: list[tuple[str, dict[str, Any]]] = []
                for reason, pool in (
                    ("largest_answer_disagreement_near_official_tie", near_tie),
                    ("closest_answers", close_answers),
                    ("model_order_change_or_largest_candidate_gap", changed_order or sorted(pairs, key=candidate_value, reverse=True)),
                ):
                    if pool:
                        selected.append((reason, pool[0]))
                seen: set[tuple[str, str, str]] = set()
                for reason, item in selected:
                    key = (item["track"], item["group"], item["task_id"])
                    if key in seen:
                        continue
                    seen.add(key)
                    examples.append(
                        {
                            "reason": reason,
                            "track": item["track"],
                            "group": item["group"],
                            "task_id": item["task_id"],
                            "answer_distance_max": item["answer_distance_max"],
                            "official_score_gap": item["official_score_gap"],
                            "gpt_score": item["models"]["GPT"]["candidate_scores"][candidate.candidate_id],
                            "qwen_score": item["models"]["Qwen"]["candidate_scores"][candidate.candidate_id],
                            "gpt_answer": item["models"]["GPT"]["submitted_answers"],
                            "qwen_answer": item["models"]["Qwen"]["submitted_answers"],
                        }
                    )
        candidate_reports.append(
            {
                "candidate": candidate.__dict__,
                "model_metrics": model_metrics,
                "pair_metrics": candidate_pairs,
                "examples": examples,
            }
        )
    candidate_reports_payload = candidate_reports
    return {
        "schema_version": 1,
        "kind": "verifier_grounded_shadow_scoring",
        "official_scoring": "v0.9.1 official_linear",
        "generated_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "release": context.release,
        "source": {
            "comparison_schema_version": payload.get("schema_version"),
            "record_count": len(source_rows),
            "scored_record_count": len(records),
            "unscored_record_count": len(source_rows) - len(records),
        },
        "validation": {
            "official_reconstruction_max_abs_error": max(official_reconstruction_errors, default=0.0),
            "official_reconstruction_record_count": len(records),
            "public_gold_verified": True,
        },
        "candidates": [candidate.__dict__ for candidate in candidates_list],
        "direct_nonlinear_summary": direct_nonlinear_summary(candidate_reports_payload),
        "records": records,
        "candidate_reports": candidate_reports_payload,
    }


def sha256(path: Path) -> str:
    return sha256_file(path)


def atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(path)


def write_csv(path: Path, report: dict[str, Any]) -> None:
    fieldnames = [
        "candidate_id", "candidate_kind", "formula", "model", "track", "group",
        "record_count", "average_score", "median_score", "zero_rate", "high_score_rate",
        "interior_rate", "spearman_vs_official", "kendall_tau_b_vs_official",
        "pair_count", "gpt_paired_average_score", "qwen_paired_average_score",
        "gpt_full_average_score", "qwen_full_average_score", "mean_signed_gap",
        "mean_absolute_gap", "median_absolute_gap", "p90_absolute_gap", "tie_count_below_0_01",
        "answer_exact_count", "answer_distance_mean", "answer_distance_median",
        "answer_distance_p90", "answer_distance_max_mean", "answer_distance_ge_0_25_count",
        "answer_distance_ge_1_count", "answer_distance_ge_2_count",
        "tie_with_answer_distance_ge_1_count", "ranking_reversal_count",
    ]
    model_lookup = {
        (report_item["candidate"]["candidate_id"], item["model"], item["track"], item["group"]): item
        for report_item in report["candidate_reports"]
        for item in report_item["model_metrics"]
    }
    pair_lookup = {
        (report_item["candidate"]["candidate_id"], item["track"], item["group"]): item
        for report_item in report["candidate_reports"]
        for item in report_item["pair_metrics"]
    }
    rows: list[dict[str, Any]] = []
    for candidate in report["candidates"]:
        for model in ("GPT", "Qwen"):
            for track in ("property_calculation_advanced", "property_calculation_basic"):
                for group in ("skill-on", "skill-off"):
                    row = {**candidate, "model": model, "track": track, "group": group}
                    row.update(model_lookup[(candidate["candidate_id"], model, track, group)])
                    row.update(pair_lookup[(candidate["candidate_id"], track, group)])
                    rows.append({key: row.get(key) for key in fieldnames})
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def md_value(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, float):
        return f"{value:.6f}".rstrip("0").rstrip(".")
    return str(value).replace("|", "\\|").replace("\n", "<br>")


def write_markdown(path: Path, report: dict[str, Any]) -> None:
    lines = [
        "# v0.9.1 Shadow Scoring Analysis",
        "",
        "`official_linear` remains the formal v0.9.1 score. All other candidates are diagnostic shadow scores and do not change verifier results.",
        "",
        f"- Scored records: {report['source']['scored_record_count']}",
        f"- Unscored records: {report['source']['unscored_record_count']}",
        f"- Public gold verification: {report['validation']['public_gold_verified']}",
        "",
        "## Candidates",
        "",
        "| candidate | kind | formula | description |",
        "| --- | --- | --- | --- |",
    ]
    for candidate in report["candidates"]:
        lines.append("| " + " | ".join(md_value(candidate[key]) for key in ("candidate_id", "kind", "formula", "description")) + " |")
    lines.extend(["", "## Model Separation", "", "Paired means use only tasks scored for both models. Full means use every scored task in the model slice.", "", "| candidate | track | group | GPT paired mean | Qwen paired mean | GPT full mean | Qwen full mean | mean signed gap | mean abs gap | median abs gap | P90 abs gap | ties <0.01 | tie + answer distance >=1 | ranking reversals |", "| --- | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |"])
    for report_item in report["candidate_reports"]:
        candidate = report_item["candidate"]
        for pair in report_item["pair_metrics"]:
            lines.append("| " + " | ".join(md_value(value) for value in (candidate["candidate_id"], pair["track"], pair["group"], pair["gpt_paired_average_score"], pair["qwen_paired_average_score"], pair["gpt_full_average_score"], pair["qwen_full_average_score"], pair["mean_signed_gap"], pair["mean_absolute_gap"], pair["median_absolute_gap"], pair["p90_absolute_gap"], pair["tie_count_below_0_01"], pair["tie_with_answer_distance_ge_1_count"], pair["ranking_reversal_count"])) + " |")
    lines.extend(["", "## Direct Nonlinear Ranking", "", "This table compares direct error-to-score mappings with arithmetic-mean task aggregation. The official linear rule is included only as a baseline. All rows retain a nonzero tail for finite normalized errors.", "", "| rank | candidate | formula | average absolute model gap | maximum slice gap | ties <0.01 | ties with answer distance >=1 | ranking reversals |", "| ---: | --- | --- | ---: | ---: | ---: | ---: | ---: |"])
    for rank, item in enumerate(report["direct_nonlinear_summary"], start=1):
        lines.append("| " + " | ".join(md_value(value) for value in (rank, item["candidate_id"], item["formula"], item["average_absolute_model_gap"], item["maximum_absolute_model_gap"], item["total_ties_below_0_01"], item["total_ties_with_answer_distance_ge_1"], item["total_ranking_reversals"])) + " |")
    lines.extend(["", "## Distribution", "", "| candidate | model | track | group | mean | median | zero rate | high-score rate | interior rate | Spearman vs official | Kendall vs official |", "| --- | --- | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |"])
    for report_item in report["candidate_reports"]:
        candidate = report_item["candidate"]
        for item in report_item["model_metrics"]:
            lines.append("| " + " | ".join(md_value(value) for value in (candidate["candidate_id"], item["model"], item["track"], item["group"], item["average_score"], item["median_score"], item["zero_rate"], item["high_score_rate"], item["interior_rate"], item["spearman_vs_official"], item["kendall_tau_b_vs_official"])) + " |")
    lines.extend(["", "## Examples", "", "Each candidate has examples selected from near-ties, close answers, and model-order changes or largest candidate gaps.", "", "| candidate | reason | track | group | task_id | answer distance | official gap | GPT score | Qwen score |", "| --- | --- | --- | --- | --- | ---: | ---: | ---: | ---: |"])
    for report_item in report["candidate_reports"]:
        candidate = report_item["candidate"]
        for example in report_item["examples"]:
            lines.append("| " + " | ".join(md_value(value) for value in (candidate["candidate_id"], example["reason"], example["track"], example["group"], example["task_id"], example["answer_distance_max"], example["official_score_gap"], example["gpt_score"], example["qwen_score"])) + " |")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_report(
    comparison_path: Path,
    output_dir: Path,
    report: dict[str, Any],
    config: ReleaseConfig,
    *,
    force: bool = False,
) -> dict[str, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    names = {
        "json": output_dir / "shadow-scoring.json",
        "csv": output_dir / "shadow-scoring.csv",
        "markdown": output_dir / "shadow-scoring.md",
        "manifest": output_dir / "shadow-scoring-manifest.json",
    }
    if not force:
        existing = [path for path in names.values() if path.exists()]
        if existing:
            raise FileExistsError("Shadow outputs already exist: " + ", ".join(str(path) for path in existing))
    atomic_write_json(names["json"], report)
    write_csv(names["csv"], report)
    write_markdown(names["markdown"], report)
    manifest = {
        "schema_version": 1,
        "kind": "verifier_grounded_shadow_scoring_manifest",
        "generated_at": report["generated_at"],
        "official_scoring": report["official_scoring"],
        "release": config.identity,
        "source": {
            "comparison_json": str(comparison_path.resolve()),
            "comparison_json_sha256": sha256(comparison_path),
            "script": str(Path(__file__).resolve()),
            "script_sha256": sha256(Path(__file__).resolve()),
        },
        "files": {},
    }
    for key, path in names.items():
        if key == "manifest":
            continue
        manifest["files"][path.name] = {"size": path.stat().st_size, "sha256": sha256(path)}
    atomic_write_json(names["manifest"], manifest)
    return names


def default_comparison_path() -> Path:
    candidates = sorted(
        (path for path in runtime_paths.project_state_root.glob(REPORT_GLOB) if path.is_file()),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    if not candidates:
        raise FileNotFoundError("No comparison.json found under state/benchmark-rescore-reports")
    return candidates[0]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--comparison-json", type=Path, help="Existing rescore comparison.json; defaults to newest report.")
    parser.add_argument("--output-dir", type=Path, help="Output directory; defaults to the comparison.json directory.")
    parser.add_argument("--force", action="store_true", help="Overwrite only the four shadow output files.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    comparison_path = (args.comparison_json or default_comparison_path()).expanduser().resolve()
    if not comparison_path.is_file():
        raise FileNotFoundError(f"comparison.json does not exist: {comparison_path}")
    output_dir = (args.output_dir or comparison_path.parent).expanduser().resolve()
    config = load_release_config()
    context = load_context(config)
    payload = json.loads(comparison_path.read_text(encoding="utf-8"))
    report = analyze_comparison(payload, context)
    paths = write_report(comparison_path, output_dir, report, config, force=args.force)
    print(json.dumps({"outputs": {key: str(path) for key, path in paths.items()}, "records": len(report["records"])}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
