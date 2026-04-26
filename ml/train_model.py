from __future__ import annotations

import math
from pathlib import Path
from typing import Iterable

import joblib
import numpy as np
import pandas as pd
from sklearn.compose import ColumnTransformer
from sklearn.ensemble import RandomForestClassifier
from sklearn.metrics import (
    accuracy_score,
    average_precision_score,
    classification_report,
    f1_score,
    precision_score,
    recall_score,
    roc_auc_score,
)
from sklearn.model_selection import train_test_split
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder


RANDOM_STATE = 42

ROOT_DIR = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT_DIR / "data"
MODEL_DIR = ROOT_DIR / "ml"
MODEL_PATH = MODEL_DIR / "fraud_model.pkl"

SYNTHETIC_DATA_PATH = DATA_DIR / "synthetic_transactions.csv"
KAGGLE_TRANSACTION_PATH = DATA_DIR / "train_transaction.csv"
KAGGLE_IDENTITY_PATH = DATA_DIR / "train_identity.csv"

RAW_COLUMNS = [
    "user_id",
    "amount",
    "device",
    "location",
    "transaction_hour",
    "isFraud",
]

CATEGORICAL_FEATURES = ["device", "location"]
NUMERIC_FEATURES = [
    "amount",
    "transaction_hour",
    "user_avg_amount",
    "amount_ratio_to_avg",
    "user_transaction_count",
    "is_new_device",
    "is_new_location",
    "is_night_transaction",
]
FEATURE_COLUMNS = CATEGORICAL_FEATURES + NUMERIC_FEATURES


def sigmoid(value: float) -> float:
    return 1.0 / (1.0 + math.exp(-value))


def ensure_directories() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    MODEL_DIR.mkdir(parents=True, exist_ok=True)


def generate_synthetic_transactions(
    output_path: Path = SYNTHETIC_DATA_PATH,
    n_transactions: int = 15_000,
    n_users: int = 600,
    random_state: int = RANDOM_STATE,
) -> pd.DataFrame:
    rng = np.random.default_rng(random_state)

    user_ids = np.array([f"user_{idx:03d}" for idx in range(n_users)])
    user_weights = rng.gamma(shape=2.0, scale=1.0, size=n_users)
    user_weights = user_weights / user_weights.sum()

    common_devices = np.array(["ios", "android", "chrome", "safari", "windows", "linux"])
    all_devices = np.array(["ios", "android", "chrome", "safari", "windows", "linux", "emulator", "unknown"])
    device_probs = np.array([0.20, 0.25, 0.18, 0.10, 0.17, 0.05, 0.03, 0.02])

    common_locations = np.array(["tashkent", "samarkand", "bukhara", "fergana", "almaty", "istanbul", "dubai"])
    all_locations = np.array(
        ["tashkent", "samarkand", "bukhara", "fergana", "almaty", "istanbul", "dubai", "foreign_ip", "unknown"]
    )
    location_probs = np.array([0.30, 0.14, 0.10, 0.12, 0.10, 0.09, 0.06, 0.06, 0.03])

    home_devices = rng.choice(common_devices, size=n_users, p=np.array([0.27, 0.31, 0.15, 0.08, 0.15, 0.04]))
    home_locations = rng.choice(common_locations, size=n_users, p=np.array([0.42, 0.14, 0.10, 0.12, 0.08, 0.08, 0.06]))
    base_amounts = rng.lognormal(mean=4.05, sigma=0.65, size=n_users)

    daytime_hours = np.arange(6, 24)
    night_hours = np.arange(0, 6)
    rows: list[dict[str, object]] = []

    for _ in range(n_transactions):
        user_idx = int(rng.choice(n_users, p=user_weights))
        user_id = user_ids[user_idx]
        base_amount = float(base_amounts[user_idx])

        device = home_devices[user_idx] if rng.random() < 0.82 else str(rng.choice(all_devices, p=device_probs))
        location = home_locations[user_idx] if rng.random() < 0.86 else str(rng.choice(all_locations, p=location_probs))

        if rng.random() < 0.82:
            hour = int(rng.choice(daytime_hours))
        else:
            hour = int(rng.choice(night_hours))

        amount = float(rng.lognormal(mean=math.log(base_amount), sigma=0.45))
        if rng.random() < 0.055:
            amount *= float(rng.uniform(2.0, 6.5))
        amount = round(max(amount, 2.0), 2)

        rows.append(
            {
                "user_id": user_id,
                "amount": amount,
                "device": device,
                "location": location,
                "transaction_hour": hour,
                "isFraud": 0,
            }
        )

    df = pd.DataFrame(rows, columns=RAW_COLUMNS)
    behavior = add_behavior_features(df)
    risky_device = behavior["device"].isin({"emulator", "unknown"}).astype(float)
    risky_location = behavior["location"].isin({"foreign_ip", "unknown"}).astype(float)

    risk_points = np.zeros(len(behavior), dtype=float)
    risk_points += (behavior["amount_ratio_to_avg"] >= 1.8).astype(float) * 0.8
    risk_points += (behavior["amount_ratio_to_avg"] >= 3.0).astype(float) * 1.1
    risk_points += (behavior["amount_ratio_to_avg"] >= 5.0).astype(float) * 1.0
    risk_points += behavior["is_new_device"].astype(float) * 1.4
    risk_points += behavior["is_new_location"].astype(float) * 1.6
    risk_points += behavior["is_night_transaction"].astype(float) * 0.8
    risk_points += ((risky_device + risky_location) > 0).astype(float) * 1.3
    risk_points += ((behavior["is_new_device"] == 1) & (behavior["is_new_location"] == 1)).astype(float) * 0.9
    risk_points += (behavior["amount"] >= 500).astype(float) * 0.7

    fraud_probability = 1.0 / (1.0 + np.exp(-(-5.2 + (1.15 * risk_points))))
    fraud_probability = np.where(risk_points >= 5.5, 0.92, fraud_probability)
    fraud_probability = np.where((risk_points >= 4.2) & (risk_points < 5.5), 0.68, fraud_probability)
    df["isFraud"] = (rng.random(len(df)) < fraud_probability).astype(int)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(output_path, index=False)
    return df


def _first_available_series(frame: pd.DataFrame, candidates: Iterable[str], default: str) -> pd.Series:
    result = pd.Series(default, index=frame.index, dtype="object")
    for column in candidates:
        if column in frame.columns:
            result = result.where(result.notna() & (result != default), frame[column])
    return result.fillna(default).astype(str)


def _make_uid(frame: pd.DataFrame) -> pd.Series:
    uid_columns = [column for column in ["card1", "card2", "card3", "card5", "addr1"] if column in frame.columns]
    if not uid_columns:
        if "TransactionID" in frame.columns:
            return "transaction_" + frame["TransactionID"].astype(str)
        return "row_" + pd.Series(np.arange(len(frame)), index=frame.index).astype(str)

    uid_parts = [frame[column].fillna("missing").astype(str) for column in uid_columns]
    uid = uid_parts[0]
    for part in uid_parts[1:]:
        uid = uid.str.cat(part, sep="_")
    return uid


def normalize_kaggle_data(transaction_path: Path, identity_path: Path | None = None) -> pd.DataFrame:
    transactions = pd.read_csv(transaction_path)
    if identity_path and identity_path.exists():
        identity = pd.read_csv(identity_path)
        if "TransactionID" in transactions.columns and "TransactionID" in identity.columns:
            transactions = transactions.merge(identity, on="TransactionID", how="left")

    required = {"TransactionAmt", "isFraud"}
    missing = required.difference(transactions.columns)
    if missing:
        raise ValueError(f"Kaggle transaction file is missing required columns: {sorted(missing)}")

    df = pd.DataFrame(index=transactions.index)
    df["user_id"] = _make_uid(transactions)
    df["amount"] = pd.to_numeric(transactions["TransactionAmt"], errors="coerce")
    df["device"] = _first_available_series(transactions, ["DeviceType", "DeviceInfo", "card4", "ProductCD"], "unknown")
    df["location"] = _first_available_series(transactions, ["addr1", "addr2", "P_emaildomain"], "unknown")

    if "TransactionDT" in transactions.columns:
        transaction_seconds = pd.to_numeric(transactions["TransactionDT"], errors="coerce").fillna(0)
        df["transaction_hour"] = ((transaction_seconds // 3600) % 24).astype(int)
    else:
        df["transaction_hour"] = 12

    df["isFraud"] = pd.to_numeric(transactions["isFraud"], errors="coerce").fillna(0).astype(int)
    return clean_raw_transactions(df)


def clean_raw_transactions(df: pd.DataFrame) -> pd.DataFrame:
    missing = set(RAW_COLUMNS).difference(df.columns)
    if missing:
        raise ValueError(f"Input data is missing required columns: {sorted(missing)}")

    clean = df[RAW_COLUMNS].copy()
    clean["user_id"] = clean["user_id"].fillna("unknown_user").astype(str)
    clean["device"] = clean["device"].fillna("unknown").astype(str).str.lower().str.strip()
    clean["location"] = clean["location"].fillna("unknown").astype(str).str.lower().str.strip()
    clean["amount"] = pd.to_numeric(clean["amount"], errors="coerce")
    clean["transaction_hour"] = pd.to_numeric(clean["transaction_hour"], errors="coerce")
    clean["isFraud"] = pd.to_numeric(clean["isFraud"], errors="coerce")

    clean = clean.dropna(subset=["amount", "transaction_hour", "isFraud"]).reset_index(drop=True)
    clean["amount"] = clean["amount"].clip(lower=0.01).astype(float)
    clean["transaction_hour"] = clean["transaction_hour"].astype(int).clip(lower=0, upper=23)
    clean["isFraud"] = clean["isFraud"].astype(int).clip(lower=0, upper=1)
    return clean


def load_or_create_transactions() -> tuple[pd.DataFrame, str]:
    ensure_directories()

    if KAGGLE_TRANSACTION_PATH.exists():
        return normalize_kaggle_data(KAGGLE_TRANSACTION_PATH, KAGGLE_IDENTITY_PATH), "kaggle"

    if SYNTHETIC_DATA_PATH.exists():
        return clean_raw_transactions(pd.read_csv(SYNTHETIC_DATA_PATH)), "synthetic_existing"

    return generate_synthetic_transactions(SYNTHETIC_DATA_PATH), "synthetic_generated"


def add_behavior_features(df: pd.DataFrame) -> pd.DataFrame:
    features = clean_raw_transactions(df).reset_index(drop=True)
    global_avg_amount = float(features["amount"].mean())

    prior_user_count = features.groupby("user_id").cumcount()
    prior_amount_sum = features.groupby("user_id")["amount"].cumsum() - features["amount"]
    prior_avg_amount = prior_amount_sum / prior_user_count.replace(0, np.nan)

    features["user_avg_amount"] = prior_avg_amount.fillna(global_avg_amount).astype(float)
    features["amount_ratio_to_avg"] = (
        features["amount"] / features["user_avg_amount"].replace(0, global_avg_amount)
    ).replace([np.inf, -np.inf], 1.0)
    features["amount_ratio_to_avg"] = features["amount_ratio_to_avg"].clip(lower=0.0, upper=50.0)
    features["user_transaction_count"] = prior_user_count.astype(int)

    first_user_device_seen = features.groupby(["user_id", "device"]).cumcount() == 0
    first_user_location_seen = features.groupby(["user_id", "location"]).cumcount() == 0
    features["is_new_device"] = (first_user_device_seen & (prior_user_count > 0)).astype(int)
    features["is_new_location"] = (first_user_location_seen & (prior_user_count > 0)).astype(int)
    features["is_night_transaction"] = features["transaction_hour"].between(0, 5).astype(int)
    return features


def build_user_profiles(df: pd.DataFrame) -> dict[str, dict[str, object]]:
    profiles: dict[str, dict[str, object]] = {}
    for user_id, group in clean_raw_transactions(df).groupby("user_id"):
        profiles[str(user_id)] = {
            "avg_amount": float(group["amount"].mean()),
            "transaction_count": int(len(group)),
            "devices": sorted(group["device"].astype(str).unique().tolist()),
            "locations": sorted(group["location"].astype(str).unique().tolist()),
        }
    return profiles


def build_model_pipeline() -> Pipeline:
    preprocessor = ColumnTransformer(
        transformers=[
            ("categorical", OneHotEncoder(handle_unknown="ignore", sparse_output=False), CATEGORICAL_FEATURES),
            ("numeric", "passthrough", NUMERIC_FEATURES),
        ]
    )

    classifier = RandomForestClassifier(
        n_estimators=250,
        max_depth=None,
        min_samples_leaf=2,
        class_weight="balanced_subsample",
        random_state=RANDOM_STATE,
        n_jobs=-1,
    )

    return Pipeline(
        steps=[
            ("preprocessor", preprocessor),
            ("classifier", classifier),
        ]
    )


def find_best_threshold(y_true: pd.Series, probability: np.ndarray, min_precision: float = 0.20) -> float:
    candidates = np.unique(np.r_[np.linspace(0.02, 0.98, 97), np.quantile(probability, np.linspace(0.02, 0.98, 97))])
    best_threshold = 0.50
    best_f1 = -1.0
    fallback_threshold = 0.50
    fallback_f1 = -1.0

    for threshold in candidates:
        predictions = (probability >= threshold).astype(int)
        precision = precision_score(y_true, predictions, zero_division=0)
        recall = recall_score(y_true, predictions, zero_division=0)
        f1 = f1_score(y_true, predictions, zero_division=0)
        if f1 > fallback_f1 or (np.isclose(f1, fallback_f1) and recall > recall_score(y_true, (probability >= fallback_threshold).astype(int), zero_division=0)):
            fallback_f1 = f1
            fallback_threshold = float(threshold)
        if precision >= min_precision and f1 > best_f1:
            best_f1 = f1
            best_threshold = float(threshold)

    return float(np.clip(best_threshold if best_f1 >= 0 else fallback_threshold, 0.02, 0.98))


def evaluate_model(model: Pipeline, x_test: pd.DataFrame, y_test: pd.Series, threshold: float) -> dict[str, float]:
    fraud_probability = model.predict_proba(x_test)[:, 1]
    predictions = (fraud_probability >= threshold).astype(int)

    metrics = {
        "accuracy": accuracy_score(y_test, predictions),
        "precision": precision_score(y_test, predictions, zero_division=0),
        "recall": recall_score(y_test, predictions, zero_division=0),
        "f1_score": f1_score(y_test, predictions, zero_division=0),
        "roc_auc": roc_auc_score(y_test, fraud_probability),
        "average_precision": average_precision_score(y_test, fraud_probability),
        "review_threshold": threshold,
    }

    print("\nEvaluation metrics")
    print("------------------")
    for metric_name, metric_value in metrics.items():
        print(f"{metric_name}: {metric_value:.4f}")

    print("\nClassification report")
    print("---------------------")
    print(classification_report(y_test, predictions, zero_division=0))
    return metrics


def save_model_artifact(model: Pipeline, source_df: pd.DataFrame, metrics: dict[str, float], review_threshold: float) -> None:
    clean_df = clean_raw_transactions(source_df)
    artifact = {
        "model": model,
        "feature_columns": FEATURE_COLUMNS,
        "categorical_features": CATEGORICAL_FEATURES,
        "numeric_features": NUMERIC_FEATURES,
        "user_profiles": build_user_profiles(clean_df),
        "global_profile": {
            "avg_amount": float(clean_df["amount"].mean()),
            "median_amount": float(clean_df["amount"].median()),
            "transaction_count": int(len(clean_df)),
            "fraud_rate": float(clean_df["isFraud"].mean()),
        },
        "thresholds": {
            "review": float(review_threshold),
            "block": float(min(0.98, max(0.80, review_threshold * 1.6))),
        },
        "metrics": metrics,
        "random_state": RANDOM_STATE,
    }
    joblib.dump(artifact, MODEL_PATH)


def train() -> Pipeline:
    df, source = load_or_create_transactions()
    features = add_behavior_features(df)

    x = features[FEATURE_COLUMNS]
    y = features["isFraud"]

    if y.nunique() < 2:
        raise ValueError("Training data must contain both fraud and non-fraud examples.")

    x_train_full, x_test, y_train_full, y_test = train_test_split(
        x,
        y,
        test_size=0.25,
        random_state=RANDOM_STATE,
        stratify=y,
    )
    x_train, x_val, y_train, y_val = train_test_split(
        x_train_full,
        y_train_full,
        test_size=0.20,
        random_state=RANDOM_STATE + 1,
        stratify=y_train_full,
    )

    model = build_model_pipeline()
    model.fit(x_train, y_train)

    validation_probability = model.predict_proba(x_val)[:, 1]
    review_threshold = find_best_threshold(y_val, validation_probability, min_precision=0.20)
    metrics = evaluate_model(model, x_test, y_test, review_threshold)
    metrics["validation_roc_auc"] = roc_auc_score(y_val, validation_probability)
    metrics["validation_average_precision"] = average_precision_score(y_val, validation_probability)
    metrics["validation_f1_score"] = f1_score(
        y_val,
        (validation_probability >= review_threshold).astype(int),
        zero_division=0,
    )
    save_model_artifact(model, df, metrics, review_threshold)

    print("\nTraining summary")
    print("----------------")
    print(f"data_source: {source}")
    print(f"rows: {len(df)}")
    print(f"fraud_rate: {df['isFraud'].mean():.4f}")
    print(f"saved_model: {MODEL_PATH}")
    return model


def main() -> None:
    train()


if __name__ == "__main__":
    main()
