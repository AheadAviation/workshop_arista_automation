"""Site base-config / services standard emitted as a NetBox config-context.

Shapes match what the workshop's `roles/arista_config` EOS templates consume
(sections/ntp.j2, services.j2, logging.j2). Values are lab-valid EOS - i.e.
they render to syntactically valid Arista config so a strict `configure
replace` / clean boot succeeds:
  * ntp.servers      -> list[str]            (sections/ntp.j2)
  * log_hosts        -> list[{ip,port}]      (sections/logging.j2)
  * dns.servers/domain                        (sections/services.j2)
  * snmp.location + snmp.communities[{name,access}]  (sections/services.j2)

TACACS is intentionally omitted here: the EOS template hard-codes `key 7 ...`,
which requires a real type-7 secret. In a lab we fall back to EOS default local
AAA rather than emit an unresolved `<vault:...>` placeholder that would be
invalid config. A production pipeline would inject a resolved key and re-enable
the TACACS block.
"""

# Stable, content-agnostic name so updating the services shape never creates a
# duplicate config-context (config-contexts are matched/updated by name).
CONTEXT_NAME = "AHEAD Standard Services"

MGMT_VRF = "MGMT"

DEFAULT_BASE_CONFIG = {
    "ntp": {"servers": ["10.0.0.10", "10.0.0.11"], "vrf": MGMT_VRF, "interface": "Management0"},
    "log_hosts": [{"ip": "10.0.0.20", "port": 514}],
    "dns": {"servers": ["10.0.0.40", "10.0.0.41"], "domain": "ahead.local"},
    "snmp": {"location": "AHEAD Lab", "communities": [{"name": "ahead-ro", "access": "ro"}]},
}


def _clean_list(value):
    if isinstance(value, str):
        parts = [p.strip() for p in value.replace("\n", ",").split(",")]
        return [p for p in parts if p]
    if isinstance(value, list):
        return [p for p in value if p]
    return []


def normalize(base_config):
    """Produce a lab-valid services block. Any override the wizard supplies
    (server lists as comma/newline strings or lists) is coerced onto the
    org-standard shape; untouched sections fall back to the standard."""
    bc = base_config or {}
    d = DEFAULT_BASE_CONFIG
    ntp_in, dns_in, snmp_in = bc.get("ntp") or {}, bc.get("dns") or {}, bc.get("snmp") or {}

    ntp_servers = _clean_list(ntp_in.get("servers")) or list(d["ntp"]["servers"])
    dns_servers = _clean_list(dns_in.get("servers")) or list(d["dns"]["servers"])
    ro_community = snmp_in.get("community") or d["snmp"]["communities"][0]["name"]

    return {
        "ntp": {"servers": ntp_servers, "vrf": MGMT_VRF,
                "interface": ntp_in.get("interface") or d["ntp"]["interface"]},
        "log_hosts": [dict(h) for h in d["log_hosts"]],
        "dns": {"servers": dns_servers, "domain": dns_in.get("domain") or d["dns"]["domain"]},
        "snmp": {"location": snmp_in.get("location") or d["snmp"]["location"],
                 "communities": [{"name": ro_community, "access": "ro"}]},
    }
