from types import SimpleNamespace

import magpie.webui.jobs as jobs
from magpie.webui.jobs import JobManager


class _DummyFuture:
    def add_done_callback(self, _cb) -> None:
        return None


def test_submit_preserves_explicit_empty_inputs(monkeypatch):
    manager = JobManager()
    fake_loop = object()

    monkeypatch.setattr(manager, "_ensure_loop", lambda: fake_loop)

    def fake_run_coroutine_threadsafe(coro, loop):
        assert loop is fake_loop
        coro.close()
        return _DummyFuture()

    monkeypatch.setattr(
        jobs.asyncio,
        "run_coroutine_threadsafe",
        fake_run_coroutine_threadsafe,
    )

    cfg = SimpleNamespace(
        default_endpoint="mac",
        endpoint=lambda _endpoint: SimpleNamespace(model="m"),
    )

    job = manager.submit(cfg, "/photos", None, "", True, inputs=[])
    assert job.inputs == ()
