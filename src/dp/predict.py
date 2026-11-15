"""Score every active customer with the saved churn model (batch predictions).

- input : ml/churn_scoring.parquet (features at the end of the data, from dp.features)
          and the model saved by dp.train
- output: curated/churn_scores/churn_scores.parquet, one row per customer

Risk bands are by rank, not by probability, because the model's probabilities sit close
together (7 in 10 customers churn). high = riskiest 10%, medium = next 30%, low = the rest.
The top 10% is the group the evaluation in docs/model_report.md measures.

Run: python3 -m dp.predict
"""
import argparse
import sys
from pathlib import Path

import joblib
import numpy as np
import pandas as pd

HIGH_FRACTION = 0.10
MEDIUM_FRACTION = 0.30
CONTEXT_COLUMNS = ["recency_days", "orders_total", "spend_total"]


def assign_bands(probability):
    """Rank 1 = riskiest. Ties are broken by row order, so the band sizes are always exact."""
    rank = probability.rank(method="first", ascending=False)
    n = len(probability)
    band = np.where(rank <= n * HIGH_FRACTION, "high",
                    np.where(rank <= n * (HIGH_FRACTION + MEDIUM_FRACTION), "medium", "low"))
    return rank.astype(int), pd.Series(band, index=probability.index)


def score(frame, bundle):
    """Churn probability, risk rank and band for every row of `frame`."""
    missing = [f for f in bundle["features"] if f not in frame.columns]
    if missing:
        raise ValueError(f"scoring data is missing features: {', '.join(missing)}")
    probability = pd.Series(bundle["model"].predict_proba(frame[bundle["features"]])[:, 1],
                            index=frame.index)
    rank, band = assign_bands(probability)
    out = pd.DataFrame({
        "customer_id": frame["customer_id"],
        "snapshot_date": frame["snapshot_date"],
        "churn_probability": probability.round(4),
        "risk_rank": rank,
        "risk_band": band,
    })
    for c in CONTEXT_COLUMNS:
        out[c] = frame[c]
    out["model_name"] = bundle["name"]
    return out.sort_values("risk_rank").reset_index(drop=True)


def predict(lake_dir, models_dir="data/models"):
    lake = Path(lake_dir)
    scoring = pd.read_parquet(lake / "ml" / "churn_scoring.parquet")
    bundle = joblib.load(Path(models_dir) / "churn_model.joblib")
    scores = score(scoring, bundle)
    dest = lake / "curated" / "churn_scores" / "churn_scores.parquet"
    dest.parent.mkdir(parents=True, exist_ok=True)
    scores.to_parquet(dest, index=False)
    return scores


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--lake", default="data/lake")
    ap.add_argument("--models-dir", default="data/models")
    args = ap.parse_args()
    try:
        scores = predict(args.lake, args.models_dir)
    except FileNotFoundError as e:
        print(f"{e}\nRun dp.features and dp.train first.")
        sys.exit(1)
    print(f"Scored {len(scores):,} customers as of {scores['snapshot_date'].iloc[0].date()} "
          f"with {scores['model_name'].iloc[0]}")
    for band, n in scores["risk_band"].value_counts().reindex(["high", "medium", "low"]).items():
        print(f"  {band:<7}{n:>8,}")
    print("-> curated/churn_scores/churn_scores.parquet")


if __name__ == "__main__":
    main()
