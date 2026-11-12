import pandas as pd
import pytest

from dp.features import (CATEGORICAL_FEATURES, NUMERIC_FEATURES, build_dataset, build_features,
                         snapshot_dates, snapshot_frame)

T = "2026-06-01"


def order(oid, cust, ts, status="delivered", promised=3, delivery=3.0, paid=0.0):
    return dict(order_id=oid, customer_id=cust, order_ts=pd.Timestamp(ts), status=status,
                promised_days=promised, delivery_days=delivery, paid_amount=paid)


def customers():
    return pd.DataFrame({
        "customer_id": ["C1", "C2", "C3", "C4", "C5"],
        "signup_date": pd.to_datetime(["2026-01-01", "2026-01-01", "2026-05-15",
                                       "2026-07-01", "2026-01-01"]).date,
        "city": ["Pune", "Delhi", "Pune", "Pune", "Pune"],
        "acquisition_channel": ["organic"] * 5,
        "marketing_opt_in": pd.array([True, False, True, True, True], dtype="boolean"),
    })


def orders():
    return pd.DataFrame([
        # C1: before T, one late delivery, one cancelled, one returned; one order after T
        order("O1", "C1", "2026-02-10 10:00:00", delivery=5.0, paid=100.0),
        order("O2", "C1", "2026-05-20 10:00:00", paid=200.0),
        order("O3", "C1", "2026-05-25 10:00:00", status="cancelled", delivery=float("nan")),
        order("O4", "C1", "2026-05-30 10:00:00", status="returned"),
        order("O5", "C1", "2026-06-10 10:00:00", paid=50.0),
        # C2: one order before T; after T only a cancelled one, and one exactly 90 days later
        order("O6", "C2", "2026-05-01 10:00:00", paid=80.0),
        order("O7", "C2", "2026-06-02 10:00:00", status="cancelled", delivery=float("nan")),
        order("O8", "C2", "2026-08-30 00:00:00", paid=80.0),
        # C5: its only order is exactly at the snapshot, so it has no history yet
        order("O9", "C5", "2026-06-01 00:00:00", paid=10.0),
    ])


def test_feature_values_for_one_customer():
    f = build_features(orders(), customers(), T).set_index("customer_id")
    c1 = f.loc["C1"]
    assert c1["orders_total"] == 3                    # cancelled order is not counted
    assert c1["recency_days"] == 1                    # 30 May 10:00 to 1 Jun 00:00
    assert c1["tenure_days"] == 151
    assert c1["orders_last_30d"] == 2 and c1["orders_last_90d"] == 2
    assert c1["spend_total"] == 300.0 and c1["spend_last_90d"] == 200.0
    assert c1["spend_per_order"] == 100.0
    assert c1["avg_gap_days"] == 54.5                 # 109 days between first and last / 2 gaps
    assert c1["cancel_rate"] == 0.25                  # 1 of 4 orders
    assert c1["return_rate"] == pytest.approx(1 / 3)  # 1 returned of 3 delivered or returned
    assert c1["late_delivery_rate"] == 0.5            # 1 of 2 delivered orders was late
    assert c1["marketing_opt_in"] == 1.0 and c1["city"] == "Pune"


def test_only_customers_with_history_who_signed_up_are_included():
    f = build_features(orders(), customers(), T)
    # C3 never ordered, C4 signs up later, C5 has no order before the snapshot
    assert f["customer_id"].tolist() == ["C1", "C2"]
    assert f["avg_gap_days"].isna().tolist() == [False, True]  # C2 has a single order


def test_features_never_see_the_future():
    base = build_features(orders(), customers(), T)
    later = pd.concat([orders(), pd.DataFrame([
        order("X1", "C1", "2026-06-01 00:00:01", paid=999.0),
        order("X2", "C2", "2026-07-15 10:00:00", paid=999.0),
        order("X3", "C1", "2027-01-01 10:00:00", status="returned"),
    ])], ignore_index=True)
    pd.testing.assert_frame_equal(base, build_features(later, customers(), T))


def test_label_uses_a_window_that_includes_t_and_excludes_t_plus_90_days():
    f = snapshot_frame(orders(), customers(), T).set_index("customer_id")
    assert f.loc["C1", "churned"] == 0  # ordered on 10 Jun
    # C2: the 2 Jun order is cancelled, the 30 Aug 00:00 order is exactly day 90 (outside)
    assert f.loc["C2", "churned"] == 1


def test_scoring_frame_has_no_label():
    f = snapshot_frame(orders(), customers(), T, with_label=False)
    assert "churned" not in f.columns


def test_snapshot_dates_keep_the_whole_label_window_inside_the_data():
    d = snapshot_dates("2026-10-31")
    assert d[0] == pd.Timestamp("2025-08-01") and d[-1] == pd.Timestamp("2026-08-01")
    assert len(d) == 13
    # 1 Sep + 90 days would end on 30 Nov, after the data stops
    assert pd.Timestamp("2026-09-01") not in d


def test_build_dataset_returns_labelled_snapshots_and_one_scoring_snapshot():
    labelled, scoring = build_dataset(orders(), customers(), "2026-10-31", first_snapshot="2026-05-01")
    assert set(labelled["snapshot_date"]) == {pd.Timestamp("2026-05-01"), pd.Timestamp("2026-06-01"),
                                              pd.Timestamp("2026-07-01"), pd.Timestamp("2026-08-01")}
    assert set(labelled["churned"]) <= {0, 1}
    assert scoring["snapshot_date"].unique().tolist() == [pd.Timestamp("2026-11-01")]
    assert "churned" not in scoring.columns
