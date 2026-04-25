from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import joblib
import pandas as pd


ROOT_DIR = Path(__file__).resolve().parents[1]
MODEL_PATH = ROOT_DIR / "ml" / "fraud_model.pkl"

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

DEFAULT_SAMPLE = {
    "user_id": "user_075",
    "amount": 10000.0,
    "device": "emulator",
    "location": "foreign_ip",
    "transaction_hour": 2,
}


def load_model_artifact(model_path: Path = MODEL_PATH) -> dict[str, Any]:
    if not model_path.exists():
        raise FileNotFoundError(f"Model not found at {model_path}. Run `python ml/train_model.py` first.")
    return joblib.load(model_path)


def parse_transaction(raw_input: str | None) -> dict[str, Any]:
    if not raw_input:
        return DEFAULT_SAMPLE.copy()

    raw_input = raw_input.strip()
    candidates = [raw_input]
    if '\\"' in raw_input:
        candidates.append(raw_input.replace('\\"', '"'))
    if (raw_input.startswith("'") and raw_input.endswith("'")) or (
        raw_input.startswith('"') and raw_input.endswith('"')
    ):
        candidates.append(raw_input[1:-1])

    last_error: json.JSONDecodeError | None = None
    for candidate in candidates:
        try:
            parsed = json.loads(candidate)
            break
        except json.JSONDecodeError as exc:
            last_error = exc
    else:
        parsed = parse_relaxed_object(raw_input)
        if parsed is None:
            raise ValueError(f"Input must be a valid JSON object: {last_error}")

    if not isinstance(parsed, dict):
        raise ValueError("Input JSON must be an object with transaction fields.")
    return parsed


def parse_relaxed_object(raw_input: str) -> dict[str, Any] | None:
    text = raw_input.strip()
    if not (text.startswith("{") and text.endswith("}")):
        return None

    result: dict[str, Any] = {}
    inner = text[1:-1].strip()
    if not inner:
        return result

    for part in inner.split(","):
        if ":" not in part:
            return None
        key, value = part.split(":", 1)
        key = key.strip().strip("'\"`\\ ")
        value = value.strip().strip("'\"`\\ ")
        if not key:
            return None

        try:
            if "." in value:
                parsed_value: Any = float(value)
            else:
                parsed_value = int(value)
        except ValueError:
            parsed_value = value
        result[key] = parsed_value

    return result


def validate_transaction(transaction: dict[str, Any]) -> dict[str, Any]:
    required = {"user_id", "amount", "device", "location", "transaction_hour"}
    missing = required.difference(transaction)
    if missing:
        raise ValueError(f"Transaction is missing required fields: {sorted(missing)}")

    amount = float(transaction["amount"])
    transaction_hour = int(transaction["transaction_hour"])
    if amount <= 0:
        raise ValueError("amount must be greater than 0.")
    if not 0 <= transaction_hour <= 23:
        raise ValueError("transaction_hour must be between 0 and 23.")

    return {
        "user_id": str(transaction["user_id"]),
        "amount": amount,
        "device": str(transaction["device"]).lower().strip(),
        "location": str(transaction["location"]).lower().strip(),
        "transaction_hour": transaction_hour,
    }


def build_feature_row(transaction: dict[str, Any], artifact: dict[str, Any]) -> tuple[pd.DataFrame, dict[str, Any]]:
    user_profiles = artifact.get("user_profiles", {})
    global_profile = artifact.get("global_profile", {})

    user_id = transaction["user_id"]
    amount = float(transaction["amount"])
    device = transaction["device"]
    location = transaction["location"]
    transaction_hour = int(transaction["transaction_hour"])

    profile = user_profiles.get(user_id)
    known_user = profile is not None

    if known_user:
        avg_amount = float(profile.get("avg_amount", global_profile.get("avg_amount", amount)))
        transaction_count = int(profile.get("transaction_count", 0))
        known_devices = set(profile.get("devices", []))
        known_locations = set(profile.get("locations", []))
    else:
        avg_amount = float(global_profile.get("avg_amount", amount))
        transaction_count = 0
        known_devices = set()
        known_locations = set()

    if avg_amount <= 0:
        avg_amount = amount

    amount_ratio = amount / avg_amount
    is_new_device = int(known_user and transaction_count > 0 and device not in known_devices)
    is_new_location = int(known_user and transaction_count > 0 and location not in known_locations)
    is_night_transaction = int(0 <= transaction_hour <= 5)

    feature_values = {
        "device": device,
        "location": location,
        "amount": amount,
        "transaction_hour": transaction_hour,
        "user_avg_amount": avg_amount,
        "amount_ratio_to_avg": min(max(amount_ratio, 0.0), 50.0),
        "user_transaction_count": transaction_count,
        "is_new_device": is_new_device,
        "is_new_location": is_new_location,
        "is_night_transaction": is_night_transaction,
    }

    context = {
        "known_user": known_user,
        "amount_ratio_to_avg": amount_ratio,
        "is_new_device": bool(is_new_device),
        "is_new_location": bool(is_new_location),
        "is_night_transaction": bool(is_night_transaction),
        "user_transaction_count": transaction_count,
    }
    return pd.DataFrame([feature_values], columns=FEATURE_COLUMNS), context


def classify_probability(probability: float, artifact: dict[str, Any]) -> tuple[str, str]:
    thresholds = artifact.get("thresholds", {"review": 0.50, "block": 0.80})
    review_threshold = float(thresholds.get("review", 0.50))
    block_threshold = float(thresholds.get("block", 0.80))

    if probability >= block_threshold:
        return "HIGH", "BLOCK"
    if probability >= review_threshold:
        return "MEDIUM", "REVIEW"
    return "LOW", "ALLOW"


def build_reasons(context: dict[str, Any], risk_level: str) -> list[str]:
    reasons: list[str] = []

    if not context["known_user"]:
        reasons.append("User has no historical profile")

    amount_ratio = float(context["amount_ratio_to_avg"])
    if amount_ratio >= 3.0:
        reasons.append("Amount is much higher than this user's average")
    elif amount_ratio >= 1.8:
        reasons.append("Amount is higher than this user's average")

    if context["is_new_device"]:
        reasons.append("New device for this user")
    if context["is_new_location"]:
        reasons.append("New location for this user")
    if context["is_night_transaction"]:
        reasons.append("Night transaction")
    if int(context["user_transaction_count"]) < 3:
        reasons.append("Limited user transaction history")

    if not reasons:
        reasons.append("Matches known user behavior" if risk_level == "LOW" else "Model detected a risky feature pattern")
    return reasons


def predict(transaction: dict[str, Any], artifact: dict[str, Any]) -> dict[str, Any]:
    model = artifact["model"]
    feature_row, context = build_feature_row(transaction, artifact)
    fraud_probability = float(model.predict_proba(feature_row)[0, 1])
    risk_level, decision = classify_probability(fraud_probability, artifact)

    return {
        "fraud_probability": round(fraud_probability, 4),
        "risk_level": risk_level,
        "decision": decision,
        "reasons": build_reasons(context, risk_level),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Predict fraud probability for one transaction.")
    parser.add_argument("--input", help="JSON transaction input. Uses a high-risk sample when omitted.")
    args = parser.parse_args()

    artifact = load_model_artifact()
    transaction = validate_transaction(parse_transaction(args.input))
    result = predict(transaction, artifact)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
