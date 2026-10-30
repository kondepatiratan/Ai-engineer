"""Cleaning the raw orders export.

Problems found in orders_sample.csv (see docs/decision_log.md):
  * 30 exact duplicate rows (same order_id)
  * final_status in mixed case / with spaces ("Rto", " RTO", "delivered")
  * 1 order still IN_TRANSIT (no outcome yet -> excluded from training)
  * phones as "+91 98xxx xxxxx" or only 9 digits
  * 12 missing pincodes, 4 missing cities
  * 3 orders with order_value = 0
"""
from __future__ import annotations

import re

import pandas as pd

PIN_RE = re.compile(r"^[1-9]\d{5}$")


def normalise_phone(raw) -> str:
    digits = re.sub(r"\D", "", str(raw or ""))
    if len(digits) == 12 and digits.startswith("91"):
        digits = digits[2:]
    return digits


def phone_is_valid(phone: str) -> bool:
    return bool(re.fullmatch(r"[6-9]\d{9}", phone or ""))


def load_orders(path, keep_unresolved: bool = False) -> pd.DataFrame:
    df = pd.read_csv(path, dtype={"phone": str, "pincode": str})
    df = df.drop_duplicates(subset="order_id", keep="first").copy()

    df["final_status"] = df["final_status"].astype(str).str.strip().str.upper()
    if not keep_unresolved:
        df = df[df["final_status"].isin(["DELIVERED", "RTO"])].copy()
    df["is_rto"] = (df["final_status"] == "RTO").astype(int)

    df["order_datetime"] = pd.to_datetime(df["order_datetime"])
    df["order_date"] = df["order_datetime"].dt.date
    df["phone"] = df["phone"].map(normalise_phone)
    df["pincode"] = df["pincode"].fillna("").str.replace(r"\.0$", "", regex=True).str.strip()
    for col in ["customer_name", "address", "city", "state", "items", "utm_source", "courier"]:
        df[col] = df[col].fillna("").astype(str).str.strip()
    return df.reset_index(drop=True)
