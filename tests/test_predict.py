import pandas as pd
import pytest

from dp.features import NUMERIC_FEATURES
from dp.predict import assign_bands, predict, score
from dp.train import CANDIDATES, FEATURES, train
from test_train import fake_dataset


def bundle_from(df):
    model = CANDIDATES["logistic_C1"]().fit(df[FEATURES], df["churned"])
    return {"model": model, "features": FEATURES, "name": "logistic_C1", "horizon_days": 90}


def test_band_sizes_are_exact_and_the_riskiest_customer_is_high():
    prob = pd.Series([i / 100 for i in range(100)])
    rank, band = assign_bands(prob)
    assert band.value_counts().to_dict() == {"low": 60, "medium": 30, "high": 10}
    assert rank[99] == 1 and band[99] == "high"       # probability 0.99
    assert band[0] == "low" and rank[0] == 100        # probability 0.00


def test_band_sizes_stay_exact_when_every_probability_is_the_same():
    _, band = assign_bands(pd.Series([0.7] * 50))
    assert band.value_counts().to_dict() == {"low": 30, "medium": 15, "high": 5}


def test_scores_are_probabilities_ranked_from_riskiest():
    df = fake_dataset(200)
    last = df[df["snapshot_date"] == df["snapshot_date"].max()].drop(columns="churned")
    out = score(last, bundle_from(df))
    assert len(out) == len(last) and out["customer_id"].is_unique
    assert out["churn_probability"].between(0, 1).all()
    assert out["risk_rank"].tolist() == list(range(1, len(out) + 1))
    assert out["churn_probability"].is_monotonic_decreasing
    assert not out.isna().any().any()
    assert set(out["risk_band"]) == {"high", "medium", "low"}


def test_high_risk_customers_really_churn_more_than_low_risk_ones():
    df = fake_dataset(400)
    train_part = df[df["snapshot_date"] < "2026-06-01"]
    held_out = df[df["snapshot_date"] == "2026-08-01"]
    out = score(held_out.drop(columns="churned"), bundle_from(train_part))
    actual = held_out.set_index("customer_id")["churned"]
    rate = out.groupby("risk_band")["customer_id"].apply(lambda ids: actual.loc[ids].mean())
    assert rate["high"] > rate["medium"] > rate["low"]


def test_missing_feature_column_is_refused():
    df = fake_dataset(100)
    bundle = bundle_from(df)
    broken = df.drop(columns=["recency_days"])
    with pytest.raises(ValueError):
        score(broken, bundle)


def test_predict_writes_one_row_per_customer(tmp_path):
    pytest.importorskip("pyarrow")
    df = fake_dataset(150)
    lake = tmp_path / "lake"
    (lake / "ml").mkdir(parents=True)
    labelled = df[df["snapshot_date"] <= "2026-08-01"]
    scoring = df[df["snapshot_date"] == "2026-08-01"].drop(columns="churned")
    labelled.to_parquet(lake / "ml" / "churn_dataset.parquet", index=False)
    scoring.to_parquet(lake / "ml" / "churn_scoring.parquet", index=False)
    train(labelled, tmp_path / "models", tmp_path / "report.md")
    scores = predict(lake, tmp_path / "models")
    back = pd.read_parquet(lake / "curated" / "churn_scores" / "churn_scores.parquet")
    assert len(back) == len(scoring) == len(scores)


def test_predict_without_a_model_raises(tmp_path):
    pytest.importorskip("pyarrow")
    lake = tmp_path / "lake"
    (lake / "ml").mkdir(parents=True)
    fake_dataset(20).drop(columns="churned").to_parquet(lake / "ml" / "churn_scoring.parquet", index=False)
    with pytest.raises(FileNotFoundError):
        predict(lake, tmp_path / "no_models")
