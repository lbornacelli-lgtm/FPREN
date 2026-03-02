"""Tests for the /feedback route in the weather_rss web dashboard."""
import sys
import os
import pytest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.dirname(__file__))


@pytest.fixture
def client():
    """Create a Flask test client with MongoDB mocked out."""
    mock_col = MagicMock()
    mock_col.find.return_value = []

    mock_db = MagicMock()
    mock_db.__getitem__ = MagicMock(return_value=mock_col)
    mock_db.__getitem__.side_effect = lambda key: mock_col

    mock_mongo_client = MagicMock()
    mock_mongo_client.__getitem__ = MagicMock(return_value=mock_db)

    with patch("pymongo.MongoClient", return_value=mock_mongo_client):
        import app as flask_app
        flask_app.app.config["TESTING"] = True
        # Patch the module-level db and status_col
        flask_app.db = mock_db
        flask_app.status_col = mock_col
        with flask_app.app.test_client() as c:
            yield c, mock_db


def test_feedback_missing_message_redirects(client):
    c, _ = client
    resp = c.post("/feedback", data={"name": "Alice", "message": ""})
    assert resp.status_code == 302
    assert "msg=" in resp.headers["Location"]
    assert "required" in resp.headers["Location"].lower()


def test_feedback_valid_redirects_with_thank_you(client):
    c, mock_db = client
    resp = c.post("/feedback", data={"name": "Alice", "message": "Looks great!"})
    assert resp.status_code == 302
    assert "Thank+you" in resp.headers["Location"]


def test_feedback_inserts_document(client):
    c, mock_db = client
    mock_feedback_col = MagicMock()
    mock_db.__getitem__ = MagicMock(return_value=mock_feedback_col)

    resp = c.post("/feedback", data={"name": "Alice", "message": "Nice dashboard"})
    assert resp.status_code == 302


def test_feedback_anonymous_name(client):
    c, mock_db = client
    resp = c.post("/feedback", data={"name": "", "message": "Good work"})
    assert resp.status_code == 302
    assert "Thank+you" in resp.headers["Location"]


def test_feedback_button_present_in_dashboard(client):
    c, _ = client
    resp = c.get("/")
    assert resp.status_code == 200
    assert b"Feedback" in resp.data


def test_feedback_dialog_present_in_dashboard(client):
    c, _ = client
    resp = c.get("/")
    assert resp.status_code == 200
    assert b"feedbackDialog" in resp.data


def test_feedback_form_action_in_dashboard(client):
    c, _ = client
    resp = c.get("/")
    assert resp.status_code == 200
    assert b'action="/feedback"' in resp.data
