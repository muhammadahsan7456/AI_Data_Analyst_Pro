import pandas as pd
import pytest

from visualization.chart_generator import generate_interactive_chart_spec
from visualization.chart_selector import select_chart


@pytest.mark.parametrize(
    ("dataframe", "expected_chart"),
    [
        (
            pd.DataFrame(
                {"Region": ["North", "South", "East", "West"], "Sales": [12, 9, 7, 6]}
            ),
            "bar",
        ),
        (
            pd.DataFrame(
                {"Month": ["Jan", "Feb", "Mar", "Apr"], "Sales": [12, 9, 14, 17]}
            ),
            "line",
        ),
        (
            pd.DataFrame(
                {"Product": [f"Product {n}" for n in range(12)], "Revenue": range(12)}
            ),
            "horizontal_bar",
        ),
        (
            pd.DataFrame(
                {
                    "Units": [10, 15, 20, 25],
                    "Revenue": [100, 175, 230, 290],
                }
            ),
            "scatter",
        ),
        (
            pd.DataFrame(
                {
                    "Status": ["Delivered", "Pending", "Returned", "Cancelled"],
                    "OrderCount": [55, 12, 8, 3],
                }
            ),
            "pie",
        ),
    ],
)
def test_aggregated_result_chart_types_match_result_shape(dataframe, expected_chart):
    spec = generate_interactive_chart_spec(dataframe, select_chart(dataframe))

    assert spec["chart_type"] == expected_chart
    assert spec["labels"]
    assert len(spec["labels"]) == len(spec["values"])
    if expected_chart == "scatter":
        assert spec["labels"] == dataframe["Units"].tolist()
        assert spec["values"] == dataframe["Revenue"].tolist()
