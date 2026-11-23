import time

import room_companion


def _wait_for(predicate, timeout=5):
    deadline = time.monotonic() + timeout
    while not predicate():
        assert time.monotonic() < deadline, "timed out"
        time.sleep(0.01)


class TestRoomCompanion:
    def test_illustrates_each_distinct_scene(self, monkeypatch, tmp_path, wmap):
        requests_made = []
        monkeypatch.setattr(
            room_companion, "generate_image", lambda payload: requests_made.append(payload) or b"\xff\xd8\xffart\xff\xd9"
        )
        viewer = room_companion.RoomCompanion(tmp_path, "test-image")
        try:
            room = wmap["Starting Room"]
            viewer.show(room)
            _wait_for(lambda: viewer.snapshot()["status"] == "ready")
            first = viewer.snapshot()
            assert first["image"] == f"/art/{first['key']}.jpg"
            assert (tmp_path / f"{first['key']}.jpg").read_bytes() == b"\xff\xd8\xffart\xff\xd9"
            viewer.show(room)
            room.items.remove(room.items[0])
            viewer.show(room)
            _wait_for(lambda: len(requests_made) == 2 and viewer.snapshot()["status"] == "ready")
            assert "rusty sword" in requests_made[0]["prompt"] and "rusty sword" not in requests_made[1]["prompt"]
            room.items.insert(0, wmap["Starting Room"].items[0])
        finally:
            viewer.close()

    def test_failed_scene_can_be_retried(self, monkeypatch, tmp_path, wmap):
        outcomes = [RuntimeError("Image request failed (HTTP 429)."), b"\xff\xd8\xffart\xff\xd9"]

        def generate(payload):
            outcome = outcomes.pop(0)
            if isinstance(outcome, Exception):
                raise outcome
            return outcome

        monkeypatch.setattr(room_companion, "generate_image", generate)
        viewer = room_companion.RoomCompanion(tmp_path, "test-image")
        try:
            viewer.show(wmap["Corridor"])
            _wait_for(lambda: viewer.snapshot()["status"] == "error")
            assert "429" in viewer.snapshot()["error"]
            assert not viewer.retry("stale-key")
            assert viewer.retry(viewer.snapshot()["key"])
            _wait_for(lambda: viewer.snapshot()["status"] == "ready")
        finally:
            viewer.close()
