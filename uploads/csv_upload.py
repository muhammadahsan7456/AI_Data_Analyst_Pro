import os
import sys
import pandas as pd

# Ensure workspace root is in sys.path for direct script execution
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

try:
    from database.connection import get_db_cursor
    from database.queries import insert_dataset
    from uploads.data_loader import (
        clean_table_name,
        create_table,
        insert_dataframe
    )
except ModuleNotFoundError:
    from connection import get_db_cursor
    from queries import insert_dataset
    from data_loader import (
        clean_table_name,
        create_table,
        insert_dataframe
    )

from uploads.multi_loader import process_file_upload, SUPPORTED_EXTENSIONS

ALLOWED_EXTENSIONS = set(SUPPORTED_EXTENSIONS.keys())
MAX_FILE_SIZE_MB = 1000


def allowed_file(filename: str) -> bool:
    """
    Check if the file has a valid extension.
    """
    ext = os.path.splitext(filename)[1].lower()
    return ext in ALLOWED_EXTENSIONS


def upload_csv(uploaded_file, user_id: int = 1, tags: str = None):
    """
    Process file upload from web request supporting CSV, Excel, JSON, XML, TXT, TSV, Parquet, Feather.
    """
    return process_file_upload(uploaded_file, user_id=user_id, tags=tags)



def select_csv_file():
    """
    CLI helper for local desktop CSV upload.
    """
    filepath = input("Enter full path to CSV file: ").strip('"\'')
    if not os.path.exists(filepath):
        print("❌ File not found.")
        return

    try:
        with open(filepath, "rb") as f:
            class MockFile:
                def __init__(self, file_obj, name):
                    self._file = file_obj
                    self.filename = name
                def read(self, *args): return self._file.read(*args)
                def seek(self, *args): return self._file.seek(*args)
                def tell(self): return self._file.tell()

            mock_upload = MockFile(f, os.path.basename(filepath))
            success, result = upload_csv(mock_upload)
            if success:
                print(f"\n✅ Uploaded '{result['table_name']}' successfully!")
            else:
                print(f"\n❌ Upload failed: {result}")
    except Exception as e:
        print(f"\n❌ Upload error: {e}")