import numpy as np
import pandas as pd
import pytest

from dp.features import CATEGORICAL_FEATURES, NUMERIC_FEATURES
from dp.train import evaluate, rule_baseline, split_by_time, train


def fake_dataset(n_per_snapshot=300, seed=0):
    """13 monthly snapshots. Churn depends on recency, so a model can learn it."""
    rng = np.random.default_rng(seed)
    frames = []
    for d in pd.date_range("2025-08-01", periods=13, freq="MS"):
        recency = rng.exponential(60, n_per_snapshot)
        churn = rng.random(n_per_snapshot) < 1 / (1 + np.exp(-(recency - 60) / 25))
        df = pd.DataFrame({c: rng.normal(size=n_per_snapshot) for c in NUMERIC_FEATURES})
        df["recency_days"] = recency
        df["city"] = rng.choice(["Pune", "Delhi"], n_per_snapshot)
        df["acquisition_channel"] = rng.choice(["organic", "social"], n_per_snapshot)
        df["customer_id"] = [f"C{i}" for i in range(n_per_snapshot)]
        df["snapshot_date"] = d
        df["churned"] = churn.astype(int)
        frames.append(df)
    return pd.concat(frames, ignore_index=True)


def test_split_keeps_a_label_window_gap_between_sets():
    s = split_by_time(fake_dataset(10))
    dates = {k: sorted(v["snapshot_date"].unique()) for k, v in s.items()}
    assert dates["test"] == [pd.Timestamp("2026-08-01")]
    assert dates["val"] == [pd.Timestamp("2026-04-01"), pd.Timestamp("2026-05-01")]
    assert dates["train"][-1] == pd.Timestamp("2026-01-01")
    gap = pd.Timedelta(days=90)
    assert dates["train"][-1] + gap <= dates["val"][0]
    assert dates["val"][-1] + gap <= dates["test"][0]
    assert sum(len(v) for v in s.values()) < len(fake_dataset(10))  # the gap snapshots are dropped


def test_split_needs_enough_snapshots():
    df = fake_dataset(10)
    with pytest.raises(ValueError):
        split_by_time(df[df["snapshot_date"] < "2025-12-01"])


def test_rule_baseline_flags_customers_silent_for_more_than_60_days():
    df = pd.DataFrame({"recency_days": [100, 30, 80, 10, 60]})
    assert rule_baseline(df).tolist() == [1, 0, 1, 0, 0]  # exactly 60 days is not enough


def test_evaluate_a_perfect_ranking():
    y = np.array([1, 1, 0, 0, 0, 0, 0, 0, 0, 0])
    score = np.array([0.9, 0.8, 0.1, 0.2, 0.1, 0.1, 0.1, 0.1, 0.1, 0.1])
    m = evaluate(y, score, (score >= 0.5).astype(int))
    assert m["pr_auc"] == 1.0 and m["roc_auc"] == 1.0
    assert m["precision_top10pct"] == 1.0           # the single riskiest customer is a churner
    assert m["lift_top10pct"] == 5.0                # 1.0 / prevalence 0.2
    assert m["precision"] == 1.0 and m["recall"] == 1.0


def test_evaluate_rule_precision_and_recall():
    y = np.array([1, 1, 0, 0])
    pred = np.array([1, 0, 1, 0])
    m = evaluate(y, np.array([100, 30, 80, 10]), pred)
    assert m["precision"] == 0.5 and m["recall"] == 0.5


def test_training_end_to_end_beats_the_baselines_and_writes_outputs(tmp_path):
    info = train(fake_dataset(), tmp_path / "models", tmp_path / "report.md")
    m = info["test"]["model: " + info["best_model"]]
    assert m["pr_auc"] > m["prevalence"] + 0.05                       # better than "everyone churns"
    assert m["pr_auc"] >= info["test"]["everyone churns"]["pr_auc"]
    assert (tmp_path / "models" / "churn_model.joblib").exists()
    assert (tmp_path / "models" / "metrics.json").exists()
    text = (tmp_path / "report.md").read_text()
    assert "## Test (scored once)" in text and info["best_model"] in text
    assert info["feature_importance"]["recency_days"] == max(info["feature_importance"].values())
