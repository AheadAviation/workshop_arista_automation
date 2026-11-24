# Make the demo user a full ticket agent, so the plugin can create and read tickets.
#
# Read (`GET /api/v1/tickets`) and write (`POST`) are authorized separately in Zammad: a user
# with the Admin role but no `ticket.agent` permission can list tickets and is refused
# "Token authorization failed" when creating one. The token is valid; only the write is denied.
#
# Group access lives in the `roles_groups` table. Zammad does not expose a top-level
# `RolesGroups` model (that raises NameError), so the row is written through the association
# Zammad does have: `role.groups` with `access`.
#
# Run:
#   docker cp scripts/zammad_agent.rb <container>:/tmp/
#   docker exec <container> /opt/zammad/contrib/docker/docker-entrypoint.sh \
#     bundle exec rails runner /tmp/zammad_agent.rb

user = User.find_by(login: "admin@example.com") || User.first
raise "no user" if user.nil?

agent = Role.find_by(name: "Agent")
raise "no Agent role in this Zammad" if agent.nil?

unless user.roles.include?(agent)
  user.roles << agent
  user.save!
end

group = Group.find_by(name: "Users") || Group.first
if group
  # `roles_groups` is a composite-key join table (role_id, group_id, access) with no `id` and
  # no unique constraint, so neither `UPDATE ... WHERE id=` nor `ON CONFLICT` works. Delete
  # then insert is unambiguous and idempotent.
  conn = ActiveRecord::Base.connection
  conn.execute(
    "DELETE FROM roles_groups WHERE role_id=#{agent.id.to_i} AND group_id=#{group.id.to_i}"
  )
  conn.execute(
    "INSERT INTO roles_groups (role_id, group_id, access) " \
    "VALUES (#{agent.id.to_i}, #{group.id.to_i}, 'full')"
  )
end

user.reload
puts "user=#{user.login} active=#{user.active} roles=#{user.roles.map(&:name).join(',')}"
puts "group=#{group&.name}"
puts "TOKEN=#{Token.find_by(name: 'netbox-ops', user: user)&.token}"
