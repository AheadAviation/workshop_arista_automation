"""REST API serializers for netbox_demo (INIT-001/SPEC-002)."""

from netbox.api.serializers import NetBoxModelSerializer
from rest_framework import serializers

from ..models import LabAgent, LabJob, StagedChange


class LabAgentSerializer(NetBoxModelSerializer):
    url = serializers.HyperlinkedIdentityField(view_name="plugins-api:netbox_demo-api:labagent-detail")

    class Meta:
        model = LabAgent
        fields = (
            "id",
            "url",
            "display",
            "name",
            "enabled",
            "last_seen",
            "last_status",
            "last_error",
            "tags",
            "custom_fields",
            "created",
            "last_updated",
        )
        brief_fields = ("id", "url", "display", "name", "enabled", "last_status")


class LabJobSerializer(NetBoxModelSerializer):
    url = serializers.HyperlinkedIdentityField(view_name="plugins-api:netbox_demo-api:labjob-detail")

    class Meta:
        model = LabJob
        fields = (
            "id",
            "url",
            "display",
            "action",
            "status",
            "payload",
            "summary",
            "output",
            "error",
            "created",
            "last_updated",
        )
        brief_fields = ("id", "url", "display", "action", "status", "created")


class StagedChangeSerializer(NetBoxModelSerializer):
    url = serializers.HyperlinkedIdentityField(
        view_name="plugins-api:netbox_demo-api:stagedchange-detail"
    )

    class Meta:
        model = StagedChange
        fields = (
            "id",
            "url",
            "display",
            "change_id",
            "operation",
            "device",
            "values",
            "netbox_plan",
            "eos_commands",
            "created",
        )
        brief_fields = ("id", "url", "display", "change_id", "operation", "device")
