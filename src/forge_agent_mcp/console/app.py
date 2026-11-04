"""FastAPI reviewer console: server-rendered Jinja2 with progressive HTMX."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from .models import DECISION_LABELS, DECISION_STATUS_LABELS, CaseDetail
from .service import (
    ConsoleError,
    ConsoleService,
    decision_label,
    decision_status_label,
    recommendation_label,
)

PACKAGE_ROOT = Path(__file__).resolve().parent
TEMPLATES_DIR = PACKAGE_ROOT / "templates"
STATIC_DIR = PACKAGE_ROOT / "static"
CONSOLE_TITLE = "Underwriting review console"


def _human_time(value: str | None) -> str:
    if not value:
        return ""
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return value
    return parsed.strftime("%H:%M:%S")


def _short(value: str | None, length: int = 12) -> str:
    if not value:
        return ""
    return value if len(value) <= length else f"{value[:length]}…"


def create_console_app(service: ConsoleService) -> FastAPI:
    app = FastAPI(title=CONSOLE_TITLE)
    templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
    templates.env.filters["human_time"] = _human_time
    templates.env.filters["short"] = _short
    templates.env.filters["decision_label"] = decision_label
    templates.env.filters["decision_status"] = decision_status_label
    templates.env.filters["recommendation_label"] = recommendation_label
    templates.env.globals.update(
        DECISION_LABELS=DECISION_LABELS,
        DECISION_STATUS_LABELS=DECISION_STATUS_LABELS,
        console_title=CONSOLE_TITLE,
    )
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

    def _context(request: Request, detail: CaseDetail, **extra: Any) -> dict[str, Any]:
        return {
            "request": request,
            "detail": detail,
            "queue": service.queue(),
            "default_approver": service.default_approver,
            "recommended_action": detail.recommended_action,
            "error": None,
            **extra,
        }

    def _render(
        request: Request,
        name: str,
        context: dict[str, Any],
        *,
        status_code: int = 200,
    ) -> HTMLResponse:
        return templates.TemplateResponse(
            request=request,
            name=name,
            context=context,
            status_code=status_code,
        )

    def _detail_or_error(
        request: Request,
        case_id: str,
        *,
        error: str | None = None,
        status_code: int = 200,
    ) -> HTMLResponse:
        try:
            detail = service.detail(case_id)
        except ConsoleError as exc:
            return _render(
                request,
                "error.html",
                {
                    "request": request,
                    "console_title": CONSOLE_TITLE,
                    "error": exc.message,
                    "case_id": case_id,
                },
                status_code=exc.status_code,
            )
        return _render(
            request,
            "case_detail.html",
            _context(request, detail, error=error),
            status_code=status_code,
        )

    @app.get("/", response_class=HTMLResponse)
    def index(request: Request) -> HTMLResponse:
        try:
            queue = service.queue()
        except Exception as exc:  # noqa: BLE001 - render the failure, never a stack trace
            return _render(
                request,
                "error.html",
                {
                    "request": request,
                    "console_title": CONSOLE_TITLE,
                    "error": str(exc),
                    "case_id": None,
                },
                status_code=502,
            )
        return _render(
            request,
            "index.html",
            {
                "request": request,
                "console_title": CONSOLE_TITLE,
                "queue": queue,
            },
        )

    @app.get("/cases/{case_id}", response_class=HTMLResponse)
    def case_detail(request: Request, case_id: str) -> HTMLResponse:
        return _detail_or_error(request, case_id)

    @app.get("/cases/{case_id}/audit", response_class=HTMLResponse)
    def case_audit(request: Request, case_id: str) -> HTMLResponse:
        try:
            detail = service.detail(case_id)
        except ConsoleError as exc:
            return _render(
                request,
                "error.html",
                {
                    "request": request,
                    "console_title": CONSOLE_TITLE,
                    "error": exc.message,
                    "case_id": case_id,
                },
                status_code=exc.status_code,
            )
        return _render(
            request,
            "_audit.html",
            {"request": request, "detail": detail},
        )

    @app.post("/cases/{case_id}/run")
    def run_case(request: Request, case_id: str) -> Any:
        try:
            service.run(case_id)
        except ConsoleError as exc:
            if exc.status_code == 404:
                return _detail_or_error(request, case_id)
            return _detail_or_error(
                request,
                case_id,
                error=exc.message,
                status_code=exc.status_code,
            )
        return RedirectResponse(url=f"/cases/{case_id}", status_code=303)

    @app.post("/cases/{case_id}/decision")
    def decide_case(
        request: Request,
        case_id: str,
        action: str = Form(...),
        approver: str = Form(""),
        note: str = Form(""),
        conditions: str = Form(""),
    ) -> Any:
        parsed_conditions = [line.strip() for line in conditions.splitlines() if line.strip()]
        try:
            service.decide(
                case_id,
                action=action,
                approver=approver,
                note=note,
                conditions=parsed_conditions,
            )
        except ConsoleError as exc:
            return _detail_or_error(
                request,
                case_id,
                error=exc.message,
                status_code=exc.status_code,
            )
        return RedirectResponse(url=f"/cases/{case_id}", status_code=303)

    @app.get("/health")
    def health() -> dict[str, bool]:
        return {"ok": True}

    return app


__all__ = ["CONSOLE_TITLE", "create_console_app"]
