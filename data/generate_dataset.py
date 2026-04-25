"""
SafeNet — Realistik sintetik tranzaksiya ma'lumotlari generatori.

Maqsad: ML model real hayotga mos ko'rsatkichlar bersin (ROC-AUC ~0.87-0.92).
Strategiya: har bir xususiyat uchun fraud va normal taqsimotlari sezilarli
            darajada kesishsin — model hech qachon mukammal bo'lmasin.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import numpy as np
import pandas as pd

SEED = 42
N_JAMI = 50_000          # jami tranzaksiyalar soni
FRAUD_ULUSHI = 0.05      # 5% fraud (real sanoatda odatda 0.1-10%)
SOM_KONVERSIYA = 12_500  # bazaviy synthetic scale -> Uzbek so'm scale
CHIQISH = Path(__file__).parent / "synthetic_transactions.csv"

# Qurilmalar va ehtimolliklar
QURILMALAR = ["ios", "android", "windows", "chrome", "safari", "linux", "emulator", "unknown"]
QURILMA_EHTIMOL_NORMAL = [0.27, 0.31, 0.15, 0.12, 0.07, 0.03, 0.03, 0.02]
QURILMA_EHTIMOL_FRAUD  = [0.22, 0.25, 0.13, 0.10, 0.06, 0.03, 0.12, 0.09]

# Joylashuvlar
JOYLAR = ["tashkent", "samarkand", "fergana", "bukhara", "almaty",
          "istanbul", "dubai", "foreign_ip", "unknown"]
JOY_EHTIMOL_NORMAL = [0.36, 0.17, 0.13, 0.10, 0.08, 0.06, 0.04, 0.04, 0.02]
JOY_EHTIMOL_FRAUD  = [0.27, 0.13, 0.10, 0.08, 0.09, 0.08, 0.06, 0.11, 0.08]

# Soatlar bo'yicha og'irliklar (0-23)
# Normal: 8-22 soat ko'proq, tunda kam
# Fraud:  tunda 0-5 sezilarli ko'p lekin kunduzi ham bo'ladi
SOAT_NORMAL = np.array([
    0.5, 0.4, 0.3, 0.3, 0.3, 0.5,   # 0-5: tungi, juda kam
    2.0, 4.5, 5.5, 6.0, 6.5, 6.5,   # 6-11: ertalab
    6.0, 6.0, 5.5, 5.5, 5.0, 5.0,   # 12-17: kun o'rtasi
    4.5, 4.0, 3.5, 3.0, 2.0, 1.5,   # 18-23: kechqurun
])
SOAT_FRAUD = np.array([
    4.5, 4.0, 3.8, 3.5, 3.2, 4.0,   # 0-5: tunda KO'P
    3.5, 4.5, 5.0, 5.5, 5.5, 5.0,   # 6-11: ertalab
    4.5, 4.5, 4.0, 4.0, 3.8, 3.8,   # 12-17: kun o'rtasi
    3.5, 3.2, 3.0, 2.5, 2.0, 2.0,   # 18-23: kechqurun
])
SOAT_NORMAL /= SOAT_NORMAL.sum()
SOAT_FRAUD  /= SOAT_FRAUD.sum()


def shovqinli_binary(rng, is_fraud, p_fraud, p_normal, shovqin_std=0.12):
    """
    Har bir tranzaksiya uchun binary xususiyat yaratadi.
    p_fraud/p_normal — asosiy ehtimollik,
    shovqin — Gauss taqsimotdan qo'shiladi (real hayot noaniqliklarini ifodalaydi).
    Bu tufayli fraud va normallar orasida sezilarli kesishuv paydo bo'ladi.
    """
    asosiy = np.where(is_fraud == 1, p_fraud, p_normal)
    shovqin = rng.normal(0, shovqin_std, len(is_fraud))
    ehtimol = (asosiy + shovqin).clip(0.01, 0.99)
    return (rng.random(len(is_fraud)) < ehtimol).astype(int)


def generate(seed: int = SEED) -> pd.DataFrame:
    rng = np.random.default_rng(seed)

    n = N_JAMI
    n_fraud  = int(n * FRAUD_ULUSHI)
    n_normal = n - n_fraud

    # ── Asosiy maydonlar ────────────────────────────────────────────────────

    # Miqdorlar avval bazaviy synthetic scale'da yaratiladi, keyin so'mga o'tkaziladi.
    # Fraud o'rtachasi biroz yuqori, lekin kichik fraud va katta normal
    # tranzaksiyalar ham ko'p bo'lgani uchun model mukammal ajrata olmaydi.
    miqdor_normal = rng.lognormal(np.log(120), 1.05, n_normal).clip(1.0, 25_000)
    miqdor_fraud  = rng.lognormal(np.log(190), 1.15, n_fraud ).clip(1.0, 35_000)

    # Fraudlarning bir qismi past miqdor bilan yashirinadi.
    mikro_fraud = rng.random(n_fraud) < 0.22
    miqdor_fraud[mikro_fraud] = rng.lognormal(np.log(45), 0.8, mikro_fraud.sum()).clip(1, 250)

    # Normal tranzaksiyalarning bir qismi katta miqdorli bo'ladi.
    katta_normal = rng.random(n_normal) < 0.08
    miqdor_normal[katta_normal] = rng.lognormal(
        np.log(450), 0.9, katta_normal.sum()
    ).clip(80, 20_000)

    # So'mga o'tkazish: 1 bazaviy birlik ~= 12 500 so'm.
    # 100 so'mgacha yaxlitlash real to'lov tizimlari uchun qulayroq ko'rinadi.
    miqdor_normal = (np.round((miqdor_normal * SOM_KONVERSIYA) / 100) * 100).astype(int)
    miqdor_fraud = (np.round((miqdor_fraud * SOM_KONVERSIYA) / 100) * 100).astype(int)

    user_id_normal = [f"user_{rng.integers(1, 801):03d}" for _ in range(n_normal)]
    user_id_fraud  = [f"user_{rng.integers(1, 801):03d}" for _ in range(n_fraud)]

    qurilma_normal = rng.choice(QURILMALAR, n_normal, p=QURILMA_EHTIMOL_NORMAL)
    qurilma_fraud  = rng.choice(QURILMALAR, n_fraud,  p=QURILMA_EHTIMOL_FRAUD)

    joy_normal = rng.choice(JOYLAR, n_normal, p=JOY_EHTIMOL_NORMAL)
    joy_fraud  = rng.choice(JOYLAR, n_fraud,  p=JOY_EHTIMOL_FRAUD)

    soat_normal = rng.choice(24, n_normal, p=SOAT_NORMAL)
    soat_fraud  = rng.choice(24, n_fraud,  p=SOAT_FRAUD)

    df = pd.concat([
        pd.DataFrame({
            "user_id": user_id_normal, "amount": miqdor_normal.round(2),
            "device": qurilma_normal, "location": joy_normal,
            "transaction_hour": soat_normal, "isFraud": 0,
        }),
        pd.DataFrame({
            "user_id": user_id_fraud, "amount": miqdor_fraud.round(2),
            "device": qurilma_fraud, "location": joy_fraud,
            "transaction_hour": soat_fraud, "isFraud": 1,
        }),
    ], ignore_index=True).sample(frac=1, random_state=seed).reset_index(drop=True)

    n_jami    = len(df)
    is_fraud  = df["isFraud"].values

    # ── Qo'shimcha xususiyatlar ──────────────────────────────────────────────

    # Tranzaksiya chastotasi (kunlik)
    # Normal va fraud chastotalari ataylab yaqinroq: real hayotda aktiv
    # normal mijozlar ham ko'p tranzaksiya qiladi.
    chastota_n = rng.integers(1, 13, n_jami)
    chastota_f = rng.integers(2, 17, n_jami)
    past_chastota = rng.random(n_jami) < 0.38
    chastota_f = np.where(past_chastota, rng.integers(1, 7, n_jami), chastota_f)
    df["Transaction Frequency"] = np.where(is_fraud, chastota_f, chastota_n).clip(1, 30)

    # Oxirgi tranzaksiyadan o'tgan vaqt (daqiqa)
    # Vaqt oralig'i: fraud odatda tezroq, lekin sezilarli kesishuv bor.
    vaqt_n = rng.exponential(220, n_jami).clip(1,   1440)
    vaqt_f = rng.exponential(130, n_jami).clip(0.5, 1440)
    vaqt_f = np.where(
        rng.random(n_jami) < 0.35,
        rng.exponential(240, n_jami).clip(30, 1440),
        vaqt_f,
    )
    df["Time Since Last Transaction"] = np.where(is_fraud, vaqt_f, vaqt_n).round(1)

    # Qurilma barmoq izi (hash — ML uchun bevosita foydali emas, identifikator sifatida)
    df["Device Fingerprinting"] = [
        hashlib.md5(f"{row.user_id}{row.device}{i}".encode()).hexdigest()[:12]
        for i, row in df.iterrows()
    ]

    # Binary xavf belgilari: farqlar kichikroq, shovqin esa kattaroq.
    # Bu "shubhali normal" va "toza fraud" holatlarini ko'paytiradi.

    # Geo-joylashuv anomaliyasi
    df["Geo-Location Flags"] = shovqinli_binary(rng, is_fraud, 0.18, 0.07, 0.13)

    # Joylashuv nomuvofiqlik
    df["Location-Inconsistent Transactions"] = shovqinli_binary(rng, is_fraud, 0.17, 0.07, 0.12)

    # Qabul qiluvchi tasdiqlangan (1=ha). Fraudda biroz kamroq, lekin
    # ko'p fraudlar ham tasdiqlangan qabul qiluvchidan foydalanadi.
    df["Recipient Verification Status"] = shovqinli_binary(rng, is_fraud, 0.78, 0.93, 0.09)

    # Qabul qiluvchi qora ro'yxatda
    df["Recipient Blacklist Status"] = shovqinli_binary(rng, is_fraud, 0.07, 0.02, 0.05)

    # VPN/Proxy ishlatish
    df["VPN or Proxy Usage"] = shovqinli_binary(rng, is_fraud, 0.16, 0.05, 0.10)

    # Savdo kategoriyasi nomuvofiqlik
    df["Merchant Category Mismatch"] = shovqinli_binary(rng, is_fraud, 0.14, 0.08, 0.10)

    # Hisob yoshi (kunlarda)
    # Yangi hisoblar riskliroq, lekin eski buzilgan hisoblar ham mavjud.
    yosh_n = rng.integers(20, 3650, n_jami)
    yosh_f = rng.integers(1,  2500, n_jami)
    eski_fraud = rng.random(n_jami) < 0.48
    yosh_f = np.where(eski_fraud, rng.integers(300, 3650, n_jami), yosh_f)
    df["Account Age"] = np.where(is_fraud, yosh_f, yosh_n)

    # Kunlik limit oshgan
    df["User Daily Limit Exceeded"] = shovqinli_binary(rng, is_fraud, 0.13, 0.04, 0.08)

    # Yaqinda yuqori miqdorli tranzaksiya
    df["Recent High-Value Transaction Flags"] = shovqinli_binary(rng, is_fraud, 0.19, 0.13, 0.11)

    # Avvalgi firibgarlik xatti-harakati
    df["Past Fraudulent Behavior Flags"] = shovqinli_binary(rng, is_fraud, 0.11, 0.04, 0.07)

    # Tranzaksiya miqdori (amount nusxasi)
    df["Transaction Amount"] = df["amount"]

    # Normallashtirilgan miqdor
    amt_min = df["amount"].min()
    amt_max = df["amount"].max()
    df["Normalized Transaction Amount"] = ((df["amount"] - amt_min) / (amt_max - amt_min)).round(4)

    # Shikoyatlar soni
    shik_n = rng.integers(0, 4, n_jami)
    shik_f = rng.integers(0, 6, n_jami)
    # Ko'p fraudlar hali shikoyatga tushmagan bo'lishi mumkin.
    shik_f = np.where(rng.random(n_jami) < 0.58, 0, shik_f)
    df["Fraud Complaints Count"] = np.where(is_fraud, shik_f, shik_n)

    # Nishon yorlig'i (isFraud nusxasi)
    df["Label"] = df["isFraud"]

    # Ustunlar tartibini birlashtirish
    tartib = [
        "user_id", "amount", "device", "location", "transaction_hour", "isFraud",
        "Transaction Amount", "Transaction Frequency", "Time Since Last Transaction",
        "Device Fingerprinting", "Geo-Location Flags", "Location-Inconsistent Transactions",
        "Recipient Verification Status", "Recipient Blacklist Status", "VPN or Proxy Usage",
        "Merchant Category Mismatch", "Account Age", "User Daily Limit Exceeded",
        "Recent High-Value Transaction Flags", "Past Fraudulent Behavior Flags",
        "Normalized Transaction Amount", "Fraud Complaints Count", "Label",
    ]
    df = df[tartib]
    df.to_csv(CHIQISH, index=False)

    fraud_soni = int(df["isFraud"].sum())
    print(f"OK  Jami: {n_jami:,} ta tranzaksiya")
    print(f"   Fraud: {fraud_soni:,} ({fraud_soni/n_jami*100:.1f}%)")
    print(f"   Normal: {n_jami - fraud_soni:,}")
    print(f"   Saqlandi: {CHIQISH}")
    return df


if __name__ == "__main__":
    generate()
