"""
tests/test_notifications.py — Mixtape

Regression tests for rating notifications (Issue #4).
Against the buggy code, rate_song() created no notification, so
test_rating_notifies_sharer would fail (0 notifications instead of 1).
"""

import pytest
from app import create_app, db
from models import User, Song
from services.notification_service import rate_song, get_notifications


@pytest.fixture
def app():
    app = create_app({"TESTING": True, "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:"})
    with app.app_context():
        db.create_all()
        yield app
        db.drop_all()


@pytest.fixture
def data(app):
    with app.app_context():
        sharer = User(username="sharer", email="sharer@example.com")
        rater = User(username="rater", email="rater@example.com")
        db.session.add_all([sharer, rater])
        db.session.flush()
        song = Song(title="Track", artist="Various", shared_by=sharer.id)
        db.session.add(song)
        db.session.commit()
        yield {"sharer": sharer, "rater": rater, "song": song}


def test_rating_notifies_sharer(app, data):
    """Rating someone else's song creates a song_rated notification for the sharer."""
    with app.app_context():
        rate_song(data["rater"].id, data["song"].id, 5)
        notifs = get_notifications(data["sharer"].id)
        assert len(notifs) == 1
        assert notifs[0]["type"] == "song_rated"


def test_self_rating_does_not_notify(app, data):
    """Rating your own song does not create a notification."""
    with app.app_context():
        rate_song(data["sharer"].id, data["song"].id, 4)
        assert get_notifications(data["sharer"].id) == []
