import io
from unittest.mock import Mock

from flask import Flask, request, session
from werkzeug.datastructures import FileStorage, MultiDict

import frontend.routes as frontend_routes
import utils.subscription


class ReportedSizeStream(io.BytesIO):
    def __init__(self, content, reported_size=None):
        super().__init__(content)
        self.reported_size = reported_size

    def seek(self, offset, whence=io.SEEK_SET):
        if whence == io.SEEK_END and self.reported_size is not None:
            super().seek(0, io.SEEK_END)
            self._reported_position = self.reported_size
            return self.reported_size
        self._reported_position = None
        return super().seek(offset, whence)

    def tell(self):
        if self._reported_position is not None:
            return self._reported_position
        return super().tell()


def _upload_request(monkeypatch, file_size, content=None, reported_size=None):
    app = Flask(__name__)
    app.secret_key = "isolated-upload-test"
    app.register_blueprint(frontend_routes.frontend)

    process_upload = Mock(return_value=(True, {
        "dataset_id": 1,
        "dataset_name": "sample.csv",
        "file_name": "sample.csv",
        "total_rows": 1,
        "total_columns": 1,
    }))
    monkeypatch.setattr(
        utils.subscription,
        "get_user_subscription_info",
        lambda user_id: {"is_pro": False, "has_pending_payment": False},
    )
    monkeypatch.setattr(frontend_routes, "process_file_upload", process_upload)
    monkeypatch.setattr(frontend_routes, "trigger_ai_event", lambda *args, **kwargs: None)
    monkeypatch.setattr(frontend_routes, "prewarm_schema_intelligence", lambda *args: None)
    monkeypatch.setattr(frontend_routes, "log_upload_event", lambda *args, **kwargs: None)
    monkeypatch.setattr(frontend_routes.system_cache, "invalidate_pattern", lambda *args: None)

    raw_view = frontend_routes.api_upload_file
    while hasattr(raw_view, "__wrapped__"):
        raw_view = raw_view.__wrapped__

    stream = ReportedSizeStream(
        content if content is not None else b"x" * file_size,
        reported_size=reported_size,
    )
    uploaded_file = FileStorage(stream=stream, filename="sample.csv")
    with app.test_request_context(
        "/api/upload-file",
        method="POST",
        headers={"X-Requested-With": "XMLHttpRequest"},
    ):
        session["user_id"] = 1
        request.files = MultiDict([("file", uploaded_file)])
        response = raw_view()

    if isinstance(response, tuple):
        response, status = response
    else:
        status = response.status_code
    return status, response.get_json(), process_upload


def test_free_user_upload_below_one_mib_reaches_file_processor(monkeypatch):
    size = 1024 * 1024 - 1
    status, payload, process_upload = _upload_request(monkeypatch, size)

    assert status == 200
    assert payload["success"] is True
    process_upload.assert_called_once()


def test_free_user_upload_at_one_mib_is_blocked_server_side(monkeypatch):
    size = 1024 * 1024
    status, payload, process_upload = _upload_request(monkeypatch, size)

    assert status == 400
    assert payload["success"] is False
    assert payload["upgrade_required"] is True
    process_upload.assert_not_called()


def test_upload_over_five_hundred_mib_is_rejected_server_side(monkeypatch):
    maximum = 500 * 1024 * 1024
    status, payload, process_upload = _upload_request(
        monkeypatch,
        file_size=1,
        reported_size=maximum + 1,
    )

    assert status == 400
    assert payload["success"] is False
    assert "absolute maximum system limit of 500 MB" in payload["error"]
    process_upload.assert_not_called()
