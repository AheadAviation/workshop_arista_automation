"""JSON API for the portal UI, mounted at /api/plugins/ztp/.

Plain Django views (not DRF) returning JsonResponse - the UI is same-origin and
authenticated via the NetBox session, so these require an authenticated user and
are CSRF-exempt for the JS fetch() calls. All NetBox writes go through the
framework-agnostic functions in siteplan.py / client.py.
"""
import json

from django.conf import settings
from django.http import JsonResponse
from django.utils.decorators import method_decorator
from django.views import View
from django.views.decorators.csrf import csrf_exempt

from .. import client
from .. import conf as config
from .. import siteplan
from .. import tracking


def _json_body(request):
    if not request.body:
        return {}
    try:
        return json.loads(request.body)
    except ValueError:
        return None


def _auth_error(request):
    # In an open lab (LOGIN_REQUIRED=False) the whole NetBox instance is
    # browsable without a session, so don't gate the portal API more tightly
    # than NetBox itself. When LOGIN_REQUIRED is on, require an authenticated user.
    if request.user.is_authenticated or not getattr(settings, "LOGIN_REQUIRED", True):
        return None
    return JsonResponse({"ok": False, "error": "authentication required"}, status=403)


@method_decorator(csrf_exempt, name="dispatch")
class HealthView(View):
    def get(self, request):
        if not config.netbox_configured():
            return JsonResponse({"ok": False, "netbox_configured": False,
                                 "error": "netbox_url/netbox_token not set"}, status=500)
        try:
            status = client.get_status()
            return JsonResponse({
                "ok": True, "netbox_configured": True,
                "netbox_version": status.get("netbox-version"),
                "use_mock_infoblox": config.use_mock_infoblox(),
                "self_portal_tag": config.self_portal_tag(),
                "containerlab_tag": config.containerlab_tag(),
            })
        except client.NetBoxError as e:
            return JsonResponse({"ok": False, "error": e.to_dict()}, status=502)


@method_decorator(csrf_exempt, name="dispatch")
class SitesView(View):
    def get(self, request):
        err = _auth_error(request)
        if err:
            return err
        try:
            sites = client.list_sites_by_tag(config.self_portal_tag())
            return JsonResponse({"ok": True, "sites": [
                {"id": s["id"], "name": s["name"], "slug": s["slug"],
                 "description": s.get("description", ""),
                 "url": f"{config.netbox_url()}/dcim/sites/{s['id']}/"}
                for s in sites
            ]})
        except client.NetBoxError as e:
            return JsonResponse({"ok": False, "error": e.to_dict()}, status=502)

    def post(self, request):
        err = _auth_error(request)
        if err:
            return err
        plan = _json_body(request)
        if plan is None:
            return JsonResponse({"ok": False, "error": "body must be JSON"}, status=400)
        try:
            result = siteplan.push_site_plan(plan)
            return JsonResponse(result, status=(200 if result.get("ok") else 502))
        except client.NetBoxError as e:
            return JsonResponse({"ok": False, "error": e.to_dict()}, status=502)
        except (ValueError, KeyError) as e:
            return JsonResponse({"ok": False, "error": str(e)}, status=400)


@method_decorator(csrf_exempt, name="dispatch")
class PreviewView(View):
    def post(self, request):
        err = _auth_error(request)
        if err:
            return err
        plan = _json_body(request)
        if plan is None:
            return JsonResponse({"ok": False, "error": "body must be JSON"}, status=400)
        try:
            return JsonResponse(siteplan.preview_site_plan(plan))
        except client.NetBoxError as e:
            return JsonResponse({"ok": False, "error": e.to_dict()}, status=502)
        except (ValueError, KeyError) as e:
            return JsonResponse({"ok": False, "error": str(e)}, status=400)


@method_decorator(csrf_exempt, name="dispatch")
class TrackingView(View):
    def get(self, request):
        err = _auth_error(request)
        if err:
            return err
        try:
            return JsonResponse({"ok": True, **tracking.build_tracking()})
        except client.NetBoxError as e:
            return JsonResponse({"ok": False, "error": e.to_dict()}, status=502)


DEPLOY_CF = "ztp_deploy_status"
DEPLOY_MSG_CF = "ztp_deploy_message"


def _ensure_deploy_fields():
    client.get_or_create_custom_field(DEPLOY_CF, type="text", label="ZTP Deploy Status",
                                      object_types=["dcim.site"])
    client.get_or_create_custom_field(DEPLOY_MSG_CF, type="text", label="ZTP Deploy Message",
                                      object_types=["dcim.site"])


@method_decorator(csrf_exempt, name="dispatch")
class DeployView(View):
    """GET: deploy status of every portal fabric. POST {site_id}: request a
    ContainerLab deploy (the host-side ztp_agent picks it up)."""
    def get(self, request):
        err = _auth_error(request)
        if err:
            return err
        try:
            sites = client.list_sites_by_tag(config.self_portal_tag())
            return JsonResponse({"ok": True, "fabrics": [
                {"id": s["id"], "name": s["name"],
                 "status": (s.get("custom_fields") or {}).get(DEPLOY_CF) or "not-deployed",
                 "message": (s.get("custom_fields") or {}).get(DEPLOY_MSG_CF) or "",
                 "url": f"{config.netbox_url()}/dcim/sites/{s['id']}/"}
                for s in sites]})
        except client.NetBoxError as e:
            return JsonResponse({"ok": False, "error": e.to_dict()}, status=502)

    def post(self, request):
        err = _auth_error(request)
        if err:
            return err
        body = _json_body(request) or {}
        site_id = body.get("site_id")
        if not site_id:
            return JsonResponse({"ok": False, "error": "site_id required"}, status=400)
        try:
            _ensure_deploy_fields()
            client.patch(f"dcim/sites/{site_id}/",
                         {"custom_fields": {DEPLOY_CF: "requested", DEPLOY_MSG_CF: "Queued for the ZTP agent"}})
            return JsonResponse({"ok": True, "status": "requested"})
        except client.NetBoxError as e:
            return JsonResponse({"ok": False, "error": e.to_dict()}, status=502)


@method_decorator(csrf_exempt, name="dispatch")
class DevicesView(View):
    """Per-device ZTP pipeline state, optionally filtered to one site."""
    def get(self, request):
        err = _auth_error(request)
        if err:
            return err
        site_id = request.GET.get("site_id")
        try:
            devices = client.list_by_tag("dcim/devices/", config.self_portal_tag())
            rows = []
            for d in devices:
                site = d.get("site") or {}
                if site_id and str(site.get("id")) != str(site_id):
                    continue
                status = d.get("status") or {}
                rows.append({
                    "id": d["id"], "name": d["name"],
                    "role": (d.get("role") or {}).get("slug"),
                    "site": site.get("name"),
                    "status": status.get("value") if isinstance(status, dict) else status,
                    "stage": tracking._stage(d),
                    "mgmt_ip": (d.get("primary_ip4") or {}).get("address"),
                    "url": f"{config.netbox_url()}/dcim/devices/{d['id']}/",
                })
            rows.sort(key=lambda r: r["name"])
            return JsonResponse({"ok": True, "devices": rows})
        except client.NetBoxError as e:
            return JsonResponse({"ok": False, "error": e.to_dict()}, status=502)
