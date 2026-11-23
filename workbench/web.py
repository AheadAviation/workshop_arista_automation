"""The workbench web app: one localhost page for the briefing (INIT-001/SPEC-008).

Deliberately small. The audience is a CIO, a CISO, a CTO and a network director in a
90-minute meeting, and Brian Nelson's brief was explicit: a GUI front end, staying away
from the CLI. So this serves one page, a handful of JSON endpoints, and nothing else — no
build step, no npm, no framework, nothing to break the night before.

Run it:

    python -m workbench.web

Then open http://127.0.0.1:8099

**Bound to loopback on purpose.** This drives real switches and writes a real audit log;
it is not something to leave reachable on a conference network.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Callable, Mapping

# Imported at module level on purpose. With `from __future__ import annotations`, FastAPI
# resolves route annotations from the module's globals; importing `Request` inside
# `create_app` would leave it unresolvable, and FastAPI would silently treat `request` as a
# query parameter instead of the request object.
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse

from workbench.gate import AuditLog
from workbench.scenarios import (
    DEMO_DEVICES,
    Lab,
    approve,
    audit_view,
    fabric_overview,
    inject_drift,
    lifecycle,
    propose_vlan,
    remediate,
    two_vendors,
)

STATIC = Path(__file__).resolve().parent / "static"

#: Loopback by default. `WORKBENCH_BIND` exists for a VM demo, and is documented as a
#: deliberate choice rather than a convenience.
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8099


# --------------------------------------------------------------------------- #
# The API, as plain functions so it is testable without a server
# --------------------------------------------------------------------------- #


def api_overview(lab: Lab, new_vlan: int | None = None) -> dict[str, Any]:
    return fabric_overview(lab, new_vlan=new_vlan)


def api_propose(lab: Lab, device: str, vlan: int | None = None) -> dict[str, Any]:
    kwargs = {"vlan": vlan} if vlan is not None else {}
    return propose_vlan(lab, device, **kwargs)


def api_approve(lab: Lab, device: str, approver: str, vlan: int | None = None) -> dict[str, Any]:
    kwargs = {"vlan": vlan} if vlan is not None else {}
    return approve(lab, device, approver=approver, **kwargs)


def api_inject(lab: Lab, device: str, vlan: int | None = None) -> dict[str, Any]:
    return inject_drift(lab, device, vlan=vlan)


def api_remediate(lab: Lab, device: str, approver: str) -> dict[str, Any]:
    return remediate(lab, device, approver=approver)


def api_two_vendors(lab: Lab) -> dict[str, Any]:
    return two_vendors(lab)


def api_lifecycle(lab: Lab) -> dict[str, Any]:
    return lifecycle(lab)


def api_audit(lab: Lab, limit: int = 50) -> dict[str, Any]:
    return audit_view(lab, limit=limit)


def api_devices() -> dict[str, Any]:
    return {"ok": True, "devices": list(DEMO_DEVICES)}


def create_app(lab_factory: Callable[[], Lab] | None = None) -> Any:
    """The FastAPI app.

    `lab_factory` is injectable so the route layer can be tested with the fake lab and no
    NetBox, no switches and no docker.
    """
    factory = lab_factory or Lab.real
    app = FastAPI(title="NetBox Automation Workbench", docs_url=None, redoc_url=None)

    def lab() -> Lab:
        try:
            return factory()
        except Exception as exc:  # a broken lab is a page state, not a 500
            raise HTTPException(status_code=503, detail=str(exc)) from exc

    @app.get("/")
    def index() -> FileResponse:
        return FileResponse(STATIC / "index.html")

    @app.get("/static/{name}")
    def static_file(name: str) -> FileResponse:
        # Only a fixed allow-list: never build a path from user input.
        allowed = {"workbench.css", "workbench.js"}
        if name not in allowed:
            raise HTTPException(status_code=404, detail="not found")
        return FileResponse(STATIC / name)

    @app.get("/api/devices")
    def devices() -> JSONResponse:
        return JSONResponse(api_devices())

    @app.get("/api/overview")
    def overview(new_vlan: int | None = None) -> JSONResponse:
        return JSONResponse(_guarded(api_overview, lab(), new_vlan))

    @app.post("/api/propose")
    async def propose(request: Request) -> JSONResponse:
        body = await _body(request)
        return JSONResponse(_guarded(api_propose, lab(), _device(body), body.get("vlan")))

    @app.post("/api/approve")
    async def approve_change(request: Request) -> JSONResponse:
        body = await _body(request)
        approver = str(body.get("approver") or "").strip()
        if not approver:
            return JSONResponse(
                {"ok": False, "headline": "An approver is required",
                 "detail": "no change is applied without a named person on the record"},
                status_code=400,
            )
        return JSONResponse(
            _guarded(api_approve, lab(), _device(body), approver, body.get("vlan"))
        )

    @app.post("/api/inject")
    async def inject(request: Request) -> JSONResponse:
        body = await _body(request)
        return JSONResponse(_guarded(api_inject, lab(), _device(body), body.get("vlan")))

    @app.post("/api/remediate")
    async def remediate_drift(request: Request) -> JSONResponse:
        body = await _body(request)
        approver = str(body.get("approver") or "").strip()
        if not approver:
            return JSONResponse(
                {"ok": False, "headline": "An approver is required",
                 "detail": "no change is applied without a named person on the record"},
                status_code=400,
            )
        return JSONResponse(_guarded(api_remediate, lab(), _device(body), approver))

    @app.get("/api/lifecycle")
    def lifecycle_view() -> JSONResponse:
        return JSONResponse(_guarded(api_lifecycle, lab()))

    @app.get("/api/two-vendors")
    def two_vendor_view() -> JSONResponse:
        return JSONResponse(_guarded(api_two_vendors, lab()))

    @app.get("/api/audit")
    def audit(limit: int = 50) -> JSONResponse:
        return JSONResponse(_guarded(api_audit, lab(), limit))

    @app.get("/api/health")
    def health() -> dict[str, Any]:
        return {"ok": True, "audit": str(AuditLog().path)}

    return app


def _guarded(fn: Callable[..., dict[str, Any]], *args: Any) -> dict[str, Any]:
    """Run an API function, turning any escape into a page-shaped error.

    A demo must never show a stack trace. If something unexpected breaks, the page gets
    a result it knows how to render, with the real reason in `detail` for the presenter.
    """
    try:
        return fn(*args)
    except Exception as exc:
        return {"ok": False, "headline": "Something went wrong", "detail": f"{type(exc).__name__}: {exc}"}


async def _body(request: Any) -> dict[str, Any]:
    try:
        payload = await request.json()
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def _device(body: Mapping[str, Any]) -> str:
    device = str(body.get("device") or "").strip()
    if device not in DEMO_DEVICES:
        raise HTTPException(
            status_code=400,
            detail=f"device must be one of {', '.join(DEMO_DEVICES)}",
        )
    return device


# --------------------------------------------------------------------------- #
# The server
# --------------------------------------------------------------------------- #


def main() -> int:
    import uvicorn

    host = os.environ.get("WORKBENCH_BIND", DEFAULT_HOST)
    port = int(os.environ.get("WORKBENCH_PORT", str(DEFAULT_PORT)))
    print(f"NetBox Automation Workbench on http://{host}:{port}")
    if host != DEFAULT_HOST:
        print(
            "  NOTE: bound to a non-loopback address on purpose. This page drives real "
            "switches and writes a real audit log; do not leave it reachable on a shared "
            "network."
        )
    uvicorn.run(create_app(), host=host, port=port, log_level="warning")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
