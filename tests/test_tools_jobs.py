# tests/test_tools_jobs.py
import asyncio
from dataclasses import dataclass

from mcp.server.fastmcp import FastMCP

from tests.conftest import FakeWorkbenchClient
from ttio_mcp.config import Config
from ttio_mcp.connection import ConnectionManager
from ttio_mcp.tools import jobs as jt


@dataclass
class _Job:
    job_id: str
    status: str


@dataclass
class _Evt:
    state: str
    data: dict


class _Jobs:
    def __init__(self):
        self.cancelled = None

    def submit(self, *, pipeline_id, inputs, params=None):
        return _Job("j1", "queued")

    def list(self, *, status_filter=None, limit=None):
        return [_Job("j1", "running")]

    def get(self, job_id):
        return _Job(job_id, "completed")

    def cancel(self, job_id):
        self.cancelled = job_id

    async def events(self, job_id):
        for s in ("queued", "running", "completed"):
            yield _Evt(s, {"job_id": job_id})


class _Pipes:
    def list(self):
        return [_Job("p1", "active")]

    def get(self, pipeline_id):
        return _Job(pipeline_id, "active")


def _app():
    cm = ConnectionManager()
    fc = FakeWorkbenchClient()
    fc.set_subclient("jobs", _Jobs())
    fc.set_subclient("pipelines", _Pipes())
    cm._inject(fc)
    app = FastMCP("t")
    jt.register(app, cm, Config.from_env())
    return app, fc


def _call(app, name, **kw):
    res = app._tool_manager.get_tool(name).fn(**kw)
    return asyncio.run(res) if asyncio.iscoroutine(res) else res


def test_job_submit():
    app, _ = _app()
    out = _call(app, "ttio_job_submit", pipeline_id="p1", inputs={"in": "uri:tio:1"})
    assert out["job_id"] == "j1"


def test_jobs_list():
    app, _ = _app()
    out = _call(app, "ttio_jobs_list")
    assert out["jobs"][0]["status"] == "running"


def test_job_cancel():
    app, _ = _app()
    out = _call(app, "ttio_job_cancel", job_id="j1")
    assert out["cancelled"] == "j1"


def test_job_events_collects():
    app, _ = _app()
    out = _call(app, "ttio_job_events", job_id="j1", max_events=2)
    assert len(out["events"]) == 2


def test_pipelines_list():
    app, _ = _app()
    out = _call(app, "ttio_pipelines_list")
    assert out["pipelines"][0]["job_id"] == "p1"
