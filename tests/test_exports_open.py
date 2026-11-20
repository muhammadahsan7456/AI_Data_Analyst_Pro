"""
R-Phase 9.7: every export format opens in the library that reads that format.

CSV (pandas), Excel (openpyxl), PDF (pypdf, skipped if not installed), Word (python-docx),
PowerPoint (python-pptx). The frame includes Unicode (Urdu), missing values, dates and
negative numbers, which are the cases that used to break report builders.
"""
import io

import pandas as pd
import pytest

from utils.exporters import (
    export_to_csv,
    export_to_excel,
    export_to_pdf_report,
    export_to_pptx_report,
    export_to_word_report,
)


@pytest.fixture
def df():
    return pd.DataFrame({
        "OrderDate": pd.to_datetime(["2025-01-05", "2025-02-11", "2025-03-20", None]),
        "City": ["Lahore", "کراچی", "Islamabad", None],
        "Sales": [1200.5, -45.0, 99999.99, None],
        "Units": [3, 0, 12, 7],
    })


def test_csv_opens(df):
    back = pd.read_csv(io.BytesIO(export_to_csv(df)))
    assert list(back.columns) == list(df.columns) and len(back) == 4
    assert "کراچی" in back["City"].tolist()


def test_excel_opens(df):
    openpyxl = pytest.importorskip("openpyxl")
    wb = openpyxl.load_workbook(io.BytesIO(export_to_excel(df, dataset_name="sales")))
    ws = wb.worksheets[0]
    assert ws.max_row >= 5 and ws.max_column >= 4
    values = [c.value for row in ws.iter_rows() for c in row]
    assert "کراچی" in values


def test_pdf_opens(df):
    pypdf = pytest.importorskip("pypdf")
    reader = pypdf.PdfReader(io.BytesIO(export_to_pdf_report(df, dataset_name="sales", insights=["Sales rose."])))
    assert len(reader.pages) >= 1
    assert "sales" in reader.pages[0].extract_text().lower()


def test_word_opens(df):
    docx = pytest.importorskip("docx")
    doc = docx.Document(io.BytesIO(export_to_word_report(df, dataset_name="sales", insights=["Sales rose."])))
    assert doc.paragraphs and len(doc.tables) >= 1


def test_pptx_opens(df):
    pptx = pytest.importorskip("pptx")
    pres = pptx.Presentation(io.BytesIO(export_to_pptx_report(df, dataset_name="sales", insights=["Sales rose."])))
    assert len(pres.slides) >= 2
