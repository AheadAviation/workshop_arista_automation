"""Make the topology view show the fabric by default (run by netbox-bootstrap).

**The problem, established by reading the plugin's own code:**

`netbox_topology_views` keeps its defaults in a single `IndividualOptions` row. Out of the box
that row has `show_unconnected=False` and `show_cables=False`, and the consequences are not
obvious:

* `show_unconnected=False` means a device becomes a node **only if it appears at the end of a
  cable** — so `get_topology_data` can return `{"nodes": [], "edges": []}` for a fabric whose
  cables are all present and correct.
* `draw_init=False` in the URL means the server generates **no data at all**: the plugin expects
  the browser to fetch the graph afterwards. Combined with the defaults above, the page looks
  completely empty.

So the page was not broken — it was correctly showing nothing, because nothing asked it to show
anything. Setting these defaults is what makes it useful on first open.

Idempotent, so it runs safely from the bootstrap on every `docker compose up`.
"""

from __future__ import annotations


def configure() -> None:
    from netbox_topology_views.models import IndividualOptions

    options, _created = IndividualOptions.objects.get_or_create(id=1)

    # Show every device, connected or not, and draw the cable runs between them. These two are
    # the difference between an empty canvas and the fabric.
    options.show_unconnected = True
    options.show_cables = True

    # Neighbours add protocol-level links; useful, and it makes spines/leaves show their
    # addressing rather than just a line.
    options.show_neighbors = True

    # Coordinates are saved when devices are dragged, so a layout survives a reload.
    options.save_coords = True

    options.save()

    print(
        "Topology defaults: show_unconnected=True, show_cables=True, "
        "show_neighbors=True, save_coords=True"
    )


configure()
