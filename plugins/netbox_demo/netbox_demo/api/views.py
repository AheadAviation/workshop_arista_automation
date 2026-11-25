"""REST API views for netbox_demo (INIT-001/SPEC-002, INIT-035/SPEC-003).

Two kinds of endpoint:

* Model CRUD through `NetBoxModelViewSet`, which inherits NetBox's token auth and
  object permissions.
* Named actions, each mapped to a fixed lab-agent action, seeder operation, or
  demo-scoped NetBox Assurance operation. Every mutating endpoint requires
  authentication and write permission.
"""

from __future__ import annotations

from rest_framework import status
from rest_framework.permissions import BasePermission, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from netbox.api.viewsets import NetBoxModelViewSet

from .. import assurance, conf, day2, design, intent, lab, renderer, seeder
from ..models import LabAgent, LabJob, StagedChange
from .serializers import LabAgentSerializer, LabJobSerializer, StagedChangeSerializer


class CanOperateDemo(BasePermission):
    """Require authenticated user with device-change permission (or superuser/staff)."""

    message = "You must be staff or hold dcim.change_device permission to run demo mutations."

    def has_permission(self, request, view) -> bool:
        user = getattr(request, "user", None)
        if not user or not user.is_authenticated:
            return False
        return bool(
            getattr(user, "is_superuser", False)
            or getattr(user, "is_staff", False)
            or user.has_perm("dcim.change_device")
        )


class LabAgentViewSet(NetBoxModelViewSet):
    queryset = LabAgent.objects.all()
    serializer_class = LabAgentSerializer


class LabJobViewSet(NetBoxModelViewSet):
    queryset = LabJob.objects.all()
    serializer_class = LabJobSerializer


class StagedChangeViewSet(NetBoxModelViewSet):
    queryset = StagedChange.objects.all()
    serializer_class = StagedChangeSerializer


class HealthView(APIView):
    """Reports whether this plugin can actually reach a lab, and why not."""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        configured, problem = conf.is_configured()
        health = lab.health_or_none() if configured else None
        return Response(
            {
                "plugin": "netbox_demo",
                "configured": configured,
                "config_problem": problem,
                "lab_agent_url": conf.lab_agent_url(),
                "lab_reachable": health is not None,
                "lab": health or {},
                "devices_expected": list(design.ALL_DEVICES),
                "devices_documented": [d.name for d in intent.demo_devices()],
                "assurance": assurance.summary(),
            },
            status=status.HTTP_200_OK if configured else status.HTTP_503_SERVICE_UNAVAILABLE,
        )


class FabricIntentView(APIView):
    """The rendered configuration for every device, or for one with `?device=`."""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        device = request.query_params.get("device", "")
        missing = intent.missing_devices()
        if missing:
            return Response(
                {
                    "error": "not every device is documented in NetBox yet",
                    "missing": missing,
                },
                status=status.HTTP_409_CONFLICT,
            )

        configs = renderer.render_all(intent.intent_for_all(), **intent.render_kwargs())
        problems = renderer.sanity_check(configs)
        if device:
            if device not in configs:
                return Response(
                    {"error": f"unknown device {device!r}", "devices": sorted(configs)},
                    status=status.HTTP_404_NOT_FOUND,
                )
            return Response(
                {"device": device, "config": configs[device], "problems": problems}
            )
        return Response({"devices": sorted(configs), "configs": configs, "problems": problems})


class DriftView(APIView):
    """Live drift for one device: rendered intent compared with `show running-config`."""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        device = request.query_params.get("device", "")
        if device not in design.ALL_DEVICES:
            return Response(
                {"error": "device is required", "devices": list(design.ALL_DEVICES)},
                status=status.HTTP_400_BAD_REQUEST,
            )
        try:
            live = lab.eapi_show(device, list(day2.DRIFT_READ_COMMANDS))
        except lab.LabAgentError as exc:
            return Response({"error": str(exc)}, status=status.HTTP_503_SERVICE_UNAVAILABLE)

        rows = live.get("result") or []
        running = ""
        if rows and isinstance(rows, list) and isinstance(rows[0], dict):
            running = rows[0].get("output", "")

        rendered = renderer.render_device(intent.intent_for(device), **intent.render_kwargs())
        items = day2.detect_drift(device, rendered, running or "")
        return Response(
            {
                "device": device,
                "drift": [item.__dict__ for item in items],
                "summary": day2.summarize_drift(items),
                "assurance": assurance.summary(),
            }
        )


class AssuranceSummaryView(APIView):
    """Read live Assurance deviation summary and demo-scoped open deviations."""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        states_param = request.query_params.get("states", "open")
        states = [s.strip() for s in states_param.split(",") if s.strip()]
        try:
            limit = min(500, max(1, int(request.query_params.get("limit", "100"))))
        except ValueError:
            limit = 100
        return Response(
            {
                "summary": assurance.summary(),
                "deviations": assurance.list_deviations(states=states, limit=limit, demo_only=True),
            }
        )


class AssuranceActionView(APIView):
    """Demo-scoped NetBox Assurance actions (`apply`, `rediff`, `ignore`)."""

    permission_classes = [IsAuthenticated, CanOperateDemo]
    ALLOWED = {"apply", "rediff", "ignore"}

    def post(self, request, action: str):
        if action not in self.ALLOWED:
            return Response(
                {"error": f"unknown assurance action {action!r}", "allowed": sorted(self.ALLOWED)},
                status=status.HTTP_400_BAD_REQUEST,
            )
        body = request.data if isinstance(request.data, dict) else {}
        ids = body.get("ids")
        if ids is not None and not isinstance(ids, list):
            return Response({"error": "ids must be a list of deviation IDs"}, status=status.HTTP_400_BAD_REQUEST)
        branch_id = body.get("branch_id") or None

        job = LabJob.objects.create(
            agent=LabAgent.objects.filter(enabled=True).first(),
            action=f"assurance-{action}",
            status=LabJob.Status.RUNNING,
            payload={"ids": ids, "branch_id": branch_id},
        )
        handlers = {
            "apply": assurance.apply_deviations,
            "rediff": assurance.rediff_deviations,
            "ignore": assurance.ignore_deviations,
        }
        result = handlers[action](ids, branch_id=branch_id, demo_only=True)
        if not result.get("ok"):
            job.status = LabJob.Status.FAILED
            job.error = str(result.get("error", "assurance action failed"))
            job.save(update_fields=["status", "error"])
            http_status = (
                status.HTTP_403_FORBIDDEN if result.get("rejected") else status.HTTP_502_BAD_GATEWAY
            )
            return Response({"job": job.pk, **result}, status=http_status)

        job.status = LabJob.Status.COMPLETED
        job.output = f"Assurance {action}: {result.get('count', 0)} demo deviation(s)"
        job.summary = {"count": result.get("count", 0)}
        job.save(update_fields=["status", "output", "summary"])
        return Response({"job": job.pk, "status": job.status, **result})


class LabActionView(APIView):
    """Invoke one allowlisted lab action or NetBox seed, recording a `LabJob`."""

    permission_classes = [IsAuthenticated, CanOperateDemo]

    ALLOWED = set(lab.AGENT_ACTIONS) | {"seed", "clean"}

    def post(self, request, action: str):
        if action not in self.ALLOWED:
            return Response(
                {"error": f"unknown action {action!r}", "allowed": sorted(self.ALLOWED)},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if action == "seed":
            job = LabJob.objects.create(
                agent=LabAgent.objects.filter(enabled=True).first(),
                action="seed",
                status=LabJob.Status.RUNNING,
                payload={},
            )
            try:
                summary = seeder.seed_all()
            except Exception as exc:  # noqa: BLE001
                job.status = LabJob.Status.FAILED
                job.error = str(exc)
                job.save(update_fields=["status", "error"])
                return Response({"job": job.pk, "error": str(exc)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
            job.status = LabJob.Status.COMPLETED
            job.output = (
                f"Seeded {len(summary['devices'])} devices, {len(summary['vlans'])} VLANs, "
                f"{summary['cables']} cables."
            )
            job.summary = summary
            job.save(update_fields=["status", "output", "summary"])
            return Response({"job": job.pk, "status": job.status, "result": summary})

        if action == "clean":
            job = LabJob.objects.create(
                agent=LabAgent.objects.filter(enabled=True).first(),
                action="clean",
                status=LabJob.Status.RUNNING,
                payload={},
            )
            removed = seeder.clean_demo_data()
            job.status = LabJob.Status.COMPLETED
            job.output = ", ".join(f"{k}: {v}" for k, v in removed.items())
            job.summary = removed
            job.save(update_fields=["status", "output", "summary"])
            return Response({"job": job.pk, "status": job.status, "result": removed})

        payload = dict(request.data) if isinstance(request.data, dict) else {}
        if action == "push-configs" and not payload.get("configs"):
            missing = intent.missing_devices()
            if missing:
                return Response(
                    {"error": "not every device is documented in NetBox yet", "missing": missing},
                    status=status.HTTP_409_CONFLICT,
                )
            configs = renderer.render_all(intent.intent_for_all(), **intent.render_kwargs())
            problems = renderer.sanity_check(configs)
            if problems:
                return Response(
                    {"error": "refusing to push invalid configs", "problems": problems},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            payload["configs"] = configs

        job = LabJob.objects.create(
            agent=LabAgent.objects.filter(enabled=True).first(),
            action=action,
            status=LabJob.Status.RUNNING,
            payload={"devices": sorted(payload["configs"])} if action == "push-configs" else payload,
        )
        try:
            result = lab.run_action(action, payload)
        except lab.LabAgentError as exc:
            job.status = LabJob.Status.FAILED
            job.error = str(exc)
            job.save(update_fields=["status", "error"])
            return Response(
                {"job": job.pk, "error": str(exc)}, status=status.HTTP_502_BAD_GATEWAY
            )

        job.status = LabJob.Status.COMPLETED
        job.output = str(result.get("output", ""))[:20000]
        job.save(update_fields=["status", "output"])
        return Response({"job": job.pk, "status": job.status, "result": result})
