"""
SafeNet — Fraud Detection tizimi  v1.0
Streamlit interaktiv boshqaruv paneli:
  EDA (ma'lumotlarni ko'rish) → Model o'rgatish → Baholash → Jonli tekshirish
"""

from __future__ import annotations

import warnings
import time
from datetime import datetime
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st
from sklearn.compose import ColumnTransformer
from sklearn.ensemble import RandomForestClassifier
from sklearn.metrics import (
    average_precision_score,
    classification_report,
    confusion_matrix,
    f1_score,
    precision_recall_curve,
    roc_auc_score,
    roc_curve,
)
from sklearn.model_selection import StratifiedKFold, cross_validate, train_test_split
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder, StandardScaler

warnings.filterwarnings("ignore")

# ── Fayllar yo'li ─────────────────────────────────────────────────────────────
ILDIZ     = Path(__file__).parent
DATA_PATH = ILDIZ / "data" / "synthetic_transactions.csv"
MODEL_PATH = ILDIZ / "ml" / "fraud_model_v2.pkl"

# ── Xususiyatlar sxemasi ──────────────────────────────────────────────────────

# Kategoriyali ustunlar (OneHotEncoder orqali kodlanadi)
KATEGORIYALI = ["device", "location"]

# Raqamli uzluksiz ustunlar (StandardScaler orqali normallashtiriladi)
RAQAMLI = [
    "amount",
    "transaction_hour",
    "Transaction Frequency",
    "Time Since Last Transaction",
    "Account Age",
    "Normalized Transaction Amount",
    "Fraud Complaints Count",
]

# Ikkilik (0/1) xususiyatlar — StandardScaler ularga ham qo'llanadi
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

# Model uchun barcha kirish xususiyatlari
XUSUSIYATLAR = RAQAMLI + BINARY + KATEGORIYALI

# Nishon ustun
NISHON = "isFraud"

# ── Sahifa sozlamalari ────────────────────────────────────────────────────────
st.set_page_config(
    page_title="SafeNet – Fraud Detection",
    page_icon="🛡️",
    layout="wide",
    initial_sidebar_state="expanded",
)

# Minimal CSS uslublari
st.markdown(
    """
<style>
[data-testid="stMetricValue"] { font-size: 1.6rem; font-weight: 700; }
.block-container { padding-top: 1.5rem; }
</style>
""",
    unsafe_allow_html=True,
)


# ── Ma'lumotlarni yuklash ─────────────────────────────────────────────────────
@st.cache_data(show_spinner="Ma'lumotlar yuklanmoqda…")
def malumotlarni_yukla() -> pd.DataFrame:
    """CSV fayldan tranzaksiyalarni yuklaydi va kesh saqlaydi."""
    return pd.read_csv(DATA_PATH)


# ── Model pipeline ────────────────────────────────────────────────────────────
def _pipeline_qur(daraxt_soni: int, max_chuqurlik: int, min_barglar: int) -> Pipeline:
    """
    Sklearn Pipeline: preprocessor + RandomForestClassifier.
    class_weight='balanced' — noqovun sinf taqsimotini (5% fraud) boshqaradi.
    """
    oldindan_ishlash = ColumnTransformer(
        transformers=[
            # Raqamli va binary ustunlarni standartlashtirish
            ("raqamli", StandardScaler(), RAQAMLI + BINARY),
            # Kategoriyali ustunlarni bir-issiq kodlash
            ("kategoriyali", OneHotEncoder(handle_unknown="ignore", sparse_output=False), KATEGORIYALI),
        ]
    )
    klassifikator = RandomForestClassifier(
        n_estimators=daraxt_soni,
        max_depth=max_chuqurlik,
        min_samples_leaf=min_barglar,
        class_weight="balanced",   # Noqovun sinflarga moslashadi
        n_jobs=-1,                 # Barcha CPU yadrolarini ishlatadi
        random_state=42,
    )
    return Pipeline([("oldindan_ishlash", oldindan_ishlash), ("klassifikator", klassifikator)])


def _optimal_chegara(y_haqiqiy: np.ndarray, y_ehtimol: np.ndarray) -> float:
    """
    Eng yaxshi F1 ball beradigan chegara qiymatini izlaydi.
    Standart 0.5 o'rniga moslashtirilgan chegara ishlatiladi.
    """
    eng_yaxshi_f1, eng_yaxshi_t = 0.0, 0.5
    for t in np.arange(0.05, 0.95, 0.01):
        f1 = f1_score(y_haqiqiy, (y_ehtimol >= t).astype(int), zero_division=0)
        if f1 > eng_yaxshi_f1:
            eng_yaxshi_f1, eng_yaxshi_t = f1, float(t)
    return eng_yaxshi_t


def orgatish_va_saqlash(
    df: pd.DataFrame,
    daraxt_soni: int = 200,
    max_chuqurlik: int = 15,
    min_barglar: int = 2,
    progress=None,
) -> dict:
    """
    To'liq ML pipeline:
      1. Ma'lumotlarni bo'lish (80/20, stratifikatsiya bilan)
      2. 5-fold kross-validatsiya
      3. Final modelni o'rgatish
      4. Optimal chegara aniqlash
      5. Ko'rsatkichlarni hisoblash va modelni saqlash
    """
    X = df[XUSUSIYATLAR].copy()
    y = df[NISHON].copy()

    # Stratifikatsiyali bo'lish — fraud nisbatini ikkala to'plamda saqlaydi
    X_o, X_t, y_o, y_t = train_test_split(X, y, test_size=0.2, stratify=y, random_state=42)

    pipe = _pipeline_qur(daraxt_soni, max_chuqurlik, min_barglar)

    if progress:
        progress.progress(15, "5-fold kross-validatsiya…")

    # Kross-validatsiya — modelning umumlashish qobiliyatini o'lchaydi
    kv = StratifiedKFold(n_splits=5, shuffle=True, random_state=42)
    kv_natija = cross_validate(
        pipe, X_o, y_o,
        cv=kv,
        scoring=["roc_auc", "f1", "precision", "recall"],
        n_jobs=-1,
    )

    if progress:
        progress.progress(55, "Final model o'rgatilmoqda…")

    pipe.fit(X_o, y_o)

    if progress:
        progress.progress(80, "Ko'rsatkichlar hisoblanmoqda…")

    # Test to'plamida bashorat ehtimolliklari
    y_ehtimol = pipe.predict_proba(X_t)[:, 1]

    # F1 ni maksimallashtiradigan chegara
    chegara = _optimal_chegara(y_t.values, y_ehtimol)
    y_bashorat = (y_ehtimol >= chegara).astype(int)

    # Xususiyatlar muhimligini chiqarish
    ohe_nomlar = list(
        pipe.named_steps["oldindan_ishlash"]
        .named_transformers_["kategoriyali"]
        .get_feature_names_out(KATEGORIYALI)
    )
    barcha_nomlar = RAQAMLI + BINARY + ohe_nomlar
    muhimlik_df = pd.DataFrame(
        {"xususiyat": barcha_nomlar,
         "muhimlik": pipe.named_steps["klassifikator"].feature_importances_}
    ).sort_values("muhimlik", ascending=False)

    # ROC va PR egri chiziqlari
    fpr, tpr, _ = roc_curve(y_t, y_ehtimol)
    aniqlik, qamrov, _ = precision_recall_curve(y_t, y_ehtimol)
    chalkash_matritsa = confusion_matrix(y_t, y_bashorat)

    artifact = {
        "pipeline":        pipe,
        "xususiyatlar":    XUSUSIYATLAR,
        "chegara":         chegara,
        "miqdor_stat":     {"min": float(df["amount"].min()), "max": float(df["amount"].max())},
        "xususiyat_muhimlik": muhimlik_df,
        "orgatish_sanasi": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "orgatish_soni":   len(X_o),
        "test_soni":       len(X_t),
        "fraud_ulushi":    float(y.mean()),
        "korsatkichlar": {
            # Kross-validatsiya (o'rgatish to'plami)
            "kv_roc_auc":     float(kv_natija["test_roc_auc"].mean()),
            "kv_roc_auc_std": float(kv_natija["test_roc_auc"].std()),
            "kv_f1":          float(kv_natija["test_f1"].mean()),
            "kv_aniqlik":     float(kv_natija["test_precision"].mean()),
            "kv_qamrov":      float(kv_natija["test_recall"].mean()),
            # Test to'plami ko'rsatkichlari
            "test_roc_auc":   float(roc_auc_score(y_t, y_ehtimol)),
            "test_ort_aniqlik": float(average_precision_score(y_t, y_ehtimol)),
            "test_f1":        float(f1_score(y_t, y_bashorat, zero_division=0)),
            "chegara":        chegara,
            # Vizualizatsiya uchun ma'lumotlar
            "chalkash_matritsa": chalkash_matritsa,
            "roc_egri":       (fpr, tpr),
            "pr_egri":        (aniqlik, qamrov),
            "hisobot":        classification_report(y_t, y_bashorat, output_dict=True, zero_division=0),
        },
    }

    MODEL_PATH.parent.mkdir(parents=True, exist_ok=True)
    joblib.dump(artifact, MODEL_PATH)

    if progress:
        progress.progress(100, "Tayyor!")

    return artifact


def tezkor_orgatish_va_saqlash(df: pd.DataFrame, progress=None) -> dict:
    """
    Streamlit Cloud cold-start uchun yengil model yaratadi.
    To'liq 5-fold training UI ichidagi O'rgatish tabida qoladi.
    """
    if len(df) > 12_000:
        train_df = df.groupby(NISHON, group_keys=False).sample(
            frac=12_000 / len(df),
            random_state=42,
        )
    else:
        train_df = df

    if progress:
        progress.progress(20, "Tezkor model uchun sample tayyorlandi...")

    X = train_df[XUSUSIYATLAR].copy()
    y = train_df[NISHON].copy()
    X_o, X_t, y_o, y_t = train_test_split(
        X,
        y,
        test_size=0.25,
        stratify=y,
        random_state=42,
    )

    pipe = _pipeline_qur(daraxt_soni=60, max_chuqurlik=12, min_barglar=5)

    if progress:
        progress.progress(45, "Tezkor RandomForest o'rgatilmoqda...")

    pipe.fit(X_o, y_o)

    if progress:
        progress.progress(75, "Metrikalar hisoblanmoqda...")

    y_ehtimol = pipe.predict_proba(X_t)[:, 1]
    chegara = _optimal_chegara(y_t.values, y_ehtimol)
    y_bashorat = (y_ehtimol >= chegara).astype(int)

    ohe_nomlar = list(
        pipe.named_steps["oldindan_ishlash"]
        .named_transformers_["kategoriyali"]
        .get_feature_names_out(KATEGORIYALI)
    )
    barcha_nomlar = RAQAMLI + BINARY + ohe_nomlar
    muhimlik_df = pd.DataFrame(
        {
            "xususiyat": barcha_nomlar,
            "muhimlik": pipe.named_steps["klassifikator"].feature_importances_,
        }
    ).sort_values("muhimlik", ascending=False)

    fpr, tpr, _ = roc_curve(y_t, y_ehtimol)
    aniqlik, qamrov, _ = precision_recall_curve(y_t, y_ehtimol)
    chalkash_matritsa = confusion_matrix(y_t, y_bashorat)
    roc_auc = float(roc_auc_score(y_t, y_ehtimol))
    f1 = float(f1_score(y_t, y_bashorat, zero_division=0))

    artifact = {
        "pipeline": pipe,
        "xususiyatlar": XUSUSIYATLAR,
        "chegara": chegara,
        "miqdor_stat": {"min": float(df["amount"].min()), "max": float(df["amount"].max())},
        "xususiyat_muhimlik": muhimlik_df,
        "orgatish_sanasi": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "orgatish_soni": len(X_o),
        "test_soni": len(X_t),
        "fraud_ulushi": float(df[NISHON].mean()),
        "korsatkichlar": {
            "kv_roc_auc": roc_auc,
            "kv_roc_auc_std": 0.0,
            "kv_f1": f1,
            "kv_aniqlik": 0.0,
            "kv_qamrov": 0.0,
            "test_roc_auc": roc_auc,
            "test_ort_aniqlik": float(average_precision_score(y_t, y_ehtimol)),
            "test_f1": f1,
            "chegara": chegara,
            "chalkash_matritsa": chalkash_matritsa,
            "roc_egri": (fpr, tpr),
            "pr_egri": (aniqlik, qamrov),
            "hisobot": classification_report(y_t, y_bashorat, output_dict=True, zero_division=0),
        },
    }

    MODEL_PATH.parent.mkdir(parents=True, exist_ok=True)
    joblib.dump(artifact, MODEL_PATH)

    if progress:
        progress.progress(100, "Tezkor model tayyor!")

    return artifact


# ── Model yuklash ─────────────────────────────────────────────────────────────
def artifactni_moslashtir(artifact: dict | None) -> dict | None:
    """
    Eski model artifactlari inglizcha kalitlar bilan saqlangan bo'lishi mumkin.
    Bu funksiya ularni joriy o'zbekcha app sxemasiga moslashtiradi.
    """
    if not isinstance(artifact, dict):
        return None

    if "korsatkichlar" in artifact:
        return artifact

    eski_metrikalar = artifact.get("metrics")
    if not isinstance(eski_metrikalar, dict):
        return None

    fi = artifact.get("feature_importance")
    if isinstance(fi, pd.DataFrame):
        fi = fi.rename(columns={"feature": "xususiyat", "importance": "muhimlik"})

    chegara = artifact.get("threshold", eski_metrikalar.get("threshold", 0.5))
    return {
        "pipeline": artifact.get("pipeline"),
        "xususiyatlar": artifact.get("features", XUSUSIYATLAR),
        "chegara": chegara,
        "miqdor_stat": artifact.get("amount_stats", {"min": 0.0, "max": 1.0}),
        "xususiyat_muhimlik": fi if isinstance(fi, pd.DataFrame) else pd.DataFrame(),
        "orgatish_sanasi": artifact.get("train_date", "noma'lum"),
        "orgatish_soni": artifact.get("n_train", 0),
        "test_soni": artifact.get("n_test", 0),
        "fraud_ulushi": artifact.get("fraud_rate", 0.0),
        "korsatkichlar": {
            "kv_roc_auc": eski_metrikalar.get("cv_roc_auc", 0.0),
            "kv_roc_auc_std": eski_metrikalar.get("cv_roc_auc_std", 0.0),
            "kv_f1": eski_metrikalar.get("cv_f1", 0.0),
            "kv_aniqlik": eski_metrikalar.get("cv_precision", 0.0),
            "kv_qamrov": eski_metrikalar.get("cv_recall", 0.0),
            "test_roc_auc": eski_metrikalar.get("test_roc_auc", 0.0),
            "test_ort_aniqlik": eski_metrikalar.get("test_avg_precision", 0.0),
            "test_f1": eski_metrikalar.get("test_f1", 0.0),
            "chegara": chegara,
            "chalkash_matritsa": eski_metrikalar.get("confusion_matrix", np.zeros((2, 2), dtype=int)),
            "roc_egri": eski_metrikalar.get("roc_curve", (np.array([0, 1]), np.array([0, 1]))),
            "pr_egri": eski_metrikalar.get("pr_curve", (np.array([1, 0]), np.array([0, 1]))),
            "hisobot": eski_metrikalar.get("report", {}),
        },
    }


@st.cache_resource(show_spinner=False)
def modelni_yukla() -> dict | None:
    """Saqlangan modelni keshdan yuklaydi."""
    if MODEL_PATH.exists():
        try:
            return artifactni_moslashtir(joblib.load(MODEL_PATH))
        except Exception:
            # Cloud muhitida pickle/sklearn/numpy versiyalari mos kelmasa,
            # app yiqilmasin: main() datasetdan modelni qayta o'rgatadi.
            return None
    return None


# ── Bashorat ──────────────────────────────────────────────────────────────────
def tranzaksiyani_tekshir(artifact: dict, tx: dict) -> dict:
    """
    Bitta tranzaksiya uchun fraud ehtimolligini hisoblaydi.
    Qaror qoidasi: chegara * 1.6 dan yuqori BLOCK, chegara usti REVIEW.
    """
    # Normallashtirilgan miqdorni hisoblash (o'rgatish statistikasidan)
    amt = float(tx.get("amount", 100))
    a_min = artifact["miqdor_stat"]["min"]
    a_max = artifact["miqdor_stat"]["max"]
    tx["Normalized Transaction Amount"] = float(
        np.clip((amt - a_min) / max(a_max - a_min, 1e-9), 0.0, 1.0)
    )

    # Kiritilmagan xususiyatlarni 0 bilan to'ldirish
    qator = {f: 0 for f in XUSUSIYATLAR}
    qator.update(tx)

    ehtimol = float(
        artifact["pipeline"].predict_proba(pd.DataFrame([qator])[XUSUSIYATLAR])[0][1]
    )

    chegara = artifact["chegara"]
    # Blok chegarasi juda past bo'lib qolmasligi uchun 70% dan boshlaymiz.
    blok_chegara = max(chegara * 1.6, 0.70)
    if ehtimol >= blok_chegara:
        xavf, qaror = "HIGH", "BLOCK"
    elif ehtimol >= chegara:
        xavf, qaror = "MEDIUM", "REVIEW"
    else:
        xavf, qaror = "LOW", "ALLOW"

    return {"ehtimol": ehtimol, "xavf": xavf, "qaror": qaror}


def simulyatsiya_namunasini_tanla(
    df: pd.DataFrame,
    soni: int,
    ssenariy: str,
    seed: int,
) -> pd.DataFrame:
    """Jonli oqim simulyatsiyasi uchun datasetdan tranzaksiyalar tanlaydi."""
    rng = np.random.default_rng(seed)

    def sinfdan_ol(label: int, n: int) -> pd.DataFrame:
        qism = df[df[NISHON] == label]
        return qism.sample(
            n=n,
            replace=len(qism) < n,
            random_state=int(rng.integers(1, 1_000_000)),
        )

    if ssenariy == "Balanslangan test (50% fraud)":
        fraud_soni = soni // 2
    elif ssenariy == "Hujum ssenariysi (35% fraud)":
        fraud_soni = max(1, int(soni * 0.35))
    else:
        return df.sample(
            n=soni,
            replace=len(df) < soni,
            random_state=seed,
        ).reset_index(drop=True)

    normal_soni = soni - fraud_soni
    namuna = pd.concat([sinfdan_ol(1, fraud_soni), sinfdan_ol(0, normal_soni)])
    return namuna.sample(frac=1, random_state=seed).reset_index(drop=True)


def oqim_metrikalari(y_true: list[int], y_pred: list[int], y_prob: list[float]) -> dict:
    """Kelgan tranzaksiyalar bo'yicha real vaqt metrikalarini hisoblaydi."""
    if not y_true:
        return {"accuracy": 0.0, "precision": 0.0, "recall": 0.0, "f1": 0.0, "roc_auc": None}

    yt = np.array(y_true)
    yp = np.array(y_pred)
    prob = np.array(y_prob)

    tp = int(((yt == 1) & (yp == 1)).sum())
    fp = int(((yt == 0) & (yp == 1)).sum())
    fn = int(((yt == 1) & (yp == 0)).sum())
    accuracy = float((yt == yp).mean())
    precision = tp / max(tp + fp, 1)
    recall = tp / max(tp + fn, 1)

    return {
        "accuracy": accuracy,
        "precision": precision,
        "recall": recall,
        "f1": float(f1_score(yt, yp, zero_division=0)),
        "roc_auc": float(roc_auc_score(yt, prob)) if len(np.unique(yt)) == 2 else None,
    }


def render_oqim_simulyatsiyasi(art: dict, df: pd.DataFrame) -> None:
    """Tranzaksiyalar ketma-ket kelayotgandek modelni jonli sinaydi."""
    st.subheader("Real vaqt tranzaksiya oqimi")
    st.caption(
        "Datasetdan tranzaksiyalar navbat bilan olinadi, model har birini baholaydi "
        "va natija pastdagi jadvalga qatorma-qator tushadi."
    )

    col1, col2, col3, col4 = st.columns([1, 1.4, 1, 1])
    with col1:
        soni = st.slider("Tranzaksiyalar soni", 10, 300, 50, 10)
    with col2:
        ssenariy = st.selectbox(
            "Simulyatsiya turi",
            [
                "Real oqim (dataset fraud ulushi)",
                "Balanslangan test (50% fraud)",
                "Hujum ssenariysi (35% fraud)",
            ],
        )
    with col3:
        pauza = st.slider("Pauza (sekund)", 0.0, 1.0, 0.10, 0.05)
    with col4:
        seed = st.number_input("Seed", 1, 999_999, 42)

    boshlash = st.button("Oqimni boshlash", type="secondary", use_container_width=True)
    metrika_joyi = st.empty()
    jadval_joyi = st.empty()
    matrix_joyi = st.empty()

    if not boshlash:
        st.info("Simulyatsiyani boshlash uchun **Oqimni boshlash** tugmasini bosing.")
        return

    oqim = simulyatsiya_namunasini_tanla(df, soni, ssenariy, int(seed))
    progress = st.progress(0, "Oqim boshlandi...")
    qatorlar: list[dict[str, object]] = []
    y_true: list[int] = []
    y_pred: list[int] = []
    y_prob: list[float] = []

    for tartib, (_, row) in enumerate(oqim.iterrows(), start=1):
        tx = {ustun: row[ustun] for ustun in XUSUSIYATLAR}
        natija = tranzaksiyani_tekshir(art, tx)
        haqiqiy = int(row[NISHON])
        bashorat = int(natija["qaror"] in ("REVIEW", "BLOCK"))
        ehtimol = float(natija["ehtimol"])

        y_true.append(haqiqiy)
        y_pred.append(bashorat)
        y_prob.append(ehtimol)

        qatorlar.append({
            "#": tartib,
            "user_id": row["user_id"],
            "amount": round(float(row["amount"]), 2),
            "device": row["device"],
            "location": row["location"],
            "hour": int(row["transaction_hour"]),
            "score_%": round(ehtimol * 100, 1),
            "risk": natija["xavf"],
            "decision": natija["qaror"],
            "haqiqiy": "Fraud" if haqiqiy else "Normal",
            "bashorat": "Fraud" if bashorat else "Normal",
            "natija": "To'g'ri" if haqiqiy == bashorat else "Xato",
        })

        m = oqim_metrikalari(y_true, y_pred, y_prob)
        with metrika_joyi.container():
            c1, c2, c3, c4, c5 = st.columns(5)
            c1.metric("Ko'rilgan", f"{tartib}/{soni}")
            c2.metric("Accuracy", f"{m['accuracy'] * 100:.1f}%")
            c3.metric("Precision", f"{m['precision'] * 100:.1f}%")
            c4.metric("Recall", f"{m['recall'] * 100:.1f}%")
            c5.metric("F1", f"{m['f1']:.3f}")

        jadval_joyi.dataframe(
            pd.DataFrame(qatorlar).tail(120),
            use_container_width=True,
            hide_index=True,
            height=420,
        )
        progress.progress(int(tartib / soni * 100), f"{tartib}/{soni} tranzaksiya tekshirildi")

        if pauza > 0:
            time.sleep(float(pauza))

    cm = confusion_matrix(y_true, y_pred, labels=[0, 1])
    final_m = oqim_metrikalari(y_true, y_pred, y_prob)
    with matrix_joyi.container():
        col_a, col_b = st.columns([1, 1])
        with col_a:
            fig = px.imshow(
                cm,
                text_auto=True,
                color_continuous_scale="Blues",
                x=["Bashorat: Normal", "Bashorat: Fraud"],
                y=["Haqiqiy: Normal", "Haqiqiy: Fraud"],
                title="Simulyatsiya chalkash matritsasi",
            )
            fig.update_coloraxes(showscale=False)
            st.plotly_chart(fig, use_container_width=True)
        with col_b:
            roc_text = "N/A" if final_m["roc_auc"] is None else f"{final_m['roc_auc']:.4f}"
            st.markdown(
                f"""
**Yakuniy natija**

- Accuracy: `{final_m['accuracy']:.4f}`
- Precision: `{final_m['precision']:.4f}`
- Recall: `{final_m['recall']:.4f}`
- F1-score: `{final_m['f1']:.4f}`
- ROC-AUC: `{roc_text}`
                """
            )

    progress.progress(100, "Simulyatsiya yakunlandi")


# ═══════════════════════════════════════════════════════════════════════════════
# TAB 1 — MA'LUMOTLAR KO'RINISHI
# ═══════════════════════════════════════════════════════════════════════════════
def korinish_tab(df: pd.DataFrame) -> None:
    st.header("Ma'lumotlar Ko'rinishi")

    jami    = len(df)
    fraud   = int(df[NISHON].sum())
    normal  = jami - fraud

    # Asosiy metrikalar
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Jami Tranzaksiyalar", f"{jami:,}")
    c2.metric("Fraud holatlari", f"{fraud:,}",
              delta=f"{fraud/jami*100:.1f}% ulush", delta_color="inverse")
    c3.metric("Normal tranzaksiyalar", f"{normal:,}")
    c4.metric("Ishlatilayotgan xususiyatlar", str(len(XUSUSIYATLAR)))

    st.divider()

    col1, col2 = st.columns(2)
    with col1:
        # Fraud/Normal nisbati — donut grafigi
        fig = px.pie(
            values=[fraud, normal],
            names=["Fraud", "Normal"],
            color_discrete_sequence=["#ef4444", "#22c55e"],
            title="Tranzaksiya yorliqlarining taqsimoti",
            hole=0.42,
        )
        fig.update_traces(textposition="outside", textinfo="percent+label")
        st.plotly_chart(fig, use_container_width=True)

    with col2:
        # Qurilma bo'yicha fraud darajasi
        qurilma_fraud = (
            df.groupby("device")[NISHON]
            .agg(fraud="sum", jami="count")
            .assign(daraja=lambda x: x["fraud"] / x["jami"] * 100)
            .reset_index()
            .sort_values("daraja", ascending=False)
        )
        fig = px.bar(
            qurilma_fraud, x="device", y="daraja", color="daraja",
            color_continuous_scale="RdYlGn_r",
            title="Qurilma bo'yicha fraud darajasi (%)",
            labels={"daraja": "Fraud darajasi (%)", "device": "Qurilma"},
        )
        fig.update_coloraxes(showscale=False)
        st.plotly_chart(fig, use_container_width=True)

    col3, col4 = st.columns(2)
    with col3:
        # Soat bo'yicha fraud darajasi
        soat_fraud = df.groupby("transaction_hour")[NISHON].mean() * 100
        fig = go.Figure(go.Scatter(
            x=soat_fraud.index, y=soat_fraud.values,
            mode="lines+markers",
            line=dict(color="#3b82f6", width=2.5),
            fill="tozeroy", fillcolor="rgba(59,130,246,0.12)",
        ))
        fig.add_hline(
            y=soat_fraud.mean(), line_dash="dash", line_color="orange",
            annotation_text=f"O'rtacha {soat_fraud.mean():.1f}%",
        )
        fig.update_layout(
            title="Soat bo'yicha fraud darajasi",
            xaxis_title="Soat (0–23)", yaxis_title="Fraud darajasi (%)",
        )
        st.plotly_chart(fig, use_container_width=True)

    with col4:
        # Tranzaksiya miqdori taqsimoti (namuna)
        namuna = df.sample(min(5000, jami), random_state=1)
        fig = px.histogram(
            namuna, x="amount",
            color=namuna[NISHON].map({0: "Normal", 1: "Fraud"}),
            nbins=60, barmode="overlay", opacity=0.72,
            color_discrete_map={"Normal": "#22c55e", "Fraud": "#ef4444"},
            title="Tranzaksiya miqdori taqsimoti",
            labels={"amount": "Miqdor ($)", "color": "Yorliq"},
        )
        st.plotly_chart(fig, use_container_width=True)

    # Joylashuv bo'yicha fraud darajasi — gorizontal bar
    st.subheader("Joylashuv bo'yicha fraud darajasi")
    joy_fraud = (
        df.groupby("location")[NISHON]
        .agg(fraud="sum", jami="count")
        .assign(daraja=lambda x: x["fraud"] / x["jami"] * 100)
        .reset_index()
        .sort_values("daraja")
    )
    fig = px.bar(
        joy_fraud, x="daraja", y="location", orientation="h", color="daraja",
        color_continuous_scale="RdYlGn_r",
        title="Joylashuv bo'yicha fraud darajasi (%)",
        labels={"daraja": "Fraud darajasi (%)", "location": "Joylashuv"},
    )
    fig.update_coloraxes(showscale=False)
    st.plotly_chart(fig, use_container_width=True)


# ═══════════════════════════════════════════════════════════════════════════════
# TAB 2 — MA'LUMOTLAR KO'ZGUSI
# ═══════════════════════════════════════════════════════════════════════════════
def kozgu_tab(df: pd.DataFrame) -> None:
    st.header("Ma'lumotlar Ko'zgusi")

    col1, col2, col3 = st.columns(3)
    with col1:
        qurilmalar = st.multiselect(
            "Qurilma", sorted(df["device"].unique()),
            default=list(df["device"].unique()),
        )
    with col2:
        joylar = st.multiselect(
            "Joylashuv", sorted(df["location"].unique()),
            default=list(df["location"].unique()),
        )
    with col3:
        yorliq = st.radio("Yorliq", ["Barchasi", "Normal (0)", "Fraud (1)"], horizontal=True)

    # Filtrlash
    niqob = df["device"].isin(qurilmalar) & df["location"].isin(joylar)
    if yorliq == "Fraud (1)":
        niqob &= df[NISHON] == 1
    elif yorliq == "Normal (0)":
        niqob &= df[NISHON] == 0
    fdf = df[niqob]

    st.info(f"Filtrlangan: {len(fdf):,} / {len(df):,} tranzaksiya")

    if fdf.empty:
        st.warning("Joriy filtr bo'yicha ma'lumot topilmadi.")
        return

    col1, col2 = st.columns(2)
    with col1:
        # Miqdor vs soat scatter grafigi
        namuna = fdf.sample(min(3000, len(fdf)), random_state=42)
        fig = px.scatter(
            namuna, x="amount", y="transaction_hour",
            color=namuna[NISHON].map({0: "Normal", 1: "Fraud"}),
            color_discrete_map={"Normal": "#22c55e", "Fraud": "#ef4444"},
            opacity=0.50,
            title="Miqdor va Soat (namuna)",
            labels={"amount": "Miqdor ($)", "transaction_hour": "Soat"},
        )
        st.plotly_chart(fig, use_container_width=True)

    with col2:
        # Korrelyatsiya matritsasi
        korr_ustunlar = [
            "amount", "Transaction Frequency", "Time Since Last Transaction",
            "Account Age", "Fraud Complaints Count", NISHON,
        ]
        korr = fdf[korr_ustunlar].corr().round(3)
        fig = px.imshow(
            korr, text_auto=".2f",
            color_continuous_scale="RdBu_r", zmin=-1, zmax=1,
            title="Xususiyatlar Korrelyatsiya Matritsasi",
        )
        st.plotly_chart(fig, use_container_width=True)

    # Binary xavf belgilari faolligi
    st.subheader("Binary xavf belgilari faollik darajasi (%)")
    bayroq_darajalari = {ustun: fdf[ustun].mean() * 100 for ustun in BINARY}
    fig = px.bar(
        x=list(bayroq_darajalari.keys()),
        y=list(bayroq_darajalari.values()),
        labels={"x": "Xavf belgisi", "y": "Faol (%)"},
        color=list(bayroq_darajalari.values()),
        color_continuous_scale="RdYlGn_r",
        title="Filtrlangan ma'lumotlarda har bir xavf belgisining ulushi",
    )
    fig.update_coloraxes(showscale=False)
    fig.update_layout(xaxis_tickangle=-30)
    st.plotly_chart(fig, use_container_width=True)

    st.subheader("Namuna (birinchi 200 qator)")
    st.dataframe(fdf.head(200), use_container_width=True)


# ═══════════════════════════════════════════════════════════════════════════════
# TAB 3 — MODEL O'RGATISH
# ═══════════════════════════════════════════════════════════════════════════════
def orgatish_tab() -> None:
    st.header("Model O'rgatish")

    st.info(
        "**Algoritm:** Random Forest Classifier\n\n"
        "- `class_weight='balanced'` — 5% fraud ulushini avtomatik kompensatsiya qiladi\n"
        "- 5-fold stratifikatsiyali kross-validatsiya\n"
        "- Avtomatik chegara optimizatsiyasi (F1-ball maksimizatsiya)"
    )

    with st.expander("Giperparametrlar", expanded=True):
        col1, col2, col3 = st.columns(3)
        with col1:
            daraxt_soni = st.slider(
                "Daraxtlar soni", 50, 500, 200, 50,
                help="Ko'p daraxt → yaxshiroq lekin sekinroq",
            )
        with col2:
            max_chuqurlik = st.slider(
                "Maksimal chuqurlik", 5, 40, 15, 1,
                help="Chuqurlik oshsa ortiqcha o'rganish xavfi",
            )
        with col3:
            min_barglar = st.slider(
                "Minimal barglar soni", 1, 20, 2, 1,
                help="Kattaroq qiymat → tekisroq bashorat",
            )

    # Mavjud modelni ko'rsatish
    mavjud = modelni_yukla()
    if mavjud:
        m = mavjud["korsatkichlar"]
        st.success(
            f"Faol model — o'rgatilgan {mavjud['orgatish_sanasi']} | "
            f"ROC-AUC {m['test_roc_auc']:.4f} | "
            f"F1 {m['test_f1']:.4f} | "
            f"Chegara {m['chegara']:.2f}"
        )

    if st.button("🚀  Model o'rgat / qayta o'rgat", type="primary"):
        df = malumotlarni_yukla()
        jadval = st.progress(0, "Boshlanmoqda…")
        with st.spinner("O'rgatilmoqda…"):
            art = orgatish_va_saqlash(df, daraxt_soni, max_chuqurlik, min_barglar, jadval)
        # Keshni tozalash — yangi model darhol ko'rinsin
        st.cache_resource.clear()
        st.success("O'rgatish muvaffaqiyatli yakunlandi!")

        m = art["korsatkichlar"]
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Test ROC-AUC",  f"{m['test_roc_auc']:.4f}")
        c2.metric("Test F1",       f"{m['test_f1']:.4f}")
        c3.metric("KV ROC-AUC",    f"{m['kv_roc_auc']:.4f} ± {m['kv_roc_auc_std']:.4f}")
        c4.metric("Chegara",       f"{m['chegara']:.2f}")

        st.subheader("5-Fold Kross-Validatsiya natijalari")
        kv_df = pd.DataFrame({
            "Ko'rsatkich": ["ROC-AUC", "F1-ball", "Aniqlik (Precision)", "Qamrov (Recall)"],
            "KV O'rtacha": [
                f"{m['kv_roc_auc']:.4f}",
                f"{m['kv_f1']:.4f}",
                f"{m['kv_aniqlik']:.4f}",
                f"{m['kv_qamrov']:.4f}",
            ],
        })
        st.dataframe(kv_df, use_container_width=False)


# ═══════════════════════════════════════════════════════════════════════════════
# TAB 4 — MODEL BAHOLASH
# ═══════════════════════════════════════════════════════════════════════════════
def baholash_tab() -> None:
    st.header("Model Baholash")

    art = modelni_yukla()
    if art is None:
        st.warning("Model topilmadi. Avval **Model O'rgatish** tabiga o'ting.")
        return

    m = art["korsatkichlar"]

    # Asosiy ko'rsatkichlar
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Test ROC-AUC",        f"{m['test_roc_auc']:.4f}")
    c2.metric("O'rt. Aniqlik (PR)",  f"{m['test_ort_aniqlik']:.4f}")
    c3.metric("Test F1-ball",        f"{m['test_f1']:.4f}")
    c4.metric("Qaror Chegarasi",     f"{m['chegara']:.2f}")

    st.divider()

    col1, col2 = st.columns(2)
    with col1:
        # Chalkash matritsa (confusion matrix)
        cm = m["chalkash_matritsa"]
        fig = px.imshow(
            cm, text_auto=True,
            color_continuous_scale="Blues",
            x=["Bashorat: Normal", "Bashorat: Fraud"],
            y=["Haqiqiy: Normal", "Haqiqiy: Fraud"],
            title="Chalkash Matritsa (Confusion Matrix)",
        )
        fig.update_coloraxes(showscale=False)
        st.plotly_chart(fig, use_container_width=True)

    with col2:
        # ROC egri chizig'i
        fpr, tpr = m["roc_egri"]
        auc = m["test_roc_auc"]
        fig = go.Figure()
        fig.add_trace(go.Scatter(
            x=fpr, y=tpr, mode="lines",
            fill="tozeroy", fillcolor="rgba(59,130,246,0.10)",
            name=f"ROC  AUC={auc:.4f}",
            line=dict(color="#3b82f6", width=2.5),
        ))
        fig.add_trace(go.Scatter(
            x=[0, 1], y=[0, 1], mode="lines", name="Tasodifiy model",
            line=dict(color="gray", dash="dash", width=1),
        ))
        fig.update_layout(
            title="ROC Egri Chizig'i",
            xaxis_title="Yolg'on musbat darajasi (FPR)",
            yaxis_title="Haqiqiy musbat darajasi (TPR)",
            legend=dict(x=0.55, y=0.1),
        )
        st.plotly_chart(fig, use_container_width=True)

    col3, col4 = st.columns(2)
    with col3:
        # Aniqlik-Qamrov egri chizig'i
        aniqlik, qamrov = m["pr_egri"]
        ap = m["test_ort_aniqlik"]
        fig = go.Figure()
        fig.add_trace(go.Scatter(
            x=qamrov, y=aniqlik, mode="lines",
            fill="tozeroy", fillcolor="rgba(249,115,22,0.10)",
            name=f"PR  AP={ap:.4f}",
            line=dict(color="#f97316", width=2.5),
        ))
        fig.update_layout(
            title="Aniqlik-Qamrov Egri Chizig'i",
            xaxis_title="Qamrov (Recall)",
            yaxis_title="Aniqlik (Precision)",
        )
        st.plotly_chart(fig, use_container_width=True)

    with col4:
        # Eng muhim xususiyatlar
        fi = art["xususiyat_muhimlik"].head(15)
        fig = px.bar(
            fi, x="muhimlik", y="xususiyat", orientation="h",
            color="muhimlik", color_continuous_scale="Blues",
            title="Eng Muhim 15 ta Xususiyat",
        )
        fig.update_layout(yaxis={"categoryorder": "total ascending"})
        fig.update_coloraxes(showscale=False)
        st.plotly_chart(fig, use_container_width=True)

    # Tasnif hisoboti
    st.subheader("Tasnif Hisoboti (Classification Report)")
    hisobot = m["hisobot"]
    hisobot_df = pd.DataFrame(hisobot).T.loc[["0", "1", "macro avg", "weighted avg"]]
    hisobot_df.index = ["Normal", "Fraud", "Makro O'rtacha", "Og'irlikli O'rtacha"]
    hisobot_df = hisobot_df.drop(columns=["support"], errors="ignore")
    st.dataframe(hisobot_df.style.format("{:.4f}"), use_container_width=False)

    # Modelni tushuntirish
    with st.expander("Ko'rsatkichlarni qanday o'qish kerak?"):
        st.markdown("""
**ROC-AUC (0–1):** Model fraud va normalni qanchalik yaxshi ajrata olishi.
0.5 = tasodifiy, 1.0 = mukammal. **0.85–0.92 maqbul** diapazon.

**Precision (Aniqlik):** Fraud deb belgilangan tranzaksiyalarning qanchasi haqiqatan fraud.

**Recall (Qamrov):** Barcha fraud tranzaksiyalarning qanchasi aniqlangan.

**F1-ball:** Aniqlik va Qamrovning muvozanatli o'rtachasi.

**Chegara:** Ushbu qiymatdan yuqori fraud ehtimolligi REVIEW/BLOCK qaroriga olib keladi.
        """)


# ═══════════════════════════════════════════════════════════════════════════════
# TAB 5 — JONLI TEKSHIRISH
# ═══════════════════════════════════════════════════════════════════════════════
def tekshirish_tab() -> None:
    st.header("Jonli Tranzaksiya Tekshiruvi")

    art = modelni_yukla()
    if art is None:
        st.warning("Model topilmadi. Avval **Model O'rgatish** tabiga o'ting.")
        return

    st.caption(
        f"Model o'rgatilgan: {art['orgatish_sanasi']}  |  "
        f"Chegara: {art['chegara']:.2f}  |  "
        f"O'quv to'plamidagi fraud ulushi: {art['fraud_ulushi']*100:.1f}%"
    )

    render_oqim_simulyatsiyasi(art, malumotlarni_yukla())

    st.divider()
    st.subheader("Bitta tranzaksiyani qo'lda tekshirish")

    with st.form("tekshirish_formasi"):
        col1, col2, col3 = st.columns(3)

        with col1:
            st.subheader("Tranzaksiya")
            miqdor   = st.number_input("Miqdor ($)", 0.01, 100_000.0, 150.0, step=1.0)
            qurilma  = st.selectbox("Qurilma", QURILMALAR := [
                "ios", "android", "windows", "chrome", "safari", "linux", "emulator", "unknown",
            ])
            joylashuv = st.selectbox("Joylashuv", [
                "tashkent", "samarkand", "fergana", "bukhara", "almaty",
                "istanbul", "dubai", "foreign_ip", "unknown",
            ])
            soat = st.slider("Tranzaksiya soati", 0, 23, 14)

        with col2:
            st.subheader("Foydalanuvchi profili")
            chastota    = st.number_input("Bugungi tranzaksiyalar soni", 1, 60, 3)
            oxirgi_vaqt = st.number_input("Oxirgi tranzaksiyadan o'tgan vaqt (daqiqa)", 0.5, 1440.0, 120.0, step=5.0)
            yosh        = st.number_input("Hisob yoshi (kunlarda)", 1, 3650, 365)
            shikoyatlar = st.number_input("O'tgan shikoyatlar soni", 0, 20, 0)

        with col3:
            st.subheader("Xavf belgilari")
            geo_bayroq     = st.checkbox("Geo-joylashuv anomaliyasi")
            joy_nomuvofiq  = st.checkbox("Joylashuv nomuvofiqlik")
            qabul_tasdiql  = st.checkbox("Qabul qiluvchi tasdiqlangan", value=True)
            qora_royxat    = st.checkbox("Qabul qiluvchi qora ro'yxatda")
            vpn_proxy      = st.checkbox("VPN / Proxy aniqlandi")
            kategoriya_nom = st.checkbox("Savdo kategoriyasi nomuvofiq")
            limit_oshdi    = st.checkbox("Kunlik limit oshdi")
            yuqori_miqdor  = st.checkbox("Yaqinda yuqori miqdorli tranzaksiya")
            eski_fraud     = st.checkbox("Avvalgi firibgarlik xatti-harakati")

        yuborildi = st.form_submit_button(
            "🔍  Tranzaksiyani Tekshir", type="primary", use_container_width=True
        )

    if not yuborildi:
        return

    # Tranzaksiya ma'lumotlarini yig'ish
    tx = {
        "amount":                          miqdor,
        "device":                          qurilma,
        "location":                        joylashuv,
        "transaction_hour":                soat,
        "Transaction Frequency":           chastota,
        "Time Since Last Transaction":     oxirgi_vaqt,
        "Account Age":                     yosh,
        "Fraud Complaints Count":          shikoyatlar,
        "Geo-Location Flags":              int(geo_bayroq),
        "Location-Inconsistent Transactions": int(joy_nomuvofiq),
        "Recipient Verification Status":   int(qabul_tasdiql),
        "Recipient Blacklist Status":       int(qora_royxat),
        "VPN or Proxy Usage":              int(vpn_proxy),
        "Merchant Category Mismatch":      int(kategoriya_nom),
        "User Daily Limit Exceeded":       int(limit_oshdi),
        "Recent High-Value Transaction Flags": int(yuqori_miqdor),
        "Past Fraudulent Behavior Flags":  int(eski_fraud),
        "Normalized Transaction Amount":   0.0,  # tranzaksiyani_tekshir() ichida hisoblanadi
    }

    natija  = tranzaksiyani_tekshir(art, tx)
    ehtimol = natija["ehtimol"]
    xavf    = natija["xavf"]
    qaror   = natija["qaror"]

    rang  = {"LOW": "#22c55e", "MEDIUM": "#f59e0b", "HIGH": "#ef4444"}[xavf]
    belgi = {"LOW": "🟢", "MEDIUM": "🟡", "HIGH": "🔴"}[xavf]

    st.divider()

    c1, c2, c3 = st.columns(3)
    c1.metric("Fraud Ehtimolligi", f"{ehtimol * 100:.1f}%")
    c2.metric("Xavf Darajasi",    f"{belgi} {xavf}")
    c3.metric("Qaror",            qaror)

    # Xavf ko'rsatgichi (gauge)
    fig = go.Figure(go.Indicator(
        mode="gauge+number+delta",
        value=round(ehtimol * 100, 1),
        domain={"x": [0, 1], "y": [0, 1]},
        title={"text": "Fraud ko'rsatkichi (0 – 100)", "font": {"size": 18}},
        delta={
            "reference": art["chegara"] * 100,
            "suffix": "% chegara bilan solishtirganda",
        },
        gauge={
            "axis": {"range": [0, 100]},
            "bar": {"color": rang, "thickness": 0.25},
            "bgcolor": "white",
            "borderwidth": 2,
            "steps": [
                {"range": [0,   art["chegara"] * 80],  "color": "#dcfce7"},
                {"range": [art["chegara"] * 80, art["chegara"] * 130], "color": "#fef9c3"},
                {"range": [art["chegara"] * 130, 100], "color": "#fee2e2"},
            ],
            "threshold": {
                "line": {"color": "black", "width": 3},
                "thickness": 0.8,
                "value": art["chegara"] * 100,
            },
        },
    ))
    fig.update_layout(height=320, margin=dict(t=60, b=20))
    st.plotly_chart(fig, use_container_width=True)

    # Xavf omillari ro'yxati
    st.subheader("Xavf omillari tahlili")
    omillar = []
    if vpn_proxy:
        omillar.append(("🔴 YUQORI",   "VPN / Proxy aniqlandi"))
    if qora_royxat:
        omillar.append(("🔴 YUQORI",   "Qabul qiluvchi qora ro'yxatda"))
    if eski_fraud:
        omillar.append(("🔴 YUQORI",   "Foydalanuvchining avvalgi firibgarlik tarixi bor"))
    if limit_oshdi:
        omillar.append(("🟡 O'RTA",    "Kunlik limit oshib ketdi"))
    if joy_nomuvofiq:
        omillar.append(("🟡 O'RTA",    "Joylashuv nomuvofiqlik aniqlandi"))
    if geo_bayroq:
        omillar.append(("🟡 O'RTA",    "Geo-joylashuv anomaliyasi"))
    if shikoyatlar > 2:
        omillar.append(("🟡 O'RTA",    f"Shikoyatlar soni yuqori: {shikoyatlar} ta"))
    if kategoriya_nom:
        omillar.append(("🟠 PAST",     "Savdo kategoriyasi nomuvofiq"))
    if not qabul_tasdiql:
        omillar.append(("🟠 PAST",     "Qabul qiluvchi tasdiqlanmagan"))
    if yuqori_miqdor:
        omillar.append(("🟠 PAST",     "Yaqinda yuqori miqdorli tranzaksiya qayd etilgan"))
    if soat < 5:
        omillar.append(("🟠 PAST",     f"Kechasi amalga oshirilgan tranzaksiya ({soat:02d}:00)"))
    if yosh < 30:
        omillar.append(("🟠 PAST",     f"Juda yangi hisob ({yosh} kunlik)"))
    if qurilma in ("emulator", "unknown"):
        omillar.append(("🟠 PAST",     f"Shubhali qurilma turi: {qurilma}"))
    if joylashuv in ("foreign_ip", "unknown"):
        omillar.append(("🟠 PAST",     f"Shubhali joylashuv: {joylashuv}"))

    if omillar:
        for daraja, tavsif in omillar:
            st.write(f"{daraja} — {tavsif}")
    else:
        st.success("✅ Bu tranzaksiya uchun sezilarli xavf omili aniqlanmadi.")


# ═══════════════════════════════════════════════════════════════════════════════
# YON PANEL VA ASOSIY FUNKSIYA
# ═══════════════════════════════════════════════════════════════════════════════
def yon_panel() -> None:
    with st.sidebar:
        st.title("🛡️ SafeNet")
        st.caption("Fraud Detection System v1.0")
        st.divider()

        art = modelni_yukla()
        if art:
            m = art["korsatkichlar"]
            st.markdown("**Model holati:** ✅ Faol")
            st.metric("ROC-AUC",   f"{m['test_roc_auc']:.4f}")
            st.metric("F1-ball",   f"{m['test_f1']:.4f}")
            st.metric("Chegara",   f"{m['chegara']:.2f}")
            st.caption(f"O'rgatilgan: {art['orgatish_sanasi']}")
            st.caption(f"O'quv: {art['orgatish_soni']:,} ta")
            st.caption(f"Sinov: {art['test_soni']:,} ta")
        else:
            st.warning("Model yo'q.\n**Model O'rgatish** tabiga o'ting.")

        st.divider()
        st.caption(f"Dataset: {DATA_PATH.name}")
        st.caption(f"Xususiyatlar: {len(XUSUSIYATLAR)} ta")
        st.caption("Algoritm: Random Forest")


def main() -> None:
    yon_panel()

    # Streamlit Cloud yoki birinchi ishga tushirishda model yo'q/mos kelmasa,
    # datasetdan avtomatik o'rgatamiz. Bu pickle versiya muammolarini yo'qotadi.
    if modelni_yukla() is None and DATA_PATH.exists():
        st.info("Birinchi ishga tushirish: deploy uchun tezkor model tayyorlanmoqda...")
        bar = st.progress(0, "Boshlanmoqda…")
        df  = malumotlarni_yukla()
        tezkor_orgatish_va_saqlash(df, progress=bar)
        st.cache_resource.clear()
        st.rerun()

    tab1, tab2, tab3, tab4, tab5 = st.tabs([
        "📊  Ko'rinish",
        "🔍  Ko'zgu",
        "🤖  O'rgatish",
        "📈  Baholash",
        "⚡  Jonli Tekshirish",
    ])

    df = malumotlarni_yukla()

    with tab1:
        korinish_tab(df)
    with tab2:
        kozgu_tab(df)
    with tab3:
        orgatish_tab()
    with tab4:
        baholash_tab()
    with tab5:
        tekshirish_tab()


if __name__ == "__main__":
    main()
