"""ETL pipeline that reads from CSV, transforms, and writes to database."""

import pandas as pd
from sqlalchemy import create_engine

engine = create_engine("postgresql://localhost/analytics")


def extract_users(csv_path: str) -> pd.DataFrame:
    """Read user data from CSV file."""
    df = pd.read_csv(csv_path)
    return df


def transform_users(df: pd.DataFrame) -> pd.DataFrame:
    """Clean and transform user data."""
    # Filter only active users
    df = df[df["status"] == "active"]
    # Uppercase names
    df["full_name"] = df["first_name"].str.upper() + " " + df["last_name"].str.upper()
    # Drop raw name columns
    df = df.drop(columns=["first_name", "last_name"])
    # Add computed age bucket
    df["age_bucket"] = pd.cut(df["age"], bins=[0, 18, 35, 50, 100], labels=["teen", "young", "mid", "senior"])
    return df


def load_users(df: pd.DataFrame) -> None:
    """Write transformed users to the analytics database."""
    df.to_sql("active_users_report", engine, if_exists="replace", index=False)


def run_pipeline(csv_path: str) -> None:
    raw = extract_users(csv_path)
    transformed = transform_users(raw)
    load_users(transformed)
