#!/usr/bin/env python3
"""Train and qualify the V15 shared encoder and shadow COG advisor."""

from __future__ import annotations

import argparse
import hashlib
import json
import random
from dataclasses import asdict
from pathlib import Path

import numpy as np
import torch

from aethersparse.cognitive.graph import compact_view
from aethersparse.cognitive.interpreter import InputStateInterpreter
from aethersparse.cognitive.models import InputType
from aethersparse.experiments.shared_encoder import (
    COG_FIELDS,
    COG_LABELS,
    classification_metrics,
    cog_parameter_count,
    encode_texts,
    encoder_parameter_count,
    iter_named_parameters,
    make_cog_advisor,
    make_encoder,
    retrieval_metrics,
    set_cosine,
    sparse_hash_baseline,
    train_cog_advisor,
    train_shared_encoder,
)

SEED = 15015


def canonical_bytes(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def answer_cases(cases: list[dict[str, object]], partition: str) -> list[dict[str, object]]:
    return [
        case
        for case in cases
        if case["partition"] == partition
        and case["accepted_disposition"] == "ANSWER"
        and case["gold_evidence"]
    ]


def evidence_pool(
    cases: list[dict[str, object]],
) -> tuple[list[str], dict[str, int], list[set[int]]]:
    texts: list[str] = []
    indexes: dict[str, int] = {}
    gold: list[set[int]] = []
    for case in cases:
        row: set[int] = set()
        for item in case["gold_evidence"]:  # type: ignore[union-attr]
            span = str(item["span_id"])
            if span not in indexes:
                indexes[span] = len(texts)
                texts.append(str(item["exact_text"]))
            row.add(indexes[span])
        gold.append(row)
    return texts, indexes, gold


def cog_features(
    cases: list[dict[str, object]],
    all_cases: list[dict[str, object]],
) -> torch.Tensor:
    by_id = {str(case["case_id"]): case for case in all_cases}
    interpreter = InputStateInterpreter()
    rows: list[list[float]] = []
    for case in cases:
        prior_entities: list[str] = []
        for prior_id in case.get("prior_case_ids", []):  # type: ignore[union-attr]
            prior = by_id.get(str(prior_id))
            if prior:
                prior_entities.extend(str(x) for x in prior["required_entity_ids"])
        interpreted = interpreter.interpret(
            InputType.NATURAL_LANGUAGE,
            str(case["question"]),
            input_id=str(case["case_id"]),
            prior_entity_ids=tuple(prior_entities),
        )
        packed = compact_view(interpreted.graph).packed_u16()
        if len(packed) != COG_FIELDS:
            raise AssertionError(f"unexpected COG width: {len(packed)}")
        rows.append([min(1.0, float(value) / 1000.0) for value in packed])
    return torch.tensor(rows, dtype=torch.float32)


def export_int8(
    artifact_dir: Path, encoder: object, advisor: object
) -> tuple[Path, dict[str, float]]:
    artifact_dir.mkdir(parents=True, exist_ok=True)
    arrays: dict[str, np.ndarray] = {}
    scales: dict[str, float] = {}
    for name, tensor in iter_named_parameters(
        (("encoder", encoder), ("cog_advisor", advisor))
    ):
        value = tensor.detach().cpu().numpy().astype(np.float32)
        scale = float(np.max(np.abs(value)) / 127.0) if value.size else 1.0
        if scale == 0.0:
            scale = 1.0
        arrays[name] = np.clip(np.rint(value / scale), -127, 127).astype(np.int8)
        scales[name] = scale
    path = artifact_dir / "v15-shadow-shared-encoder-cog-int8.npz"
    np.savez_compressed(path, **arrays)
    (artifact_dir / "v15-shadow-shared-encoder-cog-scales.json").write_bytes(
        canonical_bytes(scales) + b"\n"
    )
    return path, scales


def load_dequantized(
    artifact: Path, scales: dict[str, float], encoder: object, advisor: object
) -> None:
    arrays = np.load(artifact)
    for prefix, model in (("encoder", encoder), ("cog_advisor", advisor)):
        state = model.state_dict()
        for name, tensor in state.items():
            key = f"{prefix}.{name}"
            value = arrays[key].astype(np.float32) * scales[key]
            tensor.copy_(torch.from_numpy(value))
        model.load_state_dict(state)


def main() -> int:
    parser = argparse.ArgumentParser()
    repo = Path(__file__).resolve().parents[2]
    parser.add_argument(
        "--benchmark",
        type=Path,
        default=repo
        / "data/v050/benchmark/INDEPENDENT_NATURAL_QUERY_SET_V050_R1.json",
    )
    parser.add_argument(
        "--artifact-dir",
        type=Path,
        default=Path("/media/cloud/2982-E16B/work/v15-p4-deployment/models"),
    )
    parser.add_argument(
        "--report",
        type=Path,
        default=repo
        / "reports/droid/v15/experimental-shared-encoder-qualification.json",
    )
    parser.add_argument("--encoder-epochs", type=int, default=4)
    parser.add_argument("--cog-epochs", type=int, default=40)
    args = parser.parse_args()

    random.seed(SEED)
    np.random.seed(SEED)
    torch.manual_seed(SEED)
    benchmark = json.loads(args.benchmark.read_text(encoding="utf-8"))
    cases: list[dict[str, object]] = benchmark["cases"]
    development_answers = answer_cases(cases, "development")
    tuning_answers = answer_cases(cases, "tuning")
    training_pairs = [
        (str(case["question"]), str(case["gold_evidence"][0]["exact_text"]))  # type: ignore[index]
        for case in development_answers
    ]

    encoder = make_encoder()
    encoder_losses = train_shared_encoder(
        encoder, training_pairs, epochs=args.encoder_epochs, seed=SEED
    )

    tuning_evidence, _, tuning_gold = evidence_pool(tuning_answers)
    tuning_questions = [str(case["question"]) for case in tuning_answers]
    question_vectors = encode_texts(encoder, tuning_questions)
    evidence_vectors = encode_texts(encoder, tuning_evidence)
    learned_retrieval = retrieval_metrics(
        question_vectors, evidence_vectors, tuning_gold
    )

    baseline_evidence = [sparse_hash_baseline(text) for text in tuning_evidence]
    baseline_ranks: list[int] = []
    for question, gold in zip(tuning_questions, tuning_gold, strict=True):
        query = sparse_hash_baseline(question)
        ranked = sorted(
            range(len(baseline_evidence)),
            key=lambda index: (-set_cosine(query, baseline_evidence[index]), index),
        )
        baseline_ranks.append(
            next(index + 1 for index, item in enumerate(ranked) if item in gold)
        )
    baseline = {
        "recall_at_1": sum(rank <= 1 for rank in baseline_ranks)
        / len(baseline_ranks),
        "recall_at_5": sum(rank <= 5 for rank in baseline_ranks)
        / len(baseline_ranks),
        "recall_at_10": sum(rank <= 10 for rank in baseline_ranks)
        / len(baseline_ranks),
        "mean_reciprocal_rank": sum(1.0 / rank for rank in baseline_ranks)
        / len(baseline_ranks),
    }

    development = [case for case in cases if case["partition"] == "development"]
    tuning = [case for case in cases if case["partition"] == "tuning"]
    with torch.no_grad():
        development_vectors = encode_texts(
            encoder, [str(case["question"]) for case in development]
        )
        tuning_vectors = encode_texts(
            encoder, [str(case["question"]) for case in tuning]
        )
    development_cog = cog_features(development, cases)
    tuning_cog = cog_features(tuning, cases)
    label_index = {label: index for index, label in enumerate(COG_LABELS)}
    development_labels = [
        label_index[str(case["accepted_disposition"])] for case in development
    ]
    tuning_labels = [
        label_index[str(case["accepted_disposition"])] for case in tuning
    ]
    advisor = make_cog_advisor()
    cog_losses = train_cog_advisor(
        advisor,
        development_vectors.detach(),
        development_cog,
        development_labels,
        epochs=args.cog_epochs,
        seed=SEED,
    )
    advisor.eval()
    with torch.no_grad():
        predictions = advisor(tuning_vectors, tuning_cog).argmax(dim=1).tolist()
    cog_metrics = classification_metrics(predictions, tuning_labels)
    majority = max(
        development_labels.count(index) for index in range(len(COG_LABELS))
    ) / len(development_labels)

    artifact, scales = export_int8(args.artifact_dir, encoder, advisor)
    int8_encoder = make_encoder()
    int8_advisor = make_cog_advisor()
    load_dequantized(artifact, scales, int8_encoder, int8_advisor)
    int8_question_vectors = encode_texts(int8_encoder, tuning_questions)
    int8_evidence_vectors = encode_texts(int8_encoder, tuning_evidence)
    int8_retrieval = retrieval_metrics(
        int8_question_vectors, int8_evidence_vectors, tuning_gold
    )
    float_top1 = (question_vectors @ evidence_vectors.T).argmax(dim=1)
    int8_top1 = (int8_question_vectors @ int8_evidence_vectors.T).argmax(dim=1)
    rank_agreement = float((float_top1 == int8_top1).float().mean())
    int8_tuning_vectors = encode_texts(
        int8_encoder, [str(case["question"]) for case in tuning]
    )
    int8_advisor.eval()
    with torch.no_grad():
        int8_predictions = int8_advisor(int8_tuning_vectors, tuning_cog).argmax(
            dim=1
        ).tolist()
    int8_cog_metrics = classification_metrics(int8_predictions, tuning_labels)
    disposition_agreement = sum(
        left == right for left, right in zip(predictions, int8_predictions, strict=True)
    ) / len(predictions)
    report = {
        "schema_version": "v15-experimental-learned-probe-1",
        "authority": "SHADOW_ONLY",
        "runtime_controller_replaced": False,
        "semantic_address_artifact": False,
        "seed": SEED,
        "benchmark": {
            "path": str(args.benchmark.relative_to(repo)),
            "sha256": sha256_file(args.benchmark),
            "development_cases_used": len(development),
            "tuning_cases_used": len(tuning),
            "evaluation_cases_used": 0,
            "final_held_cases_used": 0,
        },
        "encoder": {
            "architecture": "shared-hashed-ngram-embeddingbag-192-1536-192",
            "parameter_count": encoder_parameter_count(),
            "training_pairs": len(training_pairs),
            "epochs": args.encoder_epochs,
            "losses": encoder_losses,
            "tuning_candidate_spans": len(tuning_evidence),
            "tuning_metrics": asdict(learned_retrieval),
            "int8_tuning_metrics": asdict(int8_retrieval),
            "float_int8_top1_agreement": rank_agreement,
            "static_hash_baseline": baseline,
        },
        "cog_advisor": {
            "architecture": "encoder192+cog19-512-288-5",
            "parameter_count": cog_parameter_count(),
            "labels": COG_LABELS,
            "epochs": args.cog_epochs,
            "losses": cog_losses,
            "tuning_metrics": cog_metrics,
            "int8_tuning_metrics": int8_cog_metrics,
            "float_int8_disposition_agreement": disposition_agreement,
            "majority_development_accuracy": majority,
        },
        "combined_parameter_count": encoder_parameter_count()
        + cog_parameter_count(),
        "int8_artifact": {
            "external_path": str(artifact),
            "sha256": sha256_file(artifact),
            "bytes": artifact.stat().st_size,
            "tensor_count": len(scales),
        },
        "deployment": {
            "enabled": False,
            "reason": (
                "experimental advisor only; deterministic request typing, "
                "legal mask, exact verifier, and support gate remain authoritative"
            ),
        },
    }
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_bytes(canonical_bytes(report) + b"\n")
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
