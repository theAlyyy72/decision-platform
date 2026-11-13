"""Train and honestly evaluate the churn model.

Rules that keep the evaluation honest:
- Split by TIME, never at random. Train < validation < test, in snapshot order.
- A gap of one full label window (90 days) sits between the sets, so no training label is
  decided by orders that happen during the validation or test period.
- Models are compared and tuned on validation only. The test set is scored once, at the end.
- Everything is compared with two simple baselines, because 7 in 10 customers churn
  and a model has to beat "just say everyone churns" and "no order for 60 days = churn".

Input : data/lake/ml/churn_dataset.parquet (from dp.features)
Output: data/models/churn_model.joblib, data/models/metrics.json, docs/model_report.md

Run: python3 -m dp.train
"""
import argparse
import json
import sys
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
from sklearn.compose import ColumnTransformer
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.impute import SimpleImputer
from sklearn.inspection import permutation_importance
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import average_precision_score, precision_score, recall_score, roc_auc_score
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder, StandardScaler

from dp.features import CATEGORICAL_FEATURES, HORIZON_DAYS, NUMERIC_FEATURES

FEATURES = NUMERIC_FEATURES + CATEGORICAL_FEATURES
BASELINE_DAYS = 60
TOP_FRACTION = 0.10


def split_by_time(df, horizon=HORIZON_DAYS, n_val=2, n_test=1):
    """Returns {'train': df, 'val': df, 'test': df} cut by snapshot date, with a label-window gap."""
    gap = pd.Timedelta(days=horizon)
    dates = sorted(df["snapshot_date"].unique())
    test = dates[-n_test:]
    val = [d for d in dates if d + gap <= test[0]][-n_val:]
    train = [d for d in dates if d + gap <= val[0]]
    if not train or not val:
        raise ValueError("not enough snapshots for train, validation and test with a gap")
    pick = lambda ds: df[df["snapshot_date"].isin(ds)].reset_index(drop=True)
    return {"train": pick(train), "val": pick(val), "test": pick(test)}


def logistic(C):
    num = Pipeline([("impute", SimpleImputer(strategy="median", add_indicator=True)),
                    ("scale", StandardScaler())])
    prep = ColumnTransformer([("num", num, NUMERIC_FEATURES),
                              ("cat", OneHotEncoder(handle_unknown="ignore"), CATEGORICAL_FEATURES)])
    return Pipeline([("prep", prep), ("model", LogisticRegression(C=C, max_iter=2000))])


def boosting(learning_rate, max_depth, max_iter):
    prep = ColumnTransformer([("num", "passthrough", NUMERIC_FEATURES),
                              ("cat", OneHotEncoder(handle_unknown="ignore", sparse_output=False),
                               CATEGORICAL_FEATURES)])
    model = HistGradientBoostingClassifier(learning_rate=learning_rate, max_depth=max_depth,
                                           max_iter=max_iter, l2_regularization=1.0, random_state=0)
    return Pipeline([("prep", prep), ("model", model)])


CANDIDATES = {
    "logistic_C0.1": lambda: logistic(0.1),
    "logistic_C1": lambda: logistic(1.0),
    "boosting_lr0.05_depth3": lambda: boosting(0.05, 3, 200),
    "boosting_lr0.1_depth4": lambda: boosting(0.1, 4, 200),
    "boosting_lr0.05_depth6": lambda: boosting(0.05, 6, 300),
}


def rule_baseline(df, days=BASELINE_DAYS):
    """Predict churn when the customer has not ordered for more than `days` days."""
    return (df["recency_days"] > days).astype(int).to_numpy()


def evaluate(y, score, pred):
    """Metrics for one set. `score` ranks customers (higher = more likely to churn)."""
    y, score = np.asarray(y), np.asarray(score, dtype=float)
    n_top = max(1, int(round(len(y) * TOP_FRACTION)))
    top = np.argsort(-score, kind="stable")[:n_top]
    prevalence = float(y.mean())
    p_top = float(y[top].mean())
    return {
        "rows": int(len(y)),
        "prevalence": round(prevalence, 4),
        "pr_auc": round(float(average_precision_score(y, score)), 4),
        "roc_auc": round(float(roc_auc_score(y, score)), 4),
        "precision_top10pct": round(p_top, 4),
        "lift_top10pct": round(p_top / prevalence, 3) if prevalence else None,
        "precision": round(float(precision_score(y, pred, zero_division=0)), 4),
        "recall": round(float(recall_score(y, pred, zero_division=0)), 4),
    }


def baselines(df):
    y = df["churned"].to_numpy()
    return {
        f"rule: no order for {BASELINE_DAYS}+ days": evaluate(y, df["recency_days"], rule_baseline(df)),
        "everyone churns": evaluate(y, np.zeros(len(y)), np.ones(len(y), dtype=int)),
    }


def model_metrics(model, df):
    proba = model.predict_proba(df[FEATURES])[:, 1]
    return evaluate(df["churned"], proba, (proba >= 0.5).astype(int))


def train(labelled, models_dir="data/models", report_path="docs/model_report.md", horizon=HORIZON_DAYS):
    """Compare candidates on validation, refit the best on train + validation, score test once."""
    sets = split_by_time(labelled, horizon)
    tr, va, te = sets["train"], sets["val"], sets["test"]

    validation = {}
    for name, make in CANDIDATES.items():
        model = make().fit(tr[FEATURES], tr["churned"])
        validation[name] = model_metrics(model, va)
    best = max(validation, key=lambda n: validation[n]["pr_auc"])

    final = CANDIDATES[best]().fit(pd.concat([tr, va])[FEATURES], pd.concat([tr, va])["churned"])
    test = {"model: " + best: model_metrics(final, te)}  # the one and only look at the test set
    test.update(baselines(te))

    imp = permutation_importance(final, te[FEATURES], te["churned"], scoring="average_precision",
                                 n_repeats=3, random_state=0)
    importance = (pd.Series(imp.importances_mean, index=FEATURES).sort_values(ascending=False)
                  .round(4).to_dict())

    info = {
        "horizon_days": horizon, "best_model": best,
        "splits": {k: {"snapshots": [str(pd.Timestamp(d).date()) for d in sorted(v["snapshot_date"].unique())],
                       "rows": len(v), "churn_rate": round(float(v["churned"].mean()), 4)}
                   for k, v in sets.items()},
        "validation": validation, "validation_baselines": baselines(va),
        "test": test, "feature_importance": importance,
    }
    models_dir = Path(models_dir)
    models_dir.mkdir(parents=True, exist_ok=True)
    joblib.dump({"model": final, "features": FEATURES, "name": best, "horizon_days": horizon},
                models_dir / "churn_model.joblib")
    (models_dir / "metrics.json").write_text(json.dumps(info, indent=2))
    write_report(Path(report_path), info)
    return info


def table(rows):
    cols = ["pr_auc", "roc_auc", "precision_top10pct", "lift_top10pct", "precision", "recall"]
    head = "| Model | " + " | ".join(cols) + " |\n|---|" + "---|" * len(cols) + "\n"
    return head + "\n".join(f"| {name} | " + " | ".join(str(m[c]) for c in cols) + " |"
                            for name, m in rows.items()) + "\n"


def write_report(path, info):
    s = info["splits"]
    lines = [
        "# Churn model report", "",
        f"Churn = no non-cancelled order in the {info['horizon_days']} days after the snapshot date.",
        "Generated by `python3 -m dp.train`. Do not edit by hand.", "",
        "## Split (by time, with a gap)", "",
        "| Set | Snapshots | Rows | Churn rate |", "|---|---|---|---|",
    ]
    for k in ("train", "val", "test"):
        snaps = s[k]["snapshots"]
        lines.append(f"| {k} | {snaps[0]} to {snaps[-1]} ({len(snaps)}) | {s[k]['rows']:,} | {s[k]['churn_rate']:.1%} |")
    lines += [
        "", f"A gap of {info['horizon_days']} days separates the sets, so no training label depends on orders",
        "placed in the validation or test period.", "",
        "## Validation (used to pick the model)", "",
        table({**info["validation"], **{"baseline " + k: v for k, v in info["validation_baselines"].items()}}),
        f"Chosen: `{info['best_model']}` (highest validation PR-AUC), then refit on train + validation.", "",
        "## Test (scored once)", "", table(info["test"]),
        "Columns: `precision_top10pct` is the share of real churners among the 10% of customers the model",
        "ranks as riskiest; `lift_top10pct` divides that by the overall churn rate. `precision` and `recall`",
        "use a 0.5 probability threshold (for the rule baseline, the rule itself).", "",
        "## What drives the predictions", "",
        "Drop in test PR-AUC when one feature is shuffled (bigger = more important).", "",
        "| Feature | Importance |", "|---|---|",
    ]
    lines += [f"| {k} | {v} |" for k, v in list(info["feature_importance"].items())[:8]]
    lines += [
        "", "## Caveats", "",
        "- The data is synthetic and its churn behaviour was written by the generator, so these numbers",
        "  show that the pipeline is sound, not how a real model would do.",
        "- The same customer appears in several snapshots. Splitting by time keeps their future out of",
        "  training, but rows are not independent.",
        "- Customers whose row was quarantined are not in the dataset (see the known gaps in `docs/kpi_definitions.md`).", "",
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--lake", default="data/lake")
    ap.add_argument("--models-dir", default="data/models")
    ap.add_argument("--report", default="docs/model_report.md")
    args = ap.parse_args()
    path = Path(args.lake) / "ml" / "churn_dataset.parquet"
    if not path.exists():
        print(f"{path} not found. Run dp.features first.")
        sys.exit(1)
    info = train(pd.read_parquet(path), args.models_dir, args.report)
    print(f"Best model: {info['best_model']}\n")
    print("Test set (scored once):")
    for name, m in info["test"].items():
        print(f"  {name:<34} PR-AUC {m['pr_auc']:<7} ROC-AUC {m['roc_auc']:<7} lift@10% {m['lift_top10pct']}")
    print(f"\nReport: {args.report}")


if __name__ == "__main__":
    main()
