# Give the Zammad token the permissions it needs, so writes are authorized.
#
# The token was created with `preferences: {}`. Zammad's `Token::Permissions#permissions?`
# combines the *user's* permissions with the *token's own* permission list, and an empty list
# authorizes nothing except the endpoints that need no specific permission. That is why
# reading tickets worked and creating one returned 403 "Token authorization failed" with a
# perfectly valid token -- the failure is the token's scope, not the user's role.
#
# Run:
#   docker cp scripts/zammad_token_perms.rb <container>:/tmp/
#   docker exec <container> /opt/zammad/contrib/docker/docker-entrypoint.sh \
#     bundle exec rails runner /tmp/zammad_token_perms.rb

token = Token.find_by(name: "netbox-ops")
raise "no netbox-ops token" if token.nil?

# The token needs exactly what the demo does: read and write tickets.
needed = %w[ticket.agent admin]

# Keep only permissions that actually exist, so an unknown name cannot silently narrow it.
valid = Permission.where(name: needed, active: true).pluck(:name)
raise "none of #{needed.inspect} exist as permissions" if valid.empty?

token.preferences[:permission] = valid
token.save!

puts "TOKEN_PERMISSIONS=#{token.preferences[:permission].join(',')}"
puts "TOKEN=#{token.token}"
