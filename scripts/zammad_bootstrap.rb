# Complete Zammad's first-run setup, idempotently, and emit the credentials NetBox needs.
#
# WHY THIS EXISTS
# ---------------
# Zammad's own auto-wizard is unusable here, for two reasons that are both silent:
#
#   1. `AUTOWIZARD_JSON` must be **base64**, not JSON. The entrypoint does
#      `base64 -d <<< "${AUTOWIZARD_JSON}"` and only writes the file if that succeeds, so plain
#      JSON is skipped without an error. The containers come up healthy, migrations run, and the
#      UI still shows "Set up a new system".
#   2. It only ever runs when the database has no users. So it cannot repair an install that
#      booted once without it -- which is every install after the first.
#
# And even when it does run it leaves `system_init_done` unset, because that flag is set by the
# *web* endpoint the browser calls, not by the library. So the wizard stays on screen.
#
# This script does the same work through Zammad's own models, so it is safe to run on a fresh
# database or on one that is already half-configured, and running it twice changes nothing.
#
# WHAT IT FIXES THAT A NAIVE VERSION GETS WRONG
#   * User id 1 in Zammad is the **system user** (login "-"). Creating a token against
#     `User.first` attaches it to that, so every ticket is opened by a customer whose name is
#     literally "-". The admin must be a real user with a real login, and the token must be
#     owned by *that* user.
#   * `system_init_done` has to be set explicitly, or the setup wizard never goes away.
#   * The token's permissions must be written to `preferences[:permission]`; an empty list
#     authorizes nothing, and a valid token then returns 403 on writes while reads still work.
#
# Run:
#   docker exec <railsserver> /opt/zammad/contrib/docker/docker-entrypoint.sh \
#     bundle exec rails runner /tmp/zammad_bootstrap.rb
#
# Prints one line: ITSM_JSON={...}

ADMIN_LOGIN    = ENV.fetch('ITSM_ADMIN_LOGIN', 'admin@example.com')
ADMIN_PASSWORD = ENV.fetch('ITSM_ADMIN_PASSWORD', 'admin')
ADMIN_FIRST    = ENV.fetch('ITSM_ADMIN_FIRSTNAME', 'Network')
ADMIN_LAST     = ENV.fetch('ITSM_ADMIN_LASTNAME', 'Ops')
ORG_NAME       = ENV.fetch('ITSM_ORG_NAME', 'AHEAD Demo')
PRODUCT_NAME   = ENV.fetch('ITSM_PRODUCT_NAME', 'AHEAD Network Ops')
TOKEN_NAME     = 'netbox-ops'
GROUP_NAME     = ENV.fetch('ITSM_GROUP', 'Users')

# A FIXED token value, not a random one.
#
# Zammad generates a random token on create, which means the value in the plugin's config has
# to be hand-copied after every database wipe -- and if nobody remembers, the Tickets tab shows
# 401s and the fix is invisible. That is exactly how the previous version of this worked.
#
# Pinning the value makes the whole stack reproducible: wipe the volumes, bring it up, and the
# plugin's config is still correct. It is a lab credential that is already committed to this
# repo, so pinning costs nothing; on a real install you would set ITSM_TOKEN from a secret and
# leave `itsm_token` in PLUGINS_CONFIG empty to read it from the environment instead.
TOKEN_VALUE = ENV.fetch('ITSM_TOKEN', '4aroyvDrKCLlDhnA0C1wPppAakjqICFRHqvCcJo1bfTS5JzxyWUW12aggHjfhqTX')

puts '== Zammad bootstrap =='

# Wait for the schema **and the seed** to be usable. This service is intentionally not gated on
# zammad-init completing (that condition is unreliable here -- see docker-compose.itsm.yml), so
# on a fresh database this can start while migrations are still running.
#
# Both checks matter, and learning that cost a second failed run: `db:migrate` creates the tables
# well before `db:seed` creates Zammad's system user, so a schema-only check passes and then the
# setup aborts on a missing user id 1. Waiting on both makes the race go away rather than merely
# narrowing it.
#
# Bounded, because a container that hangs forever is worse than one that fails and says why.
ready = false
90.times do
  begin
    ready = ActiveRecord::Base.connection.table_exists?('users') && User.exists?(1)
    break if ready
  rescue StandardError
    ready = false
  end
  sleep 2
end
raise 'Zammad never finished seeding (no system user after ~3 minutes). Check `zammad-init`.' if !ready
puts 'schema and seed ready'

# Chicken and egg: every row needs an `updated_by_id`, but there is no admin yet on a fresh
# install. Zammad's own AutoWizard resolves this by acting as user id 1 -- the internal system
# user with login "-" -- while it creates the first real user, and only then switching to that
# user. Doing it in the other order raises a not-null violation on `users.updated_by_id`.
#
# The system user is *not* used as the token owner further down; that was the earlier bug.
system_user = User.find(1)
if system_user.nil?
  # Deliberately explicit: this is what happens when the setup races `db:seed`, and a bare
  # nil-check failure reads like a bug in this script rather than a database that is not ready.
  raise 'Zammad has no system user (id 1), so the database seed has not finished. ' \
        'This is a timing problem, not a configuration one: run the setup again.'
end
UserInfo.current_user_id = system_user.id

admin = User.find_by(login: ADMIN_LOGIN)

if admin.nil?
  admin = User.new(
    login:     ADMIN_LOGIN,
    firstname: ADMIN_FIRST,
    lastname:  ADMIN_LAST,
    email:     ADMIN_LOGIN,
    password:  ADMIN_PASSWORD,
    active:    true,
    verified:  true,
  )
  # Zammad validates that a user has at least one role before saving, so roles go on before
  # `save!` runs. The order matters here and is not stylistic.
  admin.roles = Role.where(name: %w[Admin Agent]).to_a
  admin.save!
  puts "created admin #{admin.login} (id #{admin.id})"
else
  puts "admin already exists: #{admin.login} (id #{admin.id})"
end

# From here on, everything is authored by the real admin.
UserInfo.current_user_id = admin.id

admin.update!(active: true, verified: true, firstname: ADMIN_FIRST, lastname: ADMIN_LAST)

# Real roles, not just whatever it happened to have. A user with only `Customer` can log in and
# see nothing, which reads as a broken token rather than a missing role.
wanted_roles = Role.where(name: %w[Admin Agent]).to_a
missing_roles = wanted_roles - admin.roles.to_a
if missing_roles.any?
  admin.roles = (admin.roles.to_a | wanted_roles)
  admin.save!
  puts 'granted roles: ' + missing_roles.map(&:name).join(', ')
end

org = Organization.find_by(name: ORG_NAME) || Organization.create!(name: ORG_NAME)
admin.update!(organization_id: org.id) if admin.organization_id != org.id
puts "organization: #{org.name} (id #{org.id})"

# A group is what a ticket belongs to. Stock Zammad seeds "Users"; if a site renamed it, fall
# back to the first active group rather than failing and leaving the demo with no ticket target.
group = Group.find_by(name: GROUP_NAME) || Group.where(active: true).first
raise 'no active ticket group exists; Zammad\'s seed did not run' if group.nil?
puts "group: #{group.name} (id #{group.id})"

# **Access to the group, which is what actually authorizes a ticket write.**
#
# This was the missing piece that produced `403 Not authorized` on ticket creation while reads
# worked perfectly. A user with the Admin and Agent *roles* still sees no tickets unless it has
# group access: Zammad's authorization is group-scoped, so roles alone grant nothing. The stock
# admin gets this from the setup wizard, which we bypass, so it has to be set explicitly.
#
# `full` is the permission Zammad uses for read/write on a group.
admin.group_names_access_map = { group.name => 'full' }
admin.save!
puts "group access: #{group.name} => full"

UserInfo.current_user_id = admin.id

# The flag the UI actually reads. Without it the setup wizard stays on screen even though the
# admin exists and the API is perfectly usable -- which is exactly what happened here.
Setting.set('system_init_done', true)
Setting.set('product_name', PRODUCT_NAME)

# The two settings that make the *browser* work rather than just the API. Without them Zammad
# believes it is at zammad.example.com over https, so every login attempt fails CSRF
# verification and the UI looks broken even though `rails runner` and the REST API are fine.
# This is the difference between "the tickets tab works" and "I can actually log in".
#
# `fqdn` is a HOST, not a URL: the scheme goes in `http_type` separately. Passing the whole URL
# produces `http://http://localhost:8090` in Zammad's own logs and breaks link generation, so
# the scheme is stripped here rather than assumed absent.
public_url = ENV.fetch('ITSM_PUBLIC_URL', 'http://localhost:8090')
scheme, _, host = public_url.partition('://')
fqdn = host.sub(%r{/+\z}, '')
fqdn = public_url.sub(%r{/+\z}, '') if host.empty?

Setting.set('fqdn', fqdn)
Setting.set('http_type', ENV.fetch('ITSM_HTTP_TYPE', scheme.empty? ? 'http' : scheme))

# API access is denied by default on a stock install. The demo's whole ITSM story is an API
# token, so this has to be on.
Setting.set('api_token_access', true)

puts "system_init_done=true, product_name=#{PRODUCT_NAME}, fqdn=#{fqdn}"

# A token owned by the admin, not by User.first. The value is pinned (see TOKEN_VALUE) so it
# matches the plugin's configuration without anyone copying it by hand.
token = Token.find_by(name: TOKEN_NAME)
if token.nil?
  token = Token.create!(name: TOKEN_NAME, user: admin, action: 'api', persistent: true)
  puts "created token #{TOKEN_NAME}"
else
  # Re-point an existing token at the real admin if an earlier run attached it to the system
  # user, which is how the first version of this went wrong.
  token.user = admin
  token.save!
  puts "reused token #{TOKEN_NAME}"
end

# Force the value with `update_column`, which skips callbacks and validations.
#
# This looks like it should be a plain assignment before `save`, and it is not: `Token` has
# `before_create :generate_token`, which *unconditionally* overwrites whatever was set with a
# fresh `SecureRandom.urlsafe_base64(48)`. Assigning the value on a new record is therefore
# silently discarded — verified by wiping the volumes and watching a random token appear.
# Setting it after the row exists is the only way to pin it.
#
# Why pin at all: a random token means the value in the plugin's config has to be re-copied
# after every database wipe, and if nobody remembers, the Tickets tab answers 401 with no clue
# why. A fixed lab credential makes the whole stack reproducible.
if token.token != TOKEN_VALUE
  token.update_column(:token, TOKEN_VALUE)
  puts "pinned token value"
end

token.reload
if token.token != TOKEN_VALUE
  puts 'WARNING: this Zammad version will not accept a pinned token value.'
  puts "  Put this in PLUGINS_CONFIG['netbox_ops']['itsm_token']: #{token.token}"
end

needed = %w[admin ticket.agent]
valid = Permission.where(name: needed, active: true).pluck(:name)
raise "none of #{needed.inspect} exist as permissions on this install" if valid.empty?

token.preferences[:permission] = valid
token.save!
puts 'token permissions: ' + valid.join(', ')

require 'json'
payload = {
  url:         ENV.fetch('ITSM_PUBLIC_URL', 'http://localhost:8090'),
  token:       token.token,
  customer_id: admin.id,
  group:       group.name,
  admin_login: admin.login,
}
puts 'ITSM_JSON=' + JSON.generate(payload)
puts '== done =='
