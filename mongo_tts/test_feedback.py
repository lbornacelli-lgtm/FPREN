"""Tests for the /feedback route in the mongo_tts Flask app."""
import sys
import os
import pytest
from unittest.mock import MagicMock, patch

# Ensure the mongo_tts directory is on the path
sys.path.insert(0, os.path.dirname(__file__))


@pytest.fixture
def client():
    """Create a Flask test client with MongoDB mocked out."""
    mock_col = MagicMock()

    with patch("db._client", MagicMock()), \
         patch("db.get_collection", return_value=mock_col), \
         patch("pymongo.MongoClient") as mock_mongo:

        mock_db = MagicMock()
        mock_mongo.return_value.__getitem__ = lambda self, key: mock_db
        mock_db.__getitem__ = lambda self, key: mock_col

        import app as flask_app
        flask_app.app.config["TESTING"] = True
        with flask_app.app.test_client() as c:
            yield c, mock_col


def test_feedback_missing_message_redirects(client):
    c, _ = client
    resp = c.post("/feedback", data={"name": "Alice", "message": ""})
    assert resp.status_code == 302
    assert b"msg=Feedback" in resp.headers["Location"].encode()
    assert b"required" in resp.headers["Location"].encode().lower() or \
           "required" in resp.headers["Location"].lower()


def test_feedback_valid_submission_redirects(client):
    c, mock_col = client
    resp = c.post("/feedback", data={"name": "Alice", "message": "Great tool!"})
    assert resp.status_code == 302
    assert "Thank+you" in resp.headers["Location"]


def test_feedback_inserts_into_mongo(client):
    c, mock_col = client
    with patch("pymongo.MongoClient") as mock_mongo:
        mock_db = MagicMock()
        mock_feedback_col = MagicMock()
        mock_mongo.return_value.__getitem__ = MagicMock(return_value=mock_db)
        mock_db.__getitem__ = MagicMock(return_value=mock_feedback_col)

        resp = c.post("/feedback", data={"name": "Bob", "message": "Nice app"})
        assert resp.status_code == 302


def test_feedback_anonymous_when_no_name(client):
    c, mock_col = client
    with patch("pymongo.MongoClient") as mock_mongo:
        mock_db = MagicMock()
        mock_feedback_col = MagicMock()
        mock_mongo.return_value.__getitem__ = MagicMock(return_value=mock_db)
        mock_db.__getitem__ = MagicMock(return_value=mock_feedback_col)

        resp = c.post("/feedback", data={"name": "", "message": "Hello"})
        assert resp.status_code == 302
        assert "Thank+you" in resp.headers["Location"]


def test_feedback_button_present_in_index(client):
    c, mock_col = client
    mock_col.find.return_value = []
    resp = c.get("/")
    assert resp.status_code == 200
    assert b"Feedback" in resp.data


def test_feedback_modal_present_in_index(client):
    c, mock_col = client
    mock_col.find.return_value = []
    resp = c.get("/")
    assert resp.status_code == 200
    assert b"feedbackModal" in resp.data
