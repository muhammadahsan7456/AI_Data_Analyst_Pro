"""
AI Data Analyst Pro - Interactive CLI Interface
"""

from visualization.charts import show_chart
from visualization.chart_selector import select_chart
from ai.data_summary import summarize_dataset
from ai.gemini import ask_gemini, execute_sql_with_retry
from ai.sql_agent import explain_result
from database.connection import (
    get_connection,
    run_query,
    get_latest_table,
    get_table_columns
)
from database.queries import (
    get_all_users,
    insert_user
)
from uploads.csv_upload import select_csv_file


def show_users():
    """Display all users."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(get_all_users())
    users = cursor.fetchall()

    print("\n========== USERS ==========\n")
    for user in users:
        print(user)
    conn.close()


def add_user():
    """Add new user."""
    full_name = input("Enter Full Name: ").strip()
    email = input("Enter Email: ").strip()
    password = input("Enter Password: ").strip()

    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(insert_user(), (full_name, email, password))
    conn.commit()
    conn.close()

    print("\n✅ User Added Successfully!")


def show_dataset_overview_cli(rows):
    """Display dataset summary in CLI."""
    print("\n========== DATASET SUMMARY ==========\n")
    print(summarize_dataset(rows))

    print("\n========== SAMPLE DATA ==========\n")
    print(rows.head())


def ask_ai():
    """Ask questions from uploaded dataset."""
    table_name = get_latest_table()
    if table_name is None:
        print("❌ No uploaded dataset table found.")
        return

    print(f"\n📂 Table Detected : {table_name}")
    columns = get_table_columns(table_name)
    print(f"✅ Total Columns  : {len(columns)}")

    question = input("\n🤖 Ask AI: ").strip()
    if not question:
        return

    ai_result = execute_sql_with_retry(question, table_name, columns, max_retries=3)

    print("\n========== GENERATED SQL ==========\n")
    print(ai_result["sql"])
    print(f"⚡ Execution Time : {ai_result['execution_time_ms']} ms")
    print(f"🎯 Confidence     : {int(ai_result['confidence'] * 100)}%")

    if not ai_result["success"]:
        print(f"\n❌ SQL Execution Error: {ai_result['error']}")
        return

    df = ai_result["df"]
    if df.empty:
        print("❌ No Records Found.")
        return

    print("\n========== QUERY RESULT ==========\n")
    print(df)

    # Dataset Overview & Summary
    show_dataset_overview_cli(df)

    # AI Explanation
    explanation_prompt = explain_result(question, df.to_string(index=False))
    explanation = ask_gemini(explanation_prompt)

    print("\n========== AI EXPLANATION ==========\n")
    print(explanation)

    # Visualization Choice
    chart_choice = input("\n📊 Show Chart? (y/n): ").strip().lower()
    if chart_choice == "y":
        chart_type = select_chart(df)
        if chart_type:
            print(f"\n📈 Selected Chart : {chart_type.upper()}")
            show_chart(chart_type, df)
        else:
            print("❌ AI could not determine a suitable chart.")


def main():
    while True:
        print("\n" + "=" * 40)
        print("      AI DATA ANALYST PRO")
        print("=" * 40)
        print("1. Show Users")
        print("2. Add New User")
        print("3. Upload CSV")
        print("4. Ask AI")
        print("5. Exit")

        choice = input("\nEnter Your Choice: ").strip()
        if choice == "1":
            show_users()
        elif choice == "2":
            add_user()
        elif choice == "3":
            select_csv_file()
        elif choice == "4":
            ask_ai()
        elif choice == "5":
            print("\n👋 Goodbye!")
            break
        else:
            print("\n❌ Invalid Choice")


if __name__ == "__main__":
    main()