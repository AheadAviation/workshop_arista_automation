"""Access control for the plugin (INIT-002/SPEC-004).

**Why this module exists, in one sentence:** the plugin could be read and driven by an
unauthenticated visitor, because `LOGIN_REQUIRED` was `false` for the original workshop demo,
and under that setting Django treats every anonymous request as a superuser.

That was survivable when the only thing behind the UI was seeded data. It stopped being
survivable when this plugin gained the ability to read every switch's running configuration and
push configuration to real switches. An unauthenticated visitor could do both.

So the plugin does not rely on the global setting. Every view mixes in `OperatorRequiredMixin`,
which refuses an anonymous request regardless of how NetBox is configured, and requires an
authenticated user with the permission the action actually needs. Defence in depth: the global
setting is the outer layer, this is the inner one, and the inner one is the one that keeps
working if somebody flips the outer one back for a different demo.
"""

from __future__ import annotations

from django.contrib.auth.mixins import LoginRequiredMixin, PermissionRequiredMixin


class OperatorRequiredMixin(LoginRequiredMixin, PermissionRequiredMixin):
    """Require a logged-in user, and refuse anyone without the action's permission.

    `raise_exception = True` matters: without it, `PermissionRequiredMixin` redirects a
    logged-in-but-unauthorised user to the login page, which is confusing. A refusal is clearer
    and is what a CISO expects to see.

    The permission is declared per view — reading a config and pushing a config are different
    privileges, and collapsing them would mean everyone who can look can also change.
    """

    raise_exception = True

    #: Reading device state requires view rights on devices; changing them requires change rights.
    permission_required = ("dcim.view_device",)


class ReadOnlyOperatorMixin(OperatorRequiredMixin):
    """For views that only read: the page itself, live configs, tickets, audit."""

    permission_required = ("dcim.view_device",)


class ChangeOperatorMixin(OperatorRequiredMixin):
    """For views that change a device.

    Deliberately stricter than `ReadOnlyOperatorMixin`: pushing configuration to a switch needs
    the same permission NetBox itself requires to modify a device, so the plugin cannot become a
    way around NetBox's own permissions.
    """

    permission_required = ("dcim.change_device",)


__all__ = ["ChangeOperatorMixin", "OperatorRequiredMixin", "ReadOnlyOperatorMixin"]
