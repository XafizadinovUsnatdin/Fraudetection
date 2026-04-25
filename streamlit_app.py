"""
SafeNet - Fraud Detection

Streamlit Cloud friendly version:
- no scikit-learn / scipy dependency
- fast in-app training with pandas + numpy
- live transaction stream simulation
"""

from __future__ import annotations

import pickle
import time
import warnings
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

warnings.filterwarnings("ignore")

ROOT = Path(__file__).parent
DATA_PATH = ROOT / "data" / "synthetic_transactions.csv"
MODEL_PATH = ROOT / "ml" / "fraud_model_v2.pkl"

CATEGORICAL = ["device", "location"]
NUMERICAL = [
    "amount",
    "transaction_hour",
    "Transaction Frequency",
    "Time Since Last Transaction",
    "Account Age",
    "Normalized Transaction Amount",
    "Fraud Complaints Count",
]
BINARY = [
    "Geo-Location Flags",
    "Location-Inconsistent Transactions",
    "Recipient Verification Status",
    "Recipient Blacklist Status",
    "VPN or Proxy Usage",
    "Merchant Category Mismatch",
    "User Daily Limit Exceeded",
    "Recent High-Value Transaction Flags",
    "Past Fraudulent Behavior Flags",
]
FEATURES = NUMERICAL + BINARY + CATEGORICAL
TARGET = "isFraud"

st.set_page_config(
    page_title="SafeNet Fraud Detection",
    page_icon="Shield",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.markdown(
    """
<style>
[data-testid="stMetricValue"] { font-size: 1.55rem; font-weight: 700; }
.block-container { padding-top: 1.5rem; }
</style>
""",
    unsafe_allow_html=True,
)


@st.cache_data(show_spinner="Ma'lumotlar yuklanmoqda...")
def load_data() -> pd.DataFrame:
    return pd.read_csv(DATA_PATH)


def sigmoid(x: np.ndarray) -> np.ndarray:
    return 1.0 / (1.0 + np.exp(-np.clip(x, -35, 35)))


def stratified_split(
    df: pd.DataFrame,
    target: str,
    test_size: float = 0.25,
    seed: int = 42,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    rng = np.random.default_rng(seed)
    train_parts: list[pd.DataFrame] = []
    test_parts: list[pd.DataFrame] = []

    for _, part in df.groupby(target):
        idx = part.index.to_numpy().copy()
        rng.shuffle(idx)
        n_test = max(1, int(len(idx) * test_size))
        test_parts.append(part.loc[idx[:n_test]])
        train_parts.append(part.loc[idx[n_test:]])

    train = pd.concat(train_parts).sample(frac=1, random_state=seed).reset_index(drop=True)
    test = pd.concat(test_parts).sample(frac=1, random_state=seed + 1).reset_index(drop=True)
    return train, test


def stratified_sample(df: pd.DataFrame, n: int, seed: int = 42) -> pd.DataFrame:
    if len(df) <= n:
        return df.sample(frac=1, random_state=seed).reset_index(drop=True)

    parts: list[pd.DataFrame] = []
    for label, part in df.groupby(TARGET):
        frac = len(part) / len(df)
        take = max(1, int(n * frac))
        parts.append(part.sample(n=take, random_state=seed + int(label)))
    return pd.concat(parts).sample(frac=1, random_state=seed).reset_index(drop=True)


def confusion_matrix_np(y_true: np.ndarray, y_pred: np.ndarray) -> np.ndarray:
    tn = int(((y_true == 0) & (y_pred == 0)).sum())
    fp = int(((y_true == 0) & (y_pred == 1)).sum())
    fn = int(((y_true == 1) & (y_pred == 0)).sum())
    tp = int(((y_true == 1) & (y_pred == 1)).sum())
    return np.array([[tn, fp], [fn, tp]])


def precision_recall_f1(y_true: np.ndarray, y_pred: np.ndarray) -> tuple[float, float, float]:
    cm = confusion_matrix_np(y_true, y_pred)
    tp = cm[1, 1]
    fp = cm[0, 1]
    fn = cm[1, 0]
    precision = tp / max(tp + fp, 1)
    recall = tp / max(tp + fn, 1)
    f1 = 2 * precision * recall / max(precision + recall, 1e-12)
    return float(precision), float(recall), float(f1)


def roc_auc_np(y_true: np.ndarray, score: np.ndarray) -> float:
    y_true = np.asarray(y_true).astype(int)
    score = np.asarray(score).astype(float)
    pos = int((y_true == 1).sum())
    neg = int((y_true == 0).sum())
    if pos == 0 or neg == 0:
        return 0.5

    ranks = pd.Series(score).rank(method="average").to_numpy()
    pos_rank_sum = ranks[y_true == 1].sum()
    auc = (pos_rank_sum - pos * (pos + 1) / 2) / (pos * neg)
    return float(auc)


def average_precision_np(y_true: np.ndarray, score: np.ndarray) -> float:
    y_true = np.asarray(y_true).astype(int)
    score = np.asarray(score).astype(float)
    order = np.argsort(-score)
    y = y_true[order]
    total_pos = max(int(y.sum()), 1)
    tp = np.cumsum(y == 1)
    fp = np.cumsum(y == 0)
    precision = tp / np.maximum(tp + fp, 1)
    recall = tp / total_pos
    recall_prev = np.r_[0.0, recall[:-1]]
    return float(np.sum((recall - recall_prev) * precision))


def roc_curve_np(y_true: np.ndarray, score: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    thresholds = np.quantile(score, np.linspace(1, 0, 80))
    fpr: list[float] = []
    tpr: list[float] = []
    pos = max(int((y_true == 1).sum()), 1)
    neg = max(int((y_true == 0).sum()), 1)
    for t in thresholds:
        pred = (score >= t).astype(int)
        cm = confusion_matrix_np(y_true, pred)
        fpr.append(cm[0, 1] / neg)
        tpr.append(cm[1, 1] / pos)
    return np.array([0.0, *fpr, 1.0]), np.array([0.0, *tpr, 1.0])


def pr_curve_np(y_true: np.ndarray, score: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    thresholds = np.quantile(score, np.linspace(1, 0, 80))
    precision: list[float] = []
    recall: list[float] = []
    for t in thresholds:
        pred = (score >= t).astype(int)
        p, r, _ = precision_recall_f1(y_true, pred)
        precision.append(p)
        recall.append(r)
    return np.array(precision), np.array(recall)


def classification_report_np(y_true: np.ndarray, y_pred: np.ndarray) -> dict:
    rows: dict[str, dict[str, float]] = {}
    supports = []
    for label in [0, 1]:
        yt = (y_true == label).astype(int)
        yp = (y_pred == label).astype(int)
        p, r, f1 = precision_recall_f1(yt, yp)
        support = int((y_true == label).sum())
        supports.append(support)
        rows[str(label)] = {"precision": p, "recall": r, "f1-score": f1, "support": support}

    acc = float((y_true == y_pred).mean())
    total = max(sum(supports), 1)
    rows["accuracy"] = acc
    rows["macro avg"] = {
        "precision": float(np.mean([rows["0"]["precision"], rows["1"]["precision"]])),
        "recall": float(np.mean([rows["0"]["recall"], rows["1"]["recall"]])),
        "f1-score": float(np.mean([rows["0"]["f1-score"], rows["1"]["f1-score"]])),
        "support": total,
    }
    rows["weighted avg"] = {
        "precision": float((rows["0"]["precision"] * supports[0] + rows["1"]["precision"] * supports[1]) / total),
        "recall": float((rows["0"]["recall"] * supports[0] + rows["1"]["recall"] * supports[1]) / total),
        "f1-score": float((rows["0"]["f1-score"] * supports[0] + rows["1"]["f1-score"] * supports[1]) / total),
        "support": total,
    }
    return rows


def optimal_threshold(y_true: np.ndarray, score: np.ndarray) -> float:
    best_f1 = -1.0
    best_t = 0.5
    for t in np.linspace(0.05, 0.95, 91):
        pred = (score >= t).astype(int)
        _, _, f1 = precision_recall_f1(y_true, pred)
        if f1 > best_f1:
            best_f1 = f1
            best_t = float(t)
    return best_t


class NumpyFraudModel:
    """Small logistic model that works on Streamlit Cloud without sklearn."""

    def __init__(self, epochs: int = 180, lr: float = 0.08, l2: float = 0.002) -> None:
        self.epochs = epochs
        self.lr = lr
        self.l2 = l2
        self.numeric_mean: pd.Series | None = None
        self.numeric_std: pd.Series | None = None
        self.categories: dict[str, list[str]] = {}
        self.bin_edges: dict[str, np.ndarray] = {}
        self.feature_names: list[str] = []
        self.weights: np.ndarray | None = None
        self.bias: float = 0.0

    def _fit_schema(self, df: pd.DataFrame) -> None:
        numeric_cols = NUMERICAL + BINARY
        self.numeric_mean = df[numeric_cols].astype(float).mean()
        self.numeric_std = df[numeric_cols].astype(float).std().replace(0, 1).fillna(1)
        self.categories = {col: sorted(df[col].astype(str).fillna("unknown").unique()) for col in CATEGORICAL}
        self.bin_edges = {}
        for col in NUMERICAL:
            quantiles = df[col].astype(float).quantile(np.linspace(0.1, 0.9, 9)).to_numpy()
            self.bin_edges[col] = np.unique(quantiles)
        self.feature_names = list(numeric_cols)
        for col, edges in self.bin_edges.items():
            self.feature_names.extend([f"{col}_bin_{i}" for i in range(len(edges) + 1)])
        for col in CATEGORICAL:
            self.feature_names.extend([f"{col}_{value}" for value in self.categories[col]])

    def transform(self, df: pd.DataFrame) -> np.ndarray:
        if self.numeric_mean is None or self.numeric_std is None:
            raise RuntimeError("Model schema is not fitted")

        numeric_cols = NUMERICAL + BINARY
        num = df[numeric_cols].astype(float).copy()
        num = (num - self.numeric_mean) / self.numeric_std
        arrays = [num.to_numpy(dtype=float)]

        for col, edges in self.bin_edges.items():
            values = df[col].astype(float).to_numpy()
            bins = np.searchsorted(edges, values, side="right")
            one_hot = np.zeros((len(df), len(edges) + 1), dtype=float)
            one_hot[np.arange(len(df)), bins] = 1.0
            arrays.append(one_hot)

        for col in CATEGORICAL:
            values = df[col].astype(str).fillna("unknown")
            cats = self.categories[col]
            one_hot = np.zeros((len(df), len(cats)), dtype=float)
            index = {value: i for i, value in enumerate(cats)}
            for row_idx, value in enumerate(values):
                col_idx = index.get(value)
                if col_idx is not None:
                    one_hot[row_idx, col_idx] = 1.0
            arrays.append(one_hot)

        return np.hstack(arrays)

    def fit(self, df: pd.DataFrame, y: pd.Series) -> "NumpyFraudModel":
        self._fit_schema(df)
        x = self.transform(df)
        y_arr = y.to_numpy(dtype=float)
        n_rows, n_cols = x.shape
        self.weights = np.zeros(n_cols, dtype=float)
        self.bias = float(np.log((y_arr.mean() + 1e-6) / (1 - y_arr.mean() + 1e-6)))

        pos_weight = n_rows / max(2 * y_arr.sum(), 1)
        neg_weight = n_rows / max(2 * (n_rows - y_arr.sum()), 1)
        sample_weight = np.where(y_arr == 1, pos_weight, neg_weight)

        for _ in range(self.epochs):
            z = x @ self.weights + self.bias
            prob = sigmoid(z)
            error = (prob - y_arr) * sample_weight
            grad_w = (x.T @ error) / n_rows + self.l2 * self.weights
            grad_b = float(error.mean())
            self.weights -= self.lr * grad_w
            self.bias -= self.lr * grad_b

        return self

    def predict_proba(self, df: pd.DataFrame) -> np.ndarray:
        if self.weights is None:
            raise RuntimeError("Model is not fitted")
        x = self.transform(df)
        prob = sigmoid(x @ self.weights + self.bias)
        return np.column_stack([1 - prob, prob])

    @property
    def feature_importances_(self) -> np.ndarray:
        if self.weights is None:
            return np.zeros(len(self.feature_names))
        imp = np.abs(self.weights)
        return imp / max(float(imp.sum()), 1e-12)


def build_artifact(
    model: NumpyFraudModel,
    train_df: pd.DataFrame,
    test_df: pd.DataFrame,
    source_df: pd.DataFrame,
) -> dict:
    y_test = test_df[TARGET].to_numpy(dtype=int)
    score = model.predict_proba(test_df[FEATURES])[:, 1]
    threshold = optimal_threshold(y_test, score)
    pred = (score >= threshold).astype(int)
    precision, recall, f1 = precision_recall_f1(y_test, pred)
    fpr, tpr = roc_curve_np(y_test, score)
    pr_prec, pr_rec = pr_curve_np(y_test, score)

    fi = pd.DataFrame(
        {
            "xususiyat": model.feature_names,
            "muhimlik": model.feature_importances_,
        }
    ).sort_values("muhimlik", ascending=False)

    report = classification_report_np(y_test, pred)
    return {
        "pipeline": model,
        "xususiyatlar": FEATURES,
        "chegara": threshold,
        "miqdor_stat": {"min": float(source_df["amount"].min()), "max": float(source_df["amount"].max())},
        "xususiyat_muhimlik": fi,
        "orgatish_sanasi": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "orgatish_soni": len(train_df),
        "test_soni": len(test_df),
        "fraud_ulushi": float(source_df[TARGET].mean()),
        "korsatkichlar": {
            "kv_roc_auc": roc_auc_np(y_test, score),
            "kv_roc_auc_std": 0.0,
            "kv_f1": f1,
            "kv_aniqlik": precision,
            "kv_qamrov": recall,
            "test_roc_auc": roc_auc_np(y_test, score),
            "test_ort_aniqlik": average_precision_np(y_test, score),
            "test_f1": f1,
            "chegara": threshold,
            "chalkash_matritsa": confusion_matrix_np(y_test, pred),
            "roc_egri": (fpr, tpr),
            "pr_egri": (pr_prec, pr_rec),
            "hisobot": report,
        },
    }


def save_artifact(artifact: dict) -> None:
    MODEL_PATH.parent.mkdir(parents=True, exist_ok=True)
    with MODEL_PATH.open("wb") as f:
        pickle.dump(artifact, f)


def train_numpy_model(
    df: pd.DataFrame,
    sample_size: int = 12_000,
    epochs: int = 180,
    progress=None,
) -> dict:
    work_df = stratified_sample(df, min(sample_size, len(df)), seed=42)
    if progress:
        progress.progress(20, "Sample tayyorlandi...")

    train_df, test_df = stratified_split(work_df, TARGET, test_size=0.25, seed=42)
    model = NumpyFraudModel(epochs=epochs)

    if progress:
        progress.progress(45, "Model o'rgatilmoqda...")
    model.fit(train_df[FEATURES], train_df[TARGET])

    if progress:
        progress.progress(80, "Baholash hisoblanmoqda...")
    artifact = build_artifact(model, train_df, test_df, df)
    save_artifact(artifact)

    if progress:
        progress.progress(100, "Model tayyor!")
    return artifact


def orgatish_va_saqlash(
    df: pd.DataFrame,
    daraxt_soni: int = 200,
    max_chuqurlik: int = 15,
    min_barglar: int = 2,
    progress=None,
) -> dict:
    # Argument nomlari eski UI bilan moslik uchun qoldirilgan.
    sample_size = min(len(df), max(12_000, daraxt_soni * 120))
    epochs = int(np.clip(daraxt_soni, 120, 450))
    return train_numpy_model(df, sample_size=sample_size, epochs=epochs, progress=progress)


def tezkor_orgatish_va_saqlash(df: pd.DataFrame, progress=None) -> dict:
    return train_numpy_model(df, sample_size=10_000, epochs=140, progress=progress)


def artifactni_moslashtir(artifact: dict | None) -> dict | None:
    if not isinstance(artifact, dict):
        return None
    return artifact if "korsatkichlar" in artifact and "pipeline" in artifact else None


@st.cache_resource(show_spinner=False)
def modelni_yukla() -> dict | None:
    if MODEL_PATH.exists():
        try:
            with MODEL_PATH.open("rb") as f:
                return artifactni_moslashtir(pickle.load(f))
        except Exception:
            return None
    return None


def ensure_model() -> dict:
    artifact = modelni_yukla()
    if artifact is not None:
        return artifact
    df = load_data()
    return tezkor_orgatish_va_saqlash(df)


def tranzaksiyani_tekshir(artifact: dict, tx: dict) -> dict:
    amount = float(tx.get("amount", 100))
    a_min = artifact["miqdor_stat"]["min"]
    a_max = artifact["miqdor_stat"]["max"]
    tx["Normalized Transaction Amount"] = float(np.clip((amount - a_min) / max(a_max - a_min, 1e-9), 0.0, 1.0))

    row = {feature: 0 for feature in FEATURES}
    row.update(tx)
    prob = float(artifact["pipeline"].predict_proba(pd.DataFrame([row])[FEATURES])[0, 1])

    threshold = artifact["chegara"]
    block_threshold = max(threshold * 1.6, 0.70)
    if prob >= block_threshold:
        risk, decision = "HIGH", "BLOCK"
    elif prob >= threshold:
        risk, decision = "MEDIUM", "REVIEW"
    else:
        risk, decision = "LOW", "ALLOW"
    return {"ehtimol": prob, "xavf": risk, "qaror": decision}


def simulyatsiya_namunasini_tanla(df: pd.DataFrame, soni: int, ssenariy: str, seed: int) -> pd.DataFrame:
    rng = np.random.default_rng(seed)

    def take(label: int, n: int) -> pd.DataFrame:
        part = df[df[TARGET] == label]
        return part.sample(n=n, replace=len(part) < n, random_state=int(rng.integers(1, 1_000_000)))

    if ssenariy == "Balanslangan test (50% fraud)":
        fraud_n = soni // 2
    elif ssenariy == "Hujum ssenariysi (35% fraud)":
        fraud_n = max(1, int(soni * 0.35))
    else:
        return df.sample(n=soni, replace=len(df) < soni, random_state=seed).reset_index(drop=True)

    sample = pd.concat([take(1, fraud_n), take(0, soni - fraud_n)])
    return sample.sample(frac=1, random_state=seed).reset_index(drop=True)


def oqim_metrikalari(y_true: list[int], y_pred: list[int], y_prob: list[float]) -> dict:
    if not y_true:
        return {"accuracy": 0.0, "precision": 0.0, "recall": 0.0, "f1": 0.0, "roc_auc": None}
    yt = np.array(y_true)
    yp = np.array(y_pred)
    prob = np.array(y_prob)
    p, r, f1 = precision_recall_f1(yt, yp)
    return {
        "accuracy": float((yt == yp).mean()),
        "precision": p,
        "recall": r,
        "f1": f1,
        "roc_auc": roc_auc_np(yt, prob) if len(np.unique(yt)) == 2 else None,
    }


def render_overview(df: pd.DataFrame) -> None:
    st.header("Ma'lumotlar ko'rinishi")
    total = len(df)
    fraud = int(df[TARGET].sum())
    normal = total - fraud

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Jami tranzaksiyalar", f"{total:,}")
    c2.metric("Fraud holatlar", f"{fraud:,}", f"{fraud / total * 100:.1f}%")
    c3.metric("Normal", f"{normal:,}")
    c4.metric("Ustunlar", str(len(df.columns)))

    col1, col2 = st.columns(2)
    with col1:
        fig = px.pie(
            values=[fraud, normal],
            names=["Fraud", "Normal"],
            hole=0.42,
            color_discrete_sequence=["#ef4444", "#22c55e"],
            title="Fraud / Normal taqsimoti",
        )
        st.plotly_chart(fig, width="stretch")

    with col2:
        dev = (
            df.groupby("device")[TARGET]
            .agg(fraud="sum", jami="count")
            .assign(rate=lambda x: x["fraud"] / x["jami"] * 100)
            .reset_index()
            .sort_values("rate", ascending=False)
        )
        fig = px.bar(dev, x="device", y="rate", color="rate", color_continuous_scale="RdYlGn_r")
        fig.update_layout(title="Qurilma bo'yicha fraud darajasi", yaxis_title="Fraud %")
        fig.update_coloraxes(showscale=False)
        st.plotly_chart(fig, width="stretch")

    col3, col4 = st.columns(2)
    with col3:
        hour = df.groupby("transaction_hour")[TARGET].mean() * 100
        fig = go.Figure(go.Scatter(x=hour.index, y=hour.values, mode="lines+markers", line=dict(color="#3b82f6")))
        fig.update_layout(title="Soat bo'yicha fraud darajasi", xaxis_title="Soat", yaxis_title="Fraud %")
        st.plotly_chart(fig, width="stretch")

    with col4:
        sample = df.sample(min(6000, len(df)), random_state=42)
        fig = px.histogram(
            sample,
            x="amount",
            color=sample[TARGET].map({0: "Normal", 1: "Fraud"}),
            nbins=60,
            barmode="overlay",
            opacity=0.72,
            title="Tranzaksiya miqdori taqsimoti",
        )
        st.plotly_chart(fig, width="stretch")


def render_explorer(df: pd.DataFrame) -> None:
    st.header("Ma'lumotlar ko'zgusi")
    col1, col2, col3 = st.columns(3)
    with col1:
        devices = st.multiselect("Qurilma", sorted(df["device"].unique()), default=list(df["device"].unique()))
    with col2:
        locations = st.multiselect("Joylashuv", sorted(df["location"].unique()), default=list(df["location"].unique()))
    with col3:
        label = st.radio("Yorliq", ["Barchasi", "Normal (0)", "Fraud (1)"], horizontal=True)

    mask = df["device"].isin(devices) & df["location"].isin(locations)
    if label == "Normal (0)":
        mask &= df[TARGET] == 0
    elif label == "Fraud (1)":
        mask &= df[TARGET] == 1
    fdf = df[mask]
    st.info(f"Filtrlangan: {len(fdf):,} / {len(df):,}")
    if fdf.empty:
        return

    col1, col2 = st.columns(2)
    with col1:
        sample = fdf.sample(min(3000, len(fdf)), random_state=42)
        fig = px.scatter(
            sample,
            x="amount",
            y="transaction_hour",
            color=sample[TARGET].map({0: "Normal", 1: "Fraud"}),
            opacity=0.55,
            title="Miqdor va vaqt",
        )
        st.plotly_chart(fig, width="stretch")
    with col2:
        corr_cols = ["amount", "Transaction Frequency", "Time Since Last Transaction", "Account Age", "Fraud Complaints Count", TARGET]
        fig = px.imshow(fdf[corr_cols].corr().round(3), text_auto=".2f", color_continuous_scale="RdBu_r", zmin=-1, zmax=1)
        fig.update_layout(title="Korrelyatsiya")
        st.plotly_chart(fig, width="stretch")

    st.dataframe(fdf.head(200), width="stretch", hide_index=True)


def render_training(df: pd.DataFrame) -> None:
    st.header("Model o'rgatish")
    st.info("Deploy versiya sklearnsiz ishlaydi. Model pandas + numpy asosida logistic risk model sifatida o'rgatiladi.")

    col1, col2 = st.columns(2)
    with col1:
        sample_size = st.slider("Training sample", 5_000, min(40_000, len(df)), 18_000, 1_000)
    with col2:
        epochs = st.slider("Epochs", 80, 450, 220, 20)

    art = modelni_yukla()
    if art:
        m = art["korsatkichlar"]
        st.success(f"Faol model: ROC-AUC {m['test_roc_auc']:.4f}, F1 {m['test_f1']:.4f}, chegara {m['chegara']:.2f}")

    if st.button("Modelni qayta o'rgat", type="primary"):
        bar = st.progress(0, "Boshlanmoqda...")
        art = train_numpy_model(df, sample_size=sample_size, epochs=epochs, progress=bar)
        st.cache_resource.clear()
        m = art["korsatkichlar"]
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("ROC-AUC", f"{m['test_roc_auc']:.4f}")
        c2.metric("Avg Precision", f"{m['test_ort_aniqlik']:.4f}")
        c3.metric("F1", f"{m['test_f1']:.4f}")
        c4.metric("Threshold", f"{m['chegara']:.2f}")


def render_performance(art: dict) -> None:
    st.header("Model baholash")
    m = art["korsatkichlar"]
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("ROC-AUC", f"{m['test_roc_auc']:.4f}")
    c2.metric("Avg Precision", f"{m['test_ort_aniqlik']:.4f}")
    c3.metric("F1", f"{m['test_f1']:.4f}")
    c4.metric("Chegara", f"{m['chegara']:.2f}")

    col1, col2 = st.columns(2)
    with col1:
        fig = px.imshow(
            m["chalkash_matritsa"],
            text_auto=True,
            color_continuous_scale="Blues",
            x=["Bashorat: Normal", "Bashorat: Fraud"],
            y=["Haqiqiy: Normal", "Haqiqiy: Fraud"],
            title="Confusion matrix",
        )
        fig.update_coloraxes(showscale=False)
        st.plotly_chart(fig, width="stretch")

    with col2:
        fpr, tpr = m["roc_egri"]
        fig = go.Figure()
        fig.add_trace(go.Scatter(x=fpr, y=tpr, mode="lines", name=f"AUC={m['test_roc_auc']:.4f}"))
        fig.add_trace(go.Scatter(x=[0, 1], y=[0, 1], mode="lines", line=dict(dash="dash"), name="Tasodifiy"))
        fig.update_layout(title="ROC curve", xaxis_title="FPR", yaxis_title="TPR")
        st.plotly_chart(fig, width="stretch")

    col3, col4 = st.columns(2)
    with col3:
        prec, rec = m["pr_egri"]
        fig = go.Figure(go.Scatter(x=rec, y=prec, mode="lines", name="PR"))
        fig.update_layout(title="Precision-Recall", xaxis_title="Recall", yaxis_title="Precision")
        st.plotly_chart(fig, width="stretch")
    with col4:
        fi = art["xususiyat_muhimlik"].head(15)
        fig = px.bar(fi, x="muhimlik", y="xususiyat", orientation="h", color="muhimlik", title="Feature importance")
        fig.update_layout(yaxis={"categoryorder": "total ascending"})
        fig.update_coloraxes(showscale=False)
        st.plotly_chart(fig, width="stretch")

    rep = pd.DataFrame(m["hisobot"]).T.loc[["0", "1", "macro avg", "weighted avg"]]
    rep.index = ["Normal", "Fraud", "Macro Avg", "Weighted Avg"]
    st.dataframe(rep.drop(columns=["support"], errors="ignore").style.format("{:.4f}"), width="content")


def render_stream_simulation(art: dict, df: pd.DataFrame) -> None:
    st.subheader("Real vaqt tranzaksiya oqimi")
    col1, col2, col3, col4 = st.columns([1, 1.4, 1, 1])
    with col1:
        count = st.slider("Tranzaksiyalar soni", 10, 300, 50, 10)
    with col2:
        scenario = st.selectbox("Simulyatsiya turi", ["Real oqim (dataset fraud ulushi)", "Balanslangan test (50% fraud)", "Hujum ssenariysi (35% fraud)"])
    with col3:
        pause = st.slider("Pauza (sekund)", 0.0, 1.0, 0.05, 0.05)
    with col4:
        seed = st.number_input("Seed", 1, 999_999, 42)

    if not st.button("Oqimni boshlash", type="secondary", width="stretch"):
        st.info("Simulyatsiyani boshlash uchun tugmani bosing.")
        return

    stream = simulyatsiya_namunasini_tanla(df, count, scenario, int(seed))
    metrics_box = st.empty()
    table_box = st.empty()
    matrix_box = st.empty()
    progress = st.progress(0, "Oqim boshlandi...")
    rows: list[dict[str, object]] = []
    y_true: list[int] = []
    y_pred: list[int] = []
    y_prob: list[float] = []

    for idx, (_, row) in enumerate(stream.iterrows(), start=1):
        tx = {col: row[col] for col in FEATURES}
        result = tranzaksiyani_tekshir(art, tx)
        actual = int(row[TARGET])
        pred = int(result["qaror"] in ("REVIEW", "BLOCK"))
        prob = float(result["ehtimol"])
        y_true.append(actual)
        y_pred.append(pred)
        y_prob.append(prob)
        rows.append({
            "#": idx,
            "user_id": row["user_id"],
            "amount": round(float(row["amount"]), 2),
            "device": row["device"],
            "location": row["location"],
            "score_%": round(prob * 100, 1),
            "risk": result["xavf"],
            "decision": result["qaror"],
            "haqiqiy": "Fraud" if actual else "Normal",
            "bashorat": "Fraud" if pred else "Normal",
            "natija": "To'g'ri" if actual == pred else "Xato",
        })

        met = oqim_metrikalari(y_true, y_pred, y_prob)
        with metrics_box.container():
            c1, c2, c3, c4, c5 = st.columns(5)
            c1.metric("Ko'rilgan", f"{idx}/{count}")
            c2.metric("Accuracy", f"{met['accuracy'] * 100:.1f}%")
            c3.metric("Precision", f"{met['precision'] * 100:.1f}%")
            c4.metric("Recall", f"{met['recall'] * 100:.1f}%")
            c5.metric("F1", f"{met['f1']:.3f}")
        table_box.dataframe(pd.DataFrame(rows).tail(120), width="stretch", hide_index=True, height=420)
        progress.progress(int(idx / count * 100), f"{idx}/{count} tekshirildi")
        if pause > 0:
            time.sleep(float(pause))

    cm = confusion_matrix_np(np.array(y_true), np.array(y_pred))
    with matrix_box.container():
        fig = px.imshow(cm, text_auto=True, color_continuous_scale="Blues", x=["Pred Normal", "Pred Fraud"], y=["True Normal", "True Fraud"])
        fig.update_coloraxes(showscale=False)
        st.plotly_chart(fig, width="stretch")


def render_manual_score(art: dict) -> None:
    st.subheader("Bitta tranzaksiyani qo'lda tekshirish")
    with st.form("manual_score"):
        col1, col2, col3 = st.columns(3)
        with col1:
            amount = st.number_input("Miqdor ($)", 0.01, 100_000.0, 150.0, step=1.0)
            device = st.selectbox("Qurilma", ["ios", "android", "windows", "chrome", "safari", "linux", "emulator", "unknown"])
            location = st.selectbox("Joylashuv", ["tashkent", "samarkand", "fergana", "bukhara", "almaty", "istanbul", "dubai", "foreign_ip", "unknown"])
            hour = st.slider("Tranzaksiya soati", 0, 23, 14)
        with col2:
            freq = st.number_input("Bugungi tranzaksiyalar soni", 1, 60, 3)
            last_tx = st.number_input("Oxirgi tranzaksiyadan o'tgan vaqt (daqiqa)", 0.5, 1440.0, 120.0, step=5.0)
            age = st.number_input("Hisob yoshi (kun)", 1, 3650, 365)
            complaints = st.number_input("Shikoyatlar soni", 0, 20, 0)
        with col3:
            geo = st.checkbox("Geo anomaliya")
            loc_bad = st.checkbox("Joylashuv nomuvofiq")
            verified = st.checkbox("Qabul qiluvchi tasdiqlangan", value=True)
            blacklist = st.checkbox("Qora ro'yxat")
            vpn = st.checkbox("VPN / Proxy")
            mismatch = st.checkbox("Kategoriya nomuvofiq")
            limit = st.checkbox("Limit oshgan")
            high_value = st.checkbox("Yuqori miqdorli tranzaksiya")
            past_fraud = st.checkbox("Oldingi fraud tarixi")
        submitted = st.form_submit_button("Tranzaksiyani tekshir", type="primary", width="stretch")

    if not submitted:
        return

    tx = {
        "amount": amount,
        "device": device,
        "location": location,
        "transaction_hour": hour,
        "Transaction Frequency": freq,
        "Time Since Last Transaction": last_tx,
        "Account Age": age,
        "Fraud Complaints Count": complaints,
        "Geo-Location Flags": int(geo),
        "Location-Inconsistent Transactions": int(loc_bad),
        "Recipient Verification Status": int(verified),
        "Recipient Blacklist Status": int(blacklist),
        "VPN or Proxy Usage": int(vpn),
        "Merchant Category Mismatch": int(mismatch),
        "User Daily Limit Exceeded": int(limit),
        "Recent High-Value Transaction Flags": int(high_value),
        "Past Fraudulent Behavior Flags": int(past_fraud),
        "Normalized Transaction Amount": 0.0,
    }
    result = tranzaksiyani_tekshir(art, tx)
    color = {"LOW": "#22c55e", "MEDIUM": "#f59e0b", "HIGH": "#ef4444"}[result["xavf"]]
    c1, c2, c3 = st.columns(3)
    c1.metric("Fraud ehtimolligi", f"{result['ehtimol'] * 100:.1f}%")
    c2.metric("Xavf", result["xavf"])
    c3.metric("Qaror", result["qaror"])
    fig = go.Figure(go.Indicator(
        mode="gauge+number",
        value=round(result["ehtimol"] * 100, 1),
        title={"text": "Fraud score"},
        gauge={"axis": {"range": [0, 100]}, "bar": {"color": color}, "threshold": {"value": art["chegara"] * 100}},
    ))
    st.plotly_chart(fig, width="stretch")


def render_live(art: dict, df: pd.DataFrame) -> None:
    st.header("Jonli tekshirish")
    render_stream_simulation(art, df)
    st.divider()
    render_manual_score(art)


def sidebar(art: dict) -> None:
    with st.sidebar:
        st.title("SafeNet")
        st.caption("Fraud Detection System")
        st.divider()
        m = art["korsatkichlar"]
        st.markdown("**Model holati:** Faol")
        st.metric("ROC-AUC", f"{m['test_roc_auc']:.4f}")
        st.metric("F1", f"{m['test_f1']:.4f}")
        st.metric("Chegara", f"{m['chegara']:.2f}")
        st.caption(f"O'rgatilgan: {art['orgatish_sanasi']}")
        st.divider()
        st.caption(f"Dataset: {DATA_PATH.name}")
        st.caption(f"Algoritm: Numpy logistic risk model")


def main() -> None:
    df = load_data()
    artifact = modelni_yukla()
    if artifact is None:
        st.info("Birinchi ishga tushirish: deploy uchun tezkor model tayyorlanmoqda...")
        bar = st.progress(0, "Boshlanmoqda...")
        artifact = tezkor_orgatish_va_saqlash(df, progress=bar)
        st.cache_resource.clear()

    sidebar(artifact)
    tab1, tab2, tab3, tab4, tab5 = st.tabs(["Ko'rinish", "Ko'zgu", "O'rgatish", "Baholash", "Jonli tekshirish"])
    with tab1:
        render_overview(df)
    with tab2:
        render_explorer(df)
    with tab3:
        render_training(df)
    with tab4:
        render_performance(artifact)
    with tab5:
        render_live(artifact, df)


if __name__ == "__main__":
    main()
