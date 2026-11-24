# The ITSM half — Zammad as part of the lab

The demo's ticketing story is real: a change request becomes a ticket in a real ITSM, a human
approves it there, and the ticket moves through `new → open → closed` as the change actually
happens and is verified on the switch. This is the answer to *"are you replacing ServiceNow?"* —
no, and here is the integration working.

**Provenance:** `INIT-002-netbox-ops-plugin`, SPEC-006.

---

## One command

```powershell
.\run.ps1 itsm
```

That is the whole setup. It:

1. starts Zammad with **both** compose files,
2. waits for the database schema to exist,
3. creates the admin, organization, group, API token and settings,
4. writes the token into `docker/netbox/configuration/plugins.py`,
5. restarts NetBox, and
6. proves the token works **from inside the NetBox container**.

Step 6 is the one that matters. Proving it from the host proves the wrong thing — the host can
reach `localhost:8090`, but only NetBox has to resolve `zammad-nginx:8080`.

Log in at <http://localhost:8090> as `admin@example.com` / `admin`.

---

## Why this is more than `docker compose up`

A stock Zammad is not usable by the plugin. Four things are missing, and each one fails
*silently* or in a place far from its cause. All four are documented here because each cost real
time to find.

### 1. `AUTOWIZARD_JSON` must be base64

This is the one that looks like it should solve everything and does not.

```bash
# in Zammad's entrypoint:
if base64 -d <<< "${AUTOWIZARD_JSON}" &>> /dev/null; then
  base64 -d <<< "${AUTOWIZARD_JSON}" > "${AUTOWIZARD_RELATIVE_PATH}"
fi
```

Plain JSON is not valid base64, so the file is never written. No error, no warning: migrations
run, every container reports healthy, and the browser still shows **"Set up a new system"**.

Worse, the wizard only runs when the database has no users at all:

```bash
if ! (bundle exec rails r 'puts User.any?' 2>/dev/null | grep -q true); then
```

So it cannot repair an install that booted once without it — which is every install after the
first. And even on success it does not set `system_init_done`; that is done by the *web* endpoint
the browser calls. The wizard stays on screen regardless.

**So the setup does not use it.** It does the same work against Zammad's own models, where it can
be idempotent and can report what it did.

### 2. The setup must not race the migration

Version one ran setup as a one-shot compose service. On a cold database it could start while
`zammad-init` was still migrating, find no schema, and either fail immediately or — worse —
succeed against a half-built database. The outcome depended on timing.

`docker compose` cannot express "wait until this service's database has tables". A loop can, so
the setup runs from the host against the long-lived railsserver and polls for the schema.

### 3. Roles are not permissions

An admin with `Admin` and `Agent` roles, a valid API token, and correct token permissions still
gets **403 Not authorized** on ticket *creation* while every *read* works perfectly. That
combination reads exactly like a bad token, which is why it wastes time.

The cause is that Zammad's authorization is **group-scoped**. A user with no group access can see
nothing and write nothing, regardless of role. The stock admin gets this from the setup wizard we
bypass, so it has to be set explicitly:

```ruby
admin.group_names_access_map = { group.name => 'full' }
```

### 4. `fqdn` is a host, not a URL

Two separate settings:

| Setting | Value |
| --- | --- |
| `fqdn` | `localhost:8090` |
| `http_type` | `http` |

Passing the whole URL to `fqdn` produces `http://http://localhost:8090` in Zammad's internals and
breaks link generation and login. The scheme belongs in `http_type`.

Related: `api_token_access` must be `true`, or the API is closed to tokens entirely.

---

## Why the token is written back into the repo

Zammad's `Token` model has `before_create :generate_token`, which **unconditionally** overwrites
whatever value you set:

```ruby
def generate_token
  loop do
    self.token = SecureRandom.urlsafe_base64(48)
    break if !Token.exists?(token: token)
  end
  true
end
```

So a token cannot simply be declared. The setup script pins it afterwards with `update_column`
(which skips callbacks), and the runner then *reads the value back out* and writes whatever it
actually finds into `plugins.py`. If a future Zammad changes that callback, the runner reports the
real value instead of assuming — the flow keeps working even if the pinning stops.

That write-back is why a database wipe is no longer a manual repair job.

---

## The change flow, end to end

| Step | Ticket state |
| --- | --- |
| Step 1 — preview the change | — |
| Step 2 — open the ticket | `new` |
| Step 3 — approve by name | → `open` |
| push, verify on the switch | → `closed` |

Closing happens only after the switch is read back and confirmed compliant. Closing on approval
would claim success before there is evidence, which is the exact thing the rest of the flow exists
to avoid.

Select the ticket in step 3's dropdown. Without it the push still works — it just has no ticket to
move, which is a quieter demo than you want.

---

## What is deliberately not here

- **No Elasticsearch.** It is the heaviest component in the stack and search is not what the demo
  shows. `ELASTICSEARCH_ENABLED=false` on every service, or they hang waiting for it.
- **No email.** No inbound or outbound channel is configured. Tickets are created through the API,
  which is the point.
- **No backup sidecar.** Nothing to back up in a lab that is rebuilt by one command.
- **Not ServiceNow.** This is a stand-in that proves the *pattern*. The plugin's ITSM calls go
  through `itsm.py`, so a different system is one module, not a rewrite.

---

## If something breaks

| Symptom | Cause | Fix |
| --- | --- | --- |
| Browser shows "Set up a new system" | setup never ran | `python scripts\zammad_setup.py` |
| Tickets tab: "ITSM is not configured" | NetBox cannot resolve `zammad-nginx` — different networks | `.\run.ps1 itsm` (both files) |
| Tickets tab: 401 | token in `plugins.py` is stale | `python scripts\zammad_setup.py` |
| Ticket creation: 403 | no group access | re-run the setup; it sets `group_names_access_map` |
| Login fails, CSRF error | `fqdn` has a scheme in it | re-run the setup; it sets `fqdn` and `http_type` separately |
| Everything hangs on start | Elasticsearch not disabled | check `ELASTICSEARCH_ENABLED=false` |

Verify without changing anything:

```powershell
python scripts\zammad_setup.py --verify
```

That asks Zammad for the authenticated user **from inside NetBox**, reads ticket states, and
confirms `setup_done` is true.

---

## Where things live

| Path | What |
| --- | --- |
| `scripts/zammad_bootstrap.rb` | The setup itself, run through `rails runner` |
| `scripts/zammad_setup.py` | Waits, runs the bootstrap, writes the token, verifies |
| `docker-compose.itsm.yml` | The Zammad stack |
| `plugins/netbox_ops/netbox_ops/itsm.py` | The plugin's ITSM client |
| `tests/test_zammad_setup.py` | Pins the four silent failures above |
