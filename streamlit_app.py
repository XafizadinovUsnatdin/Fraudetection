"""
SafeNet — Anti-Fraud AI  (v3  Deep Learning)
Model: 3-hidden-layer MLP  128→64→32  Adam optimizer + weighted BCE
Streamlit Cloud: numpy/pandas only, no sklearn.
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

ROOT       = Path(__file__).parent
DATA_PATH  = ROOT / "data" / "synthetic_transactions.csv"
MODEL_PATH = ROOT / "ml"   / "fraud_model_v2.pkl"

UZS_SUFFIX = " so'm"

CATEGORICAL = ["device", "location"]
NUMERICAL   = [
    "amount", "transaction_hour",
    "Transaction Frequency", "Time Since Last Transaction",
    "Account Age", "Normalized Transaction Amount", "Fraud Complaints Count",
]
BINARY = [
    "Geo-Location Flags", "Location-Inconsistent Transactions",
    "Recipient Verification Status", "Recipient Blacklist Status",
    "VPN or Proxy Usage", "Merchant Category Mismatch",
    "User Daily Limit Exceeded", "Recent High-Value Transaction Flags",
    "Past Fraudulent Behavior Flags",
]
FEATURES = NUMERICAL + BINARY + CATEGORICAL
TARGET   = "isFraud"

# Interaction features added at transform time
RISKY_DEV = {"emulator", "unknown", "linux"}
RISKY_LOC = {"foreign_ip", "unknown"}
INTERACT_COLS = [
    "risky_device", "risky_location", "night_flag",
    "dev_x_night", "dev_x_loc", "loc_x_night", "vpn_x_blacklist",
]

def add_interactions(df: pd.DataFrame) -> pd.DataFrame:
    d = df.copy()
    d["risky_device"]    = d["device"].isin(RISKY_DEV).astype(float)
    d["risky_location"]  = d["location"].isin(RISKY_LOC).astype(float)
    d["night_flag"]      = d["transaction_hour"].between(0, 5).astype(float)
    d["dev_x_night"]     = d["risky_device"]   * d["night_flag"]
    d["dev_x_loc"]       = d["risky_device"]   * d["risky_location"]
    d["loc_x_night"]     = d["risky_location"] * d["night_flag"]
    d["vpn_x_blacklist"] = d["VPN or Proxy Usage"] * d["Recipient Blacklist Status"]
    return d

EXT_FEATURES = FEATURES + INTERACT_COLS

st.set_page_config(
    page_title="SafeNet Fraud Detection",
    page_icon="🛡️",
    layout="wide",
    initial_sidebar_state="expanded",
)
st.markdown("""
<style>
[data-testid="stMetricValue"] { font-size:1.4rem; font-weight:700; }
.block-container { padding-top:1.5rem; }
</style>
""", unsafe_allow_html=True)

# ══════════════════════════════════════════════════════════════════════════════
# Math / metric helpers  (unchanged)
# ══════════════════════════════════════════════════════════════════════════════

@st.cache_data(show_spinner="Ma'lumotlar yuklanmoqda...")
def load_data() -> pd.DataFrame:
    return pd.read_csv(DATA_PATH)

def sigmoid(x: np.ndarray) -> np.ndarray:
    return 1.0 / (1.0 + np.exp(-np.clip(x, -35, 35)))

def format_uzs(value: float) -> str:
    return f"{float(value):,.0f}{UZS_SUFFIX}"

def stratified_split(df, target, test_size=0.25, seed=42):
    rng = np.random.default_rng(seed)
    train_parts, test_parts = [], []
    for _, part in df.groupby(target):
        idx = part.index.to_numpy().copy(); rng.shuffle(idx)
        n_test = max(1, int(len(idx) * test_size))
        test_parts.append(part.loc[idx[:n_test]])
        train_parts.append(part.loc[idx[n_test:]])
    train = pd.concat(train_parts).sample(frac=1, random_state=seed).reset_index(drop=True)
    test  = pd.concat(test_parts).sample(frac=1, random_state=seed+1).reset_index(drop=True)
    return train, test

def stratified_sample(df, n, seed=42):
    if len(df) <= n:
        return df.sample(frac=1, random_state=seed).reset_index(drop=True)
    parts = []
    for label, part in df.groupby(TARGET):
        take = max(1, int(n * len(part)/len(df)))
        parts.append(part.sample(n=take, random_state=seed+int(label)))
    return pd.concat(parts).sample(frac=1, random_state=seed).reset_index(drop=True)

def confusion_matrix_np(y_true, y_pred):
    yt, yp = np.asarray(y_true).astype(int), np.asarray(y_pred).astype(int)
    return np.array([[int(((yt==0)&(yp==0)).sum()), int(((yt==0)&(yp==1)).sum())],
                     [int(((yt==1)&(yp==0)).sum()), int(((yt==1)&(yp==1)).sum())]])

def precision_recall_f1(y_true, y_pred):
    cm = confusion_matrix_np(y_true, y_pred)
    tp, fp, fn = cm[1,1], cm[0,1], cm[1,0]
    p  = tp / max(tp+fp, 1)
    r  = tp / max(tp+fn, 1)
    f1 = 2*p*r / max(p+r, 1e-12)
    return float(p), float(r), float(f1)

def roc_auc_np(y_true, score):
    yt = np.asarray(y_true).astype(int)
    sc = np.asarray(score).astype(float)
    pos, neg = int((yt==1).sum()), int((yt==0).sum())
    if pos==0 or neg==0: return 0.5
    ranks = pd.Series(sc).rank(method="average").to_numpy()
    return float((ranks[yt==1].sum() - pos*(pos+1)/2) / (pos*neg))

def average_precision_np(y_true, score):
    yt = np.asarray(y_true).astype(int)
    sc = np.asarray(score).astype(float)
    order = np.argsort(-sc); y = yt[order]; total_pos = max(int(y.sum()),1)
    tp = np.cumsum(y==1); fp = np.cumsum(y==0)
    prec = tp / np.maximum(tp+fp,1); rec = tp / total_pos
    return float(np.sum((rec - np.r_[0.0, rec[:-1]]) * prec))

def roc_curve_np(y_true, score):
    thresholds = np.quantile(score, np.linspace(1,0,80))
    yt = np.asarray(y_true).astype(int)
    pos = max(int((yt==1).sum()),1); neg = max(int((yt==0).sum()),1)
    fpr, tpr = [], []
    for t in thresholds:
        pred = (score>=t).astype(int)
        cm = confusion_matrix_np(yt, pred)
        fpr.append(cm[0,1]/neg); tpr.append(cm[1,1]/pos)
    return np.array([0.,*fpr,1.]), np.array([0.,*tpr,1.])

def pr_curve_np(y_true, score):
    thresholds = np.quantile(score, np.linspace(1,0,80))
    precs, recs = [], []
    for t in thresholds:
        pred = (score>=t).astype(int)
        p,r,_ = precision_recall_f1(y_true, pred)
        precs.append(p); recs.append(r)
    return np.array(precs), np.array(recs)

def classification_report_np(y_true, y_pred):
    rows = {}; supports = []
    for label in [0,1]:
        p,r,f1 = precision_recall_f1((y_true==label).astype(int),(y_pred==label).astype(int))
        sup = int((y_true==label).sum()); supports.append(sup)
        rows[str(label)] = {"precision":p,"recall":r,"f1-score":f1,"support":sup}
    acc   = float((y_true==y_pred).mean()); total = max(sum(supports),1)
    rows["accuracy"] = acc
    rows["macro avg"]    = {"precision": np.mean([rows["0"]["precision"],rows["1"]["precision"]]),
                            "recall":    np.mean([rows["0"]["recall"],   rows["1"]["recall"]]),
                            "f1-score":  np.mean([rows["0"]["f1-score"], rows["1"]["f1-score"]]),
                            "support": total}
    rows["weighted avg"] = {"precision": (rows["0"]["precision"]*supports[0]+rows["1"]["precision"]*supports[1])/total,
                            "recall":    (rows["0"]["recall"]   *supports[0]+rows["1"]["recall"]   *supports[1])/total,
                            "f1-score":  (rows["0"]["f1-score"] *supports[0]+rows["1"]["f1-score"] *supports[1])/total,
                            "support": total}
    return rows

def optimal_threshold(y_true, score):
    best_f1, best_t = -1., 0.5
    for t in np.linspace(0.05, 0.95, 91):
        pred = (score>=t).astype(int)
        _,_,f1 = precision_recall_f1(y_true, pred)
        if f1 > best_f1: best_f1=f1; best_t=float(t)
    return best_t

# ══════════════════════════════════════════════════════════════════════════════
# DEEP FRAUD NET  — 3-hidden-layer MLP  (replaces logistic model)
# ══════════════════════════════════════════════════════════════════════════════

class DeepFraudNet:
    """
    Architecture : Input → Dense(128,ReLU) → Dense(64,ReLU) → Dense(32,ReLU) → Dense(1,Sigmoid)
    Optimizer    : Adam  (lr=0.001, β1=0.9, β2=0.999)
    Loss         : Class-weighted binary cross-entropy
    Regularizer  : L2 weight decay
    No sklearn dependency — pure numpy.
    """

    def __init__(self, hidden=(128, 64, 32), epochs=80, lr=0.001, batch=256, l2=5e-5):
        self.hidden = tuple(hidden)
        self.epochs = epochs
        self.lr     = lr
        self.batch  = batch
        self.l2     = l2
        self.numeric_mean:  pd.Series | None       = None
        self.numeric_std:   pd.Series | None       = None
        self.categories:    dict[str, list]        = {}
        self.bin_edges:     dict[str, np.ndarray]  = {}
        self.feature_names: list[str]              = []
        self.params:        dict[str, np.ndarray]  = {}
        self.history:       list[float]            = []

    # ── schema ────────────────────────────────────────────────────────────────
    def _fit_schema(self, df: pd.DataFrame) -> None:
        num_cols = NUMERICAL + BINARY
        self.numeric_mean = df[num_cols].astype(float).mean()
        self.numeric_std  = df[num_cols].astype(float).std().replace(0,1).fillna(1)
        self.categories   = {c: sorted(df[c].astype(str).fillna("unknown").unique())
                             for c in CATEGORICAL}
        self.bin_edges = {}
        for c in NUMERICAL:
            q = df[c].astype(float).quantile(np.linspace(0.1,0.9,9)).to_numpy()
            self.bin_edges[c] = np.unique(q)
        self.feature_names = list(num_cols)
        for c, edges in self.bin_edges.items():
            self.feature_names += [f"{c}_bin_{i}" for i in range(len(edges)+1)]
        for c in CATEGORICAL:
            self.feature_names += [f"{c}_{v}" for v in self.categories[c]]

    def transform(self, df: pd.DataFrame) -> np.ndarray:
        if self.numeric_mean is None: raise RuntimeError("Not fitted")
        num_cols = NUMERICAL + BINARY
        num = (df[num_cols].astype(float) - self.numeric_mean) / self.numeric_std
        arrs = [num.to_numpy(dtype=float)]
        for c, edges in self.bin_edges.items():
            vals = df[c].astype(float).to_numpy()
            bins = np.searchsorted(edges, vals, side="right")
            oh   = np.zeros((len(df), len(edges)+1), dtype=float)
            oh[np.arange(len(df)), bins] = 1.0
            arrs.append(oh)
        for c in CATEGORICAL:
            vals = df[c].astype(str).fillna("unknown"); cats = self.categories[c]
            idx  = {v:i for i,v in enumerate(cats)}
            oh   = np.zeros((len(df), len(cats)), dtype=float)
            for ri,v in enumerate(vals):
                ci = idx.get(v)
                if ci is not None: oh[ri, ci] = 1.0
            arrs.append(oh)
        return np.hstack(arrs)

    # ── weight init ───────────────────────────────────────────────────────────
    def _init_weights(self, n_in: int) -> None:
        dims = [n_in] + list(self.hidden) + [1]
        rng  = np.random.default_rng(42)
        for i in range(len(dims)-1):
            self.params[f"W{i+1}"] = rng.standard_normal((dims[i], dims[i+1])) * np.sqrt(2.0/dims[i])
            self.params[f"b{i+1}"] = np.zeros(dims[i+1])

    # ── forward ───────────────────────────────────────────────────────────────
    def _forward(self, X: np.ndarray) -> tuple[np.ndarray, dict]:
        cache: dict = {"A0": X}
        A = X; n_hidden = len(self.hidden)
        for i in range(1, n_hidden+2):
            Z = A @ self.params[f"W{i}"] + self.params[f"b{i}"]
            A = np.maximum(0.0, Z) if i <= n_hidden else sigmoid(Z.ravel())
            cache[f"Z{i}"] = Z; cache[f"A{i}"] = A
        return A, cache

    # ── backward ─────────────────────────────────────────────────────────────
    def _backward(self, cache: dict, y: np.ndarray, sw: np.ndarray) -> dict:
        n_hidden = len(self.hidden); L = n_hidden+1; n = len(y); grads = {}
        y_hat = cache[f"A{L}"]
        dZ    = (y_hat - y) * sw / n                         # output delta
        A_prev = cache[f"A{L-1}"]
        grads[f"W{L}"] = A_prev.T @ dZ.reshape(-1,1) + self.l2 * self.params[f"W{L}"]
        grads[f"b{L}"] = np.array([dZ.sum()])
        dA = dZ.reshape(-1,1) @ self.params[f"W{L}"].T
        for i in range(L-1, 0, -1):
            dZ2   = dA * (cache[f"Z{i}"] > 0)               # ReLU grad
            A_prev = cache[f"A{i-1}"]
            grads[f"W{i}"] = A_prev.T @ dZ2 + self.l2 * self.params[f"W{i}"]
            grads[f"b{i}"] = dZ2.sum(axis=0)
            dA = dZ2 @ self.params[f"W{i}"].T
        return grads

    # ── fit ───────────────────────────────────────────────────────────────────
    def fit(self, df: pd.DataFrame, y: pd.Series, progress=None) -> "DeepFraudNet":
        self._fit_schema(df)
        X = self.transform(df); y_arr = y.to_numpy(dtype=float); n, n_in = X.shape
        self._init_weights(n_in)
        n_pos = max(y_arr.sum(), 1); n_neg = max(n-n_pos, 1)
        # sqrt-based class weighting capped at 5x (less extreme than n/(2*n_pos))
        ratio = min(np.sqrt(n_neg / n_pos), 5.0)
        sw = np.where(y_arr==1, ratio, 1.0)
        m_adam = {k: np.zeros_like(v) for k,v in self.params.items()}
        v_adam = {k: np.zeros_like(v) for k,v in self.params.items()}
        beta1, beta2, eps_adam = 0.9, 0.999, 1e-8; t = 0
        indices = np.arange(n)
        for epoch in range(self.epochs):
            np.random.seed(epoch); np.random.shuffle(indices)
            epoch_loss = 0.0; nb = 0
            for start in range(0, n, self.batch):
                bi = indices[start:start+self.batch]
                Xb, yb, swb = X[bi], y_arr[bi], sw[bi]
                y_hat, cache = self._forward(Xb)
                e = 1e-9
                epoch_loss += -float(np.mean(swb*(yb*np.log(y_hat+e)+(1-yb)*np.log(1-y_hat+e))))
                nb += 1
                grads = self._backward(cache, yb, swb)
                t += 1
                for k in self.params:
                    m_adam[k] = beta1*m_adam[k] + (1-beta1)*grads[k]
                    v_adam[k] = beta2*v_adam[k] + (1-beta2)*grads[k]**2
                    mh = m_adam[k]/(1-beta1**t); vh = v_adam[k]/(1-beta2**t)
                    self.params[k] -= self.lr * mh / (np.sqrt(vh)+eps_adam)
            self.history.append(epoch_loss/max(nb,1))
            if progress and (epoch+1) % max(self.epochs//10,1) == 0:
                pct = int((epoch+1)/self.epochs*80)+10
                progress.progress(min(pct,90), f"Epoch {epoch+1}/{self.epochs}  loss={self.history[-1]:.4f}")
        return self

    def predict_proba(self, df: pd.DataFrame) -> np.ndarray:
        y_hat, _ = self._forward(self.transform(df))
        # cap at 0.995 so displayed score never rounds to 100%
        y_hat = np.clip(y_hat, 0.005, 0.995)
        return np.column_stack([1-y_hat, y_hat])

    @property
    def feature_importances_(self) -> np.ndarray:
        W1 = self.params.get("W1")
        if W1 is None: return np.zeros(len(self.feature_names))
        imp = np.abs(W1).sum(axis=1)
        return imp / max(imp.sum(), 1e-12)

# ══════════════════════════════════════════════════════════════════════════════
# Artifact helpers
# ══════════════════════════════════════════════════════════════════════════════

def build_artifact(model: DeepFraudNet, train_df, test_df, source_df) -> dict:
    feat = EXT_FEATURES if set(EXT_FEATURES).issubset(test_df.columns) else FEATURES
    y_test = test_df[TARGET].to_numpy(dtype=int)
    score  = model.predict_proba(test_df[feat])[:, 1]
    threshold = optimal_threshold(y_test, score)
    pred  = (score >= threshold).astype(int)
    p, r, f1 = precision_recall_f1(y_test, pred)
    fi = pd.DataFrame({"xususiyat": model.feature_names,
                       "muhimlik":  model.feature_importances_}).sort_values("muhimlik", ascending=False)
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
        "model_turi": "DeepFraudNet (256→128→64, Adam+Interactions)",
        "korsatkichlar": {
            "kv_roc_auc": roc_auc_np(y_test, score),
            "kv_roc_auc_std": 0.0,
            "kv_f1": f1, "kv_aniqlik": p, "kv_qamrov": r,
            "test_roc_auc": roc_auc_np(y_test, score),
            "test_ort_aniqlik": average_precision_np(y_test, score),
            "test_f1": f1, "chegara": threshold,
            "chalkash_matritsa": confusion_matrix_np(y_test, pred),
            "roc_egri": roc_curve_np(y_test, score),
            "pr_egri":  pr_curve_np(y_test, score),
            "hisobot":  classification_report_np(y_test, pred),
            "loss_tarix": model.history,
        },
    }

def save_artifact(artifact: dict) -> None:
    MODEL_PATH.parent.mkdir(parents=True, exist_ok=True)
    with MODEL_PATH.open("wb") as f: pickle.dump(artifact, f)

def train_deep_model(df: pd.DataFrame, sample_size=20000, epochs=80,
                     hidden=(256,128,64), progress=None) -> dict:
    df_ext = add_interactions(df)
    work   = stratified_sample(df_ext, min(sample_size, len(df_ext)), seed=42)
    if progress: progress.progress(15, "Dataset va interaction features tayyorlandi...")
    train_df, test_df = stratified_split(work, TARGET, 0.20, 42)
    model = DeepFraudNet(hidden=hidden, epochs=epochs, batch=512, l2=1e-5)
    if progress: progress.progress(20, "DeepFraudNet o'rgatilmoqda (256→128→64, Adam)...")
    model.fit(train_df[EXT_FEATURES], train_df[TARGET], progress=progress)
    if progress: progress.progress(92, "Metrikalar hisoblanmoqda...")
    artifact = build_artifact(model, train_df, test_df, df_ext)
    save_artifact(artifact)
    if progress: progress.progress(100, "Model tayyor!")
    return artifact

def orgatish_va_saqlash(df, daraxt_soni=200, max_chuqurlik=None, min_barglar=None, progress=None):
    epochs = int(np.clip(daraxt_soni // 2, 60, 120))
    return train_deep_model(df, sample_size=min(len(df), 30000), epochs=epochs, progress=progress)

def tezkor_orgatish_va_saqlash(df, progress=None):
    return train_deep_model(df, sample_size=15000, epochs=60, progress=progress)

def artifactni_moslashtir(a):
    return a if isinstance(a, dict) and "korsatkichlar" in a and "pipeline" in a else None

@st.cache_resource(show_spinner=False)
def modelni_yukla() -> dict | None:
    if MODEL_PATH.exists():
        try:
            with MODEL_PATH.open("rb") as f: return artifactni_moslashtir(pickle.load(f))
        except Exception: return None
    return None

def ensure_model(df) -> dict:
    art = modelni_yukla()
    return art if art else tezkor_orgatish_va_saqlash(df)

# ══════════════════════════════════════════════════════════════════════════════
# Risk signal engine
# ══════════════════════════════════════════════════════════════════════════════

def compute_risk_signals(row: pd.Series) -> list[tuple[str, float, str]]:
    """Return list of (signal_name, risk_0_1, description) for display."""
    signals: list[tuple[str, float, str]] = []

    # 1. Device
    dev = str(row.get("device","unknown")).lower()
    if   dev in ("emulator","unknown"): signals.append(("📱 Qurilma", 0.92, f"Emulator / noma'lum qurilma — bank ilovasi emas"))
    elif dev == "linux":                signals.append(("📱 Qurilma", 0.40, f"Linux — kamdan-kam uchraydigan platforma"))
    else:                               signals.append(("📱 Qurilma", 0.04, f"Oddiy qurilma: {dev}"))

    # 2. Location
    loc = str(row.get("location","unknown")).lower()
    if   loc in ("foreign_ip","unknown"): signals.append(("📍 Lokatsiya", 0.88, "Chet el / noma'lum IP manzildan ulanish"))
    elif loc in ("istanbul","dubai"):     signals.append(("📍 Lokatsiya", 0.35, f"Xalqaro lokatsiya: {loc}"))
    elif loc == "almaty":                 signals.append(("📍 Lokatsiya", 0.22, "Qo'shni mamlakat: Olmaota"))
    else:                                 signals.append(("📍 Lokatsiya", 0.04, f"Mahalliy: {loc}"))

    # 3. Amount anomaly
    norm = float(row.get("Normalized Transaction Amount", 0))
    amt  = float(row.get("amount", 0))
    if   norm > 0.88: signals.append(("💰 Summa", 0.85, f"Juda katta summa: {format_uzs(amt)}"))
    elif norm > 0.65: signals.append(("💰 Summa", 0.50, f"Yuqori summa: {format_uzs(amt)}"))
    elif norm > 0.40: signals.append(("💰 Summa", 0.22, f"O'rtacha summa: {format_uzs(amt)}"))
    else:             signals.append(("💰 Summa", 0.04, f"Oddiy summa: {format_uzs(amt)}"))

    # 4. Behavioral flags
    vpn       = int(row.get("VPN or Proxy Usage", 0))
    blacklist = int(row.get("Recipient Blacklist Status", 0))
    past_fr   = int(row.get("Past Fraudulent Behavior Flags", 0))
    loc_bad   = int(row.get("Location-Inconsistent Transactions", 0))
    geo_flag  = int(row.get("Geo-Location Flags", 0))
    beh_score = min(1.0, vpn*0.35 + blacklist*0.45 + past_fr*0.40 + loc_bad*0.25 + geo_flag*0.20)
    parts = ([("VPN/Proxy" if vpn else ""),("Qora ro'yxat" if blacklist else ""),
              ("Fraud tarixi" if past_fr else ""),("Joylashuv noto'g'ri" if loc_bad else ""),
              ("Geo anomaliya" if geo_flag else "")])
    beh_desc = ", ".join(p for p in parts if p) or "Xatti-harakatda anomaliya yo'q"
    signals.append(("🧠 Xulq-atvor", beh_score, beh_desc))

    # 5. Time
    hour = int(row.get("transaction_hour", 12))
    if   0  <= hour <= 5:  signals.append(("🕐 Vaqt", 0.62, f"Tun saati {hour:02d}:xx — fraud eng ko'p bu vaqtda"))
    elif 22 <= hour <= 23: signals.append(("🕐 Vaqt", 0.28, f"Kech kechasi {hour:02d}:xx"))
    else:                  signals.append(("🕐 Vaqt", 0.03, f"Ish vaqti {hour:02d}:xx"))

    return signals

def risk_bar_md(name: str, score: float, desc: str) -> str:
    pct  = int(score * 100)
    fill = int(score * 18)
    bar  = "█"*fill + "░"*(18-fill)
    if   score < 0.30: emoji, color = "🟢", "past"
    elif score < 0.65: emoji, color = "🟡", "o'rta"
    else:              emoji, color = "🔴", "yuqori"
    return f"{emoji} **{name}** `{bar}` **{pct}%** ({color})  \n&nbsp;&nbsp;&nbsp;↳ *{desc}*"

# ══════════════════════════════════════════════════════════════════════════════
# Transaction helpers
# ══════════════════════════════════════════════════════════════════════════════

def tranzaksiyani_tekshir(artifact: dict, tx: dict) -> dict:
    amount = float(tx.get("amount", 1_500_000))
    a_min  = artifact["miqdor_stat"]["min"]; a_max = artifact["miqdor_stat"]["max"]
    tx["Normalized Transaction Amount"] = float(np.clip((amount-a_min)/max(a_max-a_min,1e-9),0,1))
    row    = {f: 0 for f in EXT_FEATURES}; row.update(tx)
    row_df = add_interactions(pd.DataFrame([row]))
    feat   = EXT_FEATURES if set(EXT_FEATURES).issubset(row_df.columns) else FEATURES
    prob   = float(artifact["pipeline"].predict_proba(row_df[feat])[0,1])
    thr    = artifact["chegara"]; block_thr = max(thr*1.6, 0.70)
    if   prob >= block_thr: risk, decision = "HIGH",   "BLOCK"
    elif prob >= thr:       risk, decision = "MEDIUM",  "REVIEW"
    else:                   risk, decision = "LOW",     "ALLOW"
    return {"ehtimol": prob, "xavf": risk, "qaror": decision}

def simulyatsiya_namunasini_tanla(df, soni, ssenariy, seed):
    rng = np.random.default_rng(seed)
    def take(label,n):
        p=df[df[TARGET]==label]; return p.sample(n=n,replace=len(p)<n,random_state=int(rng.integers(1,1000000)))
    if   ssenariy == "Balanslangan test (50% fraud)":   fn = soni//2
    elif ssenariy == "Hujum ssenariysi (35% fraud)":    fn = max(1,int(soni*0.35))
    else: return df.sample(n=soni,replace=len(df)<soni,random_state=seed).reset_index(drop=True)
    return pd.concat([take(1,fn),take(0,soni-fn)]).sample(frac=1,random_state=seed).reset_index(drop=True)

def oqim_metrikalari(y_true, y_pred, y_prob):
    if not y_true: return {"accuracy":0,"precision":0,"recall":0,"f1":0,"roc_auc":None}
    yt,yp,pr = np.array(y_true),np.array(y_pred),np.array(y_prob)
    p,r,f1   = precision_recall_f1(yt,yp)
    return {"accuracy":float((yt==yp).mean()),"precision":p,"recall":r,"f1":f1,
            "roc_auc": roc_auc_np(yt,pr) if len(np.unique(yt))==2 else None}

def decision_label(d):
    return {"ALLOW":"✅ Ruxsat berildi","REVIEW":"⚠️ Tekshiruv kerak","BLOCK":"🚫 Bloklandi"}.get(d,d)

def status_label(s):
    return {"SETTLED":"Muvaffaqiyatli yakunlandi","MANUAL_REVIEW_QUEUE":"Operator navbatiga yuborildi","DECLINED":"Rad etildi"}.get(s,s)

# ══════════════════════════════════════════════════════════════════════════════
# RENDER — Overview
# ══════════════════════════════════════════════════════════════════════════════

def render_overview(df):
    st.header("Ma'lumotlar ko'rinishi")
    total = len(df); fraud = int(df[TARGET].sum()); normal = total-fraud
    c1,c2,c3,c4 = st.columns(4)
    c1.metric("Jami tranzaksiyalar", f"{total:,}")
    c2.metric("Fraud holatlar", f"{fraud:,}", f"{fraud/total*100:.1f}%")
    c3.metric("Normal", f"{normal:,}")
    c4.metric("Ustunlar", str(len(df.columns)))
    col1,col2 = st.columns(2)
    with col1:
        fig=px.pie(values=[fraud,normal],names=["Fraud","Normal"],hole=0.42,
                   color_discrete_sequence=["#ef4444","#22c55e"],title="Fraud / Normal taqsimoti")
        st.plotly_chart(fig, use_container_width=True)
    with col2:
        dev=(df.groupby("device")[TARGET].agg(fraud="sum",jami="count")
             .assign(rate=lambda x:x["fraud"]/x["jami"]*100).reset_index().sort_values("rate",ascending=False))
        fig=px.bar(dev,x="device",y="rate",color="rate",color_continuous_scale="RdYlGn_r",
                   title="Qurilma bo'yicha fraud darajasi"); fig.update_coloraxes(showscale=False)
        st.plotly_chart(fig, use_container_width=True)
    col3,col4 = st.columns(2)
    with col3:
        hour=df.groupby("transaction_hour")[TARGET].mean()*100
        fig=go.Figure(go.Scatter(x=hour.index,y=hour.values,mode="lines+markers",line=dict(color="#3b82f6")))
        fig.update_layout(title="Soat bo'yicha fraud darajasi",xaxis_title="Soat",yaxis_title="Fraud %")
        st.plotly_chart(fig, use_container_width=True)
    with col4:
        samp=df.sample(min(6000,len(df)),random_state=42)
        fig=px.histogram(samp,x="amount",color=samp[TARGET].map({0:"Normal",1:"Fraud"}),
                         nbins=60,barmode="overlay",opacity=0.72,
                         title="Tranzaksiya miqdori taqsimoti",labels={"amount":"Summa"})
        st.plotly_chart(fig, use_container_width=True)

# ══════════════════════════════════════════════════════════════════════════════
# RENDER — Explorer
# ══════════════════════════════════════════════════════════════════════════════

def render_explorer(df):
    st.header("Ma'lumotlar ko'zgusi")
    c1,c2,c3 = st.columns(3)
    with c1: devices   = st.multiselect("Qurilma",   sorted(df["device"].unique()),   default=list(df["device"].unique()))
    with c2: locations = st.multiselect("Joylashuv", sorted(df["location"].unique()), default=list(df["location"].unique()))
    with c3: label     = st.radio("Yorliq",["Barchasi","Normal (0)","Fraud (1)"],horizontal=True)
    mask = df["device"].isin(devices) & df["location"].isin(locations)
    if label=="Normal (0)": mask &= df[TARGET]==0
    elif label=="Fraud (1)": mask &= df[TARGET]==1
    fdf = df[mask]; st.info(f"Filtrlangan: {len(fdf):,} / {len(df):,}")
    if fdf.empty: return
    c1,c2 = st.columns(2)
    with c1:
        samp=fdf.sample(min(3000,len(fdf)),random_state=42)
        fig=px.scatter(samp,x="amount",y="transaction_hour",
                       color=samp[TARGET].map({0:"Normal",1:"Fraud"}),opacity=0.55,
                       title="Miqdor va vaqt",labels={"amount":"Summa","transaction_hour":"Soat"})
        st.plotly_chart(fig, use_container_width=True)
    with c2:
        cc=["amount","Transaction Frequency","Time Since Last Transaction","Account Age","Fraud Complaints Count",TARGET]
        fig=px.imshow(fdf[cc].corr().round(3),text_auto=".2f",color_continuous_scale="RdBu_r",zmin=-1,zmax=1,title="Korrelyatsiya")
        st.plotly_chart(fig, use_container_width=True)
    st.dataframe(fdf.head(200), use_container_width=True, hide_index=True)

# ══════════════════════════════════════════════════════════════════════════════
# RENDER — Training
# ══════════════════════════════════════════════════════════════════════════════

def render_training(df):
    st.header("Model o'rgatish")
    st.info("**DeepFraudNet** — 3 qatlamli neyron tarmoq: 128→64→32→1, Adam optimizer, class-weighted BCE loss.")
    with st.expander("Model arxitekturasi"):
        st.markdown("""
| Qatlam | Neyronlar | Aktivatsiya | Parametrlar |
|--------|-----------|-------------|-------------|
| Input  | ~200      | —           | —           |
| Dense 1| **256**   | ReLU        | ~51,200     |
| Dense 2| **128**   | ReLU        | ~32,896     |
| Dense 3| **64**    | ReLU        | ~8,256      |
| Output | **1**     | Sigmoid     | 65          |
| **Jami** | —      | —           | **~92,400** |

**Optimizer:** Adam (lr=0.001, β₁=0.9, β₂=0.999)
**Loss:** sqrt-weighted Binary Cross-Entropy (fraud ratio cap: 5×)
**Regularization:** L2 weight decay (λ=1e-5)
**Init:** He initialization (√2/fan_in)
**Extra features:** 7 ta interaction belgisi (device×location, device×night, vpn×blacklist...)
        """)
    c1,c2,c3 = st.columns(3)
    with c1: sample_size = st.slider("Training sample", 5000, min(40000,len(df)), 14000, 1000)
    with c2: epochs      = st.slider("Epochs",         40, 150, 80, 10)
    with c3: hidden_size = st.selectbox("Arxitektura", ["128→64→32 (standart)","256→128→64 (katta)","64→32→16 (tez)"])
    hidden_map = {"128→64→32 (standart)":(128,64,32),"256→128→64 (katta)":(256,128,64),"64→32→16 (tez)":(64,32,16)}
    art = modelni_yukla()
    if art:
        m=art["korsatkichlar"]
        st.success(f"Faol model: {art.get('model_turi','DeepFraudNet')} | ROC-AUC {m['test_roc_auc']:.4f} | F1 {m['test_f1']:.4f} | Threshold {m['chegara']:.2f}")
    if st.button("Modelni qayta o'rgat", type="primary"):
        bar=st.progress(0,"Boshlanmoqda...")
        art=train_deep_model(df,sample_size=sample_size,epochs=epochs,hidden=hidden_map[hidden_size],progress=bar)
        st.cache_resource.clear()
        m=art["korsatkichlar"]
        c1,c2,c3,c4=st.columns(4)
        c1.metric("ROC-AUC",f"{m['test_roc_auc']:.4f}"); c2.metric("Avg Precision",f"{m['test_ort_aniqlik']:.4f}")
        c3.metric("F1",f"{m['test_f1']:.4f}"); c4.metric("Threshold",f"{m['chegara']:.2f}")
        if m.get("loss_tarix"):
            fig=go.Figure(go.Scatter(y=m["loss_tarix"],mode="lines",line=dict(color="#3b82f6")))
            fig.update_layout(title="O'qitish davomida loss (BCE)",xaxis_title="Epoch",yaxis_title="Loss")
            st.plotly_chart(fig, use_container_width=True)

# ══════════════════════════════════════════════════════════════════════════════
# RENDER — Performance
# ══════════════════════════════════════════════════════════════════════════════

def render_performance(art):
    st.header("Model baholash")
    m=art["korsatkichlar"]
    c1,c2,c3,c4=st.columns(4)
    c1.metric("ROC-AUC",f"{m['test_roc_auc']:.4f}"); c2.metric("Avg Precision",f"{m['test_ort_aniqlik']:.4f}")
    c3.metric("F1",f"{m['test_f1']:.4f}"); c4.metric("Chegara",f"{m['chegara']:.2f}")
    c1,c2 = st.columns(2)
    with c1:
        fig=px.imshow(m["chalkash_matritsa"],text_auto=True,color_continuous_scale="Blues",
                      x=["Bashorat: Normal","Bashorat: Fraud"],y=["Haqiqiy: Normal","Haqiqiy: Fraud"],title="Confusion matrix")
        fig.update_coloraxes(showscale=False); st.plotly_chart(fig, use_container_width=True)
    with c2:
        fpr,tpr=m["roc_egri"]
        fig=go.Figure()
        fig.add_trace(go.Scatter(x=fpr,y=tpr,mode="lines",name=f"AUC={m['test_roc_auc']:.4f}",line=dict(color="#3b82f6",width=2)))
        fig.add_trace(go.Scatter(x=[0,1],y=[0,1],mode="lines",line=dict(dash="dash",color="gray"),name="Tasodifiy"))
        fig.update_layout(title="ROC egri chizig'i",xaxis_title="FPR",yaxis_title="TPR")
        st.plotly_chart(fig, use_container_width=True)
    c3,c4 = st.columns(2)
    with c3:
        pr,rc=m["pr_egri"]
        fig=go.Figure(go.Scatter(x=rc,y=pr,mode="lines",name="PR",line=dict(color="#8b5cf6",width=2)))
        fig.update_layout(title="Precision-Recall egri chizig'i",xaxis_title="Recall",yaxis_title="Precision")
        st.plotly_chart(fig, use_container_width=True)
    with c4:
        fi=art["xususiyat_muhimlik"].head(15)
        fig=px.bar(fi,x="muhimlik",y="xususiyat",orientation="h",color="muhimlik",
                   color_continuous_scale="Blues",title="Feature importance (W₁ normlari)")
        fig.update_layout(yaxis={"categoryorder":"total ascending"}); fig.update_coloraxes(showscale=False)
        st.plotly_chart(fig, use_container_width=True)
    if m.get("loss_tarix"):
        fig=go.Figure(go.Scatter(y=m["loss_tarix"],mode="lines",line=dict(color="#f59e0b",width=2)))
        fig.update_layout(title="O'qitish loss tarixi",xaxis_title="Epoch",yaxis_title="BCE Loss")
        st.plotly_chart(fig, use_container_width=True)
    rep=pd.DataFrame(m["hisobot"]).T.loc[["0","1","macro avg","weighted avg"]]
    rep.index=["Normal","Fraud","Macro Avg","Weighted Avg"]
    st.dataframe(rep.drop(columns=["support"],errors="ignore").style.format("{:.4f}"), use_container_width=True)

# ══════════════════════════════════════════════════════════════════════════════
# RENDER — Live (IMPROVED)
# ══════════════════════════════════════════════════════════════════════════════

MERCHANTS = [
    ("Uzum Market",        "🛍️",  "E-tijorat",        "UzCard"),
    ("Payme P2P",          "💸",  "Pul o'tkazma",     "Humo"),
    ("Click Wallet",       "📱",  "Elektron hamyon",  "UzCard"),
    ("Anor Supermarket",   "🏪",  "Oziq-ovqat",       "Humo"),
    ("Crypto Exchange UZ", "💎",  "Raqamli aktiv",    "UzCard"),
    ("UzCard ATM-5821",    "🏧",  "Naqd pul olish",   "UzCard"),
    ("Beeline UZ",         "📡",  "Telekom",          "Humo"),
    ("AliExpress.UZ",      "📦",  "Xalqaro e-tijorat","Visa"),
]
CHANNELS = ["📲 Mobile ilovasi", "💻 Web brauzer", "🏧 POS terminal", "🔗 API o'tkazma"]

SCENARIOS = {
    "Oddiy ish kuni ☀️": {
        "desc": "Normal bank ish kuni — oz fraud (5-8%)",
        "fraud_ratio": 0.06,
    },
    "Kechki fraud hujumi 🌙": {
        "desc": "Tungi/kechki hujum — ko'p fraud urinish (35-40%)",
        "fraud_ratio": 0.38,
    },
    "Karta klonlash ssenariysi 💳": {
        "desc": "O'g'irlangan karta ma'lumotlari bilan urinishlar (25%)",
        "fraud_ratio": 0.25,
    },
    "Balanslangan test ⚖️": {
        "desc": "50/50 test — model sifatini tekshirish uchun",
        "fraud_ratio": 0.50,
    },
}

def render_transaction_lifecycle(art: dict, df: pd.DataFrame) -> None:
    st.subheader("1. Bank tranzaksiya qayta ishlash simulyatsiyasi")
    st.markdown("""
Har bir to'lov **real bank tizimidagi kabi** 6 ta bosqichdan o'tadi va SafeNet har bir tranzaksiyani
**5 ta xavf signali** bo'yicha tahlil qilib, millisekundlar ichida qaror chiqaradi.
    """)

    c1, c2, c3, c4 = st.columns([1, 1.5, 1, 1])
    with c1: count    = st.slider("Tranzaksiyalar soni", 3, 25, 8, 1, key="lc_n")
    with c2: scenario = st.selectbox("Stsenariy", list(SCENARIOS.keys()), key="lc_sc")
    with c3: pause    = st.slider("Tezlik (sek)", 0.2, 3.0, 0.8, 0.1, key="lc_p")
    with c4: seed     = st.number_input("Seed", 1, 999999, 2026, key="lc_seed")

    sc = SCENARIOS[scenario]
    st.caption(f"📋 {sc['desc']}")

    if not st.button("▶ Simulyatsiyani boshlash", type="primary", key="lc_btn"):
        st.info("Tugmani bosing — har bir tranzaksiya uchun bank receipt, xavf tahlili va ML qarori ko'rsatiladi.")
        return

    fraud_ratio = sc["fraud_ratio"]
    fn = max(1, int(count * fraud_ratio))
    fraud_part  = df[df[TARGET]==1].sample(min(fn, len(df[df[TARGET]==1])), random_state=int(seed))
    normal_part = df[df[TARGET]==0].sample(min(count-fn, len(df[df[TARGET]==0])), random_state=int(seed)+1)
    stream = pd.concat([fraud_part, normal_part]).sample(frac=1, random_state=int(seed)).reset_index(drop=True)

    rng = np.random.default_rng(int(seed))
    base_time = pd.Timestamp.now()

    progress_bar = st.progress(0, "Tayyor...")
    tx_card_ph   = st.empty()
    log_ph       = st.empty()
    chart_ph     = st.empty()

    ledger: list[dict] = []
    probs_over_time: list[float] = []
    y_true_all: list[int] = []; y_pred_all: list[int] = []

    for idx, (_, row) in enumerate(stream.iterrows(), start=1):
        m_name, m_icon, m_cat, m_net = MERCHANTS[int(rng.integers(0, len(MERCHANTS)))]
        channel    = CHANNELS[int(rng.integers(0, len(CHANNELS)))]
        tx_id      = f"UZ{rng.integers(100000,999999)}"
        event_time = base_time + pd.Timedelta(seconds=idx * int(rng.integers(3,12)))

        result  = tranzaksiyani_tekshir(art, {c: row[c] for c in FEATURES})
        prob    = float(result["ehtimol"])
        decision= result["qaror"]
        actual  = int(row[TARGET])
        signals = compute_risk_signals(row)
        probs_over_time.append(prob)
        y_true_all.append(actual); y_pred_all.append(int(decision in ("REVIEW","BLOCK")))

        # ── Pipeline stages ────────────────────────────────────────────────
        stage_ms = [
            ("1️⃣ To'lov so'rovi qabul qilindi",       "Kanal va format tekshirildi",        int(rng.integers(12,35))),
            ("2️⃣ Karta/hisob validatsiyasi",           f"Karta tarmog'i: {m_net} ✓",          int(rng.integers(18,55))),
            ("3️⃣ Qurilma va lokatsiya tekshirildi",    f"{row['device']} | {row['location']}", int(rng.integers(22,70))),
            ("4️⃣ Xulq-atvor tahlili",                  "Foydalanuvchi profili solishtirildi",  int(rng.integers(30,80))),
            ("5️⃣ SafeNet ML modeli (DeepFraudNet)",    f"P(fraud) = {prob*100:.1f}%",          int(rng.integers(28,65))),
            ("6️⃣ Yakuniy qaror va yozuv",              decision_label(decision),               int(rng.integers(10,30))),
        ]
        total_ms = sum(s[2] for s in stage_ms)

        # ── Display transaction card ────────────────────────────────────────
        with tx_card_ph.container():
            # Header
            dec_colors = {"ALLOW":"🟢","REVIEW":"🟡","BLOCK":"🔴"}
            st.markdown(f"### {m_icon} {m_name} &nbsp;&nbsp; `{tx_id}` &nbsp;&nbsp; {dec_colors[decision]} **{decision_label(decision)}**")
            st.divider()

            col_receipt, col_signals, col_decision = st.columns([1.1, 1.4, 1.0])

            with col_receipt:
                st.markdown("**🧾 To'lov Kvitansiyasi**")
                st.markdown(f"""
| | |
|---|---|
| **Merchant** | {m_name} |
| **Kategoriya** | {m_cat} |
| **Summa** | **{format_uzs(row['amount'])}** |
| **Qurilma** | `{row['device']}` |
| **Lokatsiya** | `{row['location']}` |
| **Soat** | `{int(row['transaction_hour']):02d}:xx` |
| **Kanal** | {channel} |
| **Tarmoq** | {m_net} |
| **Vaqt** | {event_time.strftime('%H:%M:%S')} |
                """)

            with col_signals:
                st.markdown("**🔍 Xavf Signal Tahlili**")
                for sig_name, sig_score, sig_desc in signals:
                    st.markdown(risk_bar_md(sig_name, sig_score, sig_desc), unsafe_allow_html=True)
                st.markdown("---")
                overall = prob
                st.markdown(f"**Umumiy fraud ehtimoli: `{overall*100:.1f}%`**")

            with col_decision:
                st.markdown("**⚖️ ML Qarori**")
                gauge_color = {"LOW":"#22c55e","MEDIUM":"#f59e0b","HIGH":"#ef4444"}[result["xavf"]]
                fig = go.Figure(go.Indicator(
                    mode="gauge+number",
                    value=round(prob*100,1),
                    number={"suffix":"%","font":{"size":28}},
                    title={"text":"Fraud Score"},
                    gauge={"axis":{"range":[0,100]},
                           "bar":{"color":gauge_color},
                           "steps":[{"range":[0,50],"color":"#dcfce7"},
                                    {"range":[50,80],"color":"#fef9c3"},
                                    {"range":[80,100],"color":"#fee2e2"}],
                           "threshold":{"line":{"color":"black","width":2},"thickness":0.8,"value":art["chegara"]*100}},
                ))
                fig.update_layout(height=220, margin=dict(t=30,b=10,l=10,r=10))
                st.plotly_chart(fig, use_container_width=True, key=f"gauge_{idx}")

                if   decision == "BLOCK":  st.error(f"🚫 **BLOKLANDI**\nFraud ehtimoli juda yuqori")
                elif decision == "REVIEW": st.warning(f"⚠️ **TEKSHIRUV**\nOperatorga yuborildi")
                else:                      st.success(f"✅ **RUXSAT**\nOdatiy tranzaksiya")

                if actual == 1:
                    st.error("📍 Haqiqiy: Fraud")
                else:
                    st.success("📍 Haqiqiy: Normal")

            # Pipeline stages
            with st.expander("🔄 Qayta ishlash bosqichlari"):
                stage_df = pd.DataFrame(stage_ms, columns=["Bosqich", "Natija", "Vaqt (ms)"])
                st.dataframe(stage_df, use_container_width=True, hide_index=True)
                st.caption(f"⏱️ Jami qayta ishlash vaqti: **{total_ms} ms**")

        # ── Ledger ─────────────────────────────────────────────────────────
        final_status = {"ALLOW":"SETTLED","REVIEW":"MANUAL_REVIEW_QUEUE","BLOCK":"DECLINED"}[decision]
        ledger.append({
            "#":       idx,
            "Vaqt":    event_time.strftime("%H:%M:%S"),
            "Merchant": f"{m_icon} {m_name}",
            "Summa":   format_uzs(row["amount"]),
            "Qurilma": row["device"],
            "Lokatsiya": row["location"],
            "Score %": f"{prob*100:.1f}",
            "Qaror":   decision_label(decision),
            "Holat":   status_label(final_status),
            "Haqiqiy": "🔴 Fraud" if actual else "🟢 Normal",
            "Natija":  "✅" if actual==int(decision in ("REVIEW","BLOCK")) else "❌",
        })
        log_ph.dataframe(pd.DataFrame(ledger).tail(15), use_container_width=True, hide_index=True, height=300)

        # ── Running probability chart ──────────────────────────────────────
        if len(probs_over_time) >= 2:
            with chart_ph.container():
                prob_df = pd.DataFrame({
                    "Tranzaksiya": list(range(1, len(probs_over_time)+1)),
                    "P(fraud)": probs_over_time,
                    "Haqiqiy": ["Fraud" if y else "Normal" for y in y_true_all],
                })
                fig = go.Figure()
                fig.add_trace(go.Scatter(x=prob_df["Tranzaksiya"], y=prob_df["P(fraud)"],
                                         mode="lines+markers",
                                         marker=dict(color=["#ef4444" if p>0.5 else "#22c55e" for p in probs_over_time], size=10),
                                         line=dict(color="#94a3b8", width=1.5), name="Fraud ehtimoli"))
                fig.add_hline(y=art["chegara"], line_dash="dot", line_color="#f59e0b",
                              annotation_text=f"Review chegarasi ({art['chegara']:.2f})")
                fig.add_hline(y=max(art["chegara"]*1.6,0.70), line_dash="dot", line_color="#ef4444",
                              annotation_text="Block chegarasi")
                fig.update_layout(title="Oqimdagi fraud ehtimoli", xaxis_title="Tranzaksiya #",
                                  yaxis_title="P(fraud)", yaxis=dict(range=[0,1]), height=280,
                                  margin=dict(t=40,b=30))
                st.plotly_chart(fig, use_container_width=True, key=f"prob_chart_{idx}")

        progress_bar.progress(int(idx/len(stream)*100), f"{idx}/{len(stream)} tranzaksiya qayta ishlandi")
        time.sleep(float(pause))

    # ── Final summary ──────────────────────────────────────────────────────
    met = oqim_metrikalari(y_true_all, y_pred_all, probs_over_time)
    st.success(f"✅ Simulyatsiya yakunlandi — {len(stream)} tranzaksiya")
    k1,k2,k3,k4,k5 = st.columns(5)
    k1.metric("Aniqlik",  f"{met['accuracy']*100:.1f}%")
    k2.metric("Precision",f"{met['precision']*100:.1f}%")
    k3.metric("Recall",   f"{met['recall']*100:.1f}%")
    k4.metric("F1",       f"{met['f1']:.3f}")
    k5.metric("ROC-AUC",  f"{met['roc_auc']:.3f}" if met["roc_auc"] else "—")


def render_manual_score(art: dict) -> None:
    st.subheader("2. Bitta tranzaksiyani qo'lda tekshirish")
    st.caption("O'zingiz parametrlar kiriting — model qanday qaror chiqarishini va sababi nima ekanini ko'ring.")

    with st.expander("💡 Tez namunalar — bosib ko'ring"):
        c1,c2,c3 = st.columns(3)
        c1.markdown("**🟢 Xavfsiz misol**\n- iOS, Toshkent, 50k so'm, 14:00")
        c2.markdown("**🟡 O'rta xavf**\n- Chrome, Samarqand, 850k so'm, 23:15")
        c3.markdown("**🔴 Yuqori xavf**\n- Emulator, foreign_ip, 10M so'm, 02:45")

    with st.form("manual_score"):
        c1,c2,c3 = st.columns(3)
        with c1:
            amount   = st.number_input("Summa (so'm)",    1., 500_000_000., 1_500_000., step=50000.)
            device   = st.selectbox("Qurilma",  ["ios","android","windows","chrome","safari","linux","emulator","unknown"])
            location = st.selectbox("Joylashuv",["tashkent","samarkand","fergana","bukhara","almaty","istanbul","dubai","foreign_ip","unknown"])
            hour     = st.slider("Tranzaksiya soati", 0, 23, 14)
        with c2:
            freq       = st.number_input("Bugungi tranzaksiyalar soni",    1,  60, 3)
            last_tx    = st.number_input("Oxirgi tranzaksiyadan (daqiqa)", 0.5,1440., 120., step=5.)
            age        = st.number_input("Hisob yoshi (kun)",              1,  3650, 365)
            complaints = st.number_input("Shikoyatlar soni",               0,  20, 0)
        with c3:
            geo       = st.checkbox("Geo anomaliya")
            loc_bad   = st.checkbox("Joylashuv nomuvofiq")
            verified  = st.checkbox("Qabul qiluvchi tasdiqlangan", value=True)
            blacklist = st.checkbox("Qora ro'yxat")
            vpn       = st.checkbox("VPN / Proxy")
            mismatch  = st.checkbox("Kategoriya nomuvofiq")
            limit     = st.checkbox("Limit oshgan")
            high_val  = st.checkbox("Yuqori miqdorli tranzaksiya")
            past_fr   = st.checkbox("Oldingi fraud tarixi")
        submitted = st.form_submit_button("🔍 Tekshir", type="primary", use_container_width=True)

    if not submitted: return

    tx_input = {
        "amount": amount, "device": device, "location": location, "transaction_hour": hour,
        "Transaction Frequency": freq, "Time Since Last Transaction": last_tx,
        "Account Age": age, "Fraud Complaints Count": complaints,
        "Geo-Location Flags": int(geo), "Location-Inconsistent Transactions": int(loc_bad),
        "Recipient Verification Status": int(verified), "Recipient Blacklist Status": int(blacklist),
        "VPN or Proxy Usage": int(vpn), "Merchant Category Mismatch": int(mismatch),
        "User Daily Limit Exceeded": int(limit), "Recent High-Value Transaction Flags": int(high_val),
        "Past Fraudulent Behavior Flags": int(past_fr), "Normalized Transaction Amount": 0.0,
    }
    result = tranzaksiyani_tekshir(art, tx_input)
    prob   = result["ehtimol"]; decision = result["qaror"]

    c1,c2,c3 = st.columns(3)
    c1.metric("Fraud ehtimolligi", f"{prob*100:.1f}%")
    c2.metric("Xavf darajasi",     result["xavf"])
    c3.metric("Qaror",             decision_label(decision))

    col_gauge, col_signals = st.columns([1, 1.4])
    with col_gauge:
        gc = {"LOW":"#22c55e","MEDIUM":"#f59e0b","HIGH":"#ef4444"}[result["xavf"]]
        fig=go.Figure(go.Indicator(mode="gauge+number",value=round(prob*100,1),
            number={"suffix":"%"},title={"text":"Fraud Score"},
            gauge={"axis":{"range":[0,100]},"bar":{"color":gc},
                   "steps":[{"range":[0,50],"color":"#dcfce7"},{"range":[50,80],"color":"#fef9c3"},{"range":[80,100],"color":"#fee2e2"}],
                   "threshold":{"line":{"color":"black","width":2},"thickness":0.8,"value":art["chegara"]*100}}))
        fig.update_layout(height=280, margin=dict(t=30,b=10,l=10,r=10))
        st.plotly_chart(fig, use_container_width=True)

    with col_signals:
        st.markdown("**🔍 Xavf Signal Tahlili**")
        fake_row = pd.Series(tx_input)
        for sig_name, sig_score, sig_desc in compute_risk_signals(fake_row):
            st.markdown(risk_bar_md(sig_name, sig_score, sig_desc), unsafe_allow_html=True)

    if   decision=="BLOCK":  st.error(f"🚫 Tranzaksiya bloklandi. Fraud ehtimoli juda yuqori ({prob*100:.1f}%).")
    elif decision=="REVIEW": st.warning(f"⚠️ Operator tekshiruviga yuborildi. Ehtimol: {prob*100:.1f}%.")
    else:                    st.success(f"✅ Tranzaksiya ruxsat berildi. Xavf darajasi past ({prob*100:.1f}%).")


def render_live(art: dict, df: pd.DataFrame) -> None:
    st.header("Jonli tekshirish")
    st.markdown("""
Bu bo'lim ikkita tekshiruv rejimini taqdim etadi:
1. **Bank simulyatsiyasi** — tranzaksiyalar navbat bilan keladi, har birida receipt + xavf tahlili + ML qaror ko'rsatiladi.
2. **Qo'lda tekshirish** — o'z parametrlaringizni kiritib, model nima deyishini va nima sababdan deyishini ko'ring.
    """)
    render_transaction_lifecycle(art, df)
    st.divider()
    render_manual_score(art)

# ══════════════════════════════════════════════════════════════════════════════
# Sidebar & main
# ══════════════════════════════════════════════════════════════════════════════

def sidebar(art: dict) -> None:
    with st.sidebar:
        st.title("🛡️ SafeNet")
        st.caption("Anti-Fraud AI System")
        st.divider()
        m = art["korsatkichlar"]
        st.markdown("**Model holati:** 🟢 Faol")
        st.markdown(f"**Turi:** `{art.get('model_turi','DeepFraudNet')}`")
        st.metric("ROC-AUC", f"{m['test_roc_auc']:.4f}")
        st.metric("F1-Score", f"{m['test_f1']:.4f}")
        st.metric("Threshold", f"{m['chegara']:.2f}")
        st.divider()
        st.caption(f"O'rgatilgan: {art['orgatish_sanasi']}")
        st.caption(f"Train: {art['orgatish_soni']:,} | Test: {art['test_soni']:,}")
        st.caption(f"Fraud ulushi: {art['fraud_ulushi']*100:.1f}%")
        st.divider()
        st.caption("Arxitektura: 128→64→32→1")
        st.caption("Optimizer: Adam (lr=0.001)")


def main() -> None:
    df = load_data()
    artifact = modelni_yukla()
    if artifact is None:
        st.info("🔄 Birinchi ishga tushirish: DeepFraudNet o'rgatilmoqda...")
        bar = st.progress(0, "Boshlanmoqda...")
        artifact = tezkor_orgatish_va_saqlash(df, progress=bar)
        st.cache_resource.clear()
        st.rerun()
    sidebar(artifact)
    tab1,tab2,tab3,tab4,tab5 = st.tabs(["📊 Ko'rinish","🔎 Ko'zgu","🧠 O'rgatish","📈 Baholash","⚡ Jonli tekshirish"])
    with tab1: render_overview(df)
    with tab2: render_explorer(df)
    with tab3: render_training(df)
    with tab4: render_performance(artifact)
    with tab5: render_live(artifact, df)


if __name__ == "__main__":
    main()
