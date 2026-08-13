import pandas as pd


def show_dataset_overview(df):
    """
    Display quick dataset overview.
    """

    print("\n" + "=" * 40)
    print("📂 DATASET OVERVIEW")
    print("=" * 40)

    print(f"Rows          : {df.shape[0]}")
    print(f"Columns       : {df.shape[1]}")
    print(f"Memory Usage  : {round(df.memory_usage(deep=True).sum()/1024,2)} KB")

    print("\nColumns:")
    for col in df.columns:
        print(f"• {col}")

    print("=" * 40)



