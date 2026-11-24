# Create a Zammad API token for the NetBox Ops plugin.
#
# Run inside the railsserver container, letting the image's own entrypoint set up the
# environment (it generates config/database.yml from POSTGRESQL_* at start):
#
#   docker cp scripts/zammad_token.rb <container>:/tmp/zammad_token.rb
#   docker exec <container> /opt/zammad/contrib/docker/docker-entrypoint.sh \
#     bundle exec rails runner /tmp/zammad_token.rb
#
# Running `rails runner` directly does not work: the entrypoint generates database.yml from
# the POSTGRESQL_* variables, so a bare runner fails with "No such file - config/database.yml".

user = User.find_by(login: "admin@example.com") || User.first
raise "no user to attach a token to" if user.nil?

existing = Token.find_by(name: "netbox-ops", user: user)
token = existing || Token.create!(name: "netbox-ops", user: user, action: "api", persistent: true)

puts "USER=#{user.login}"
puts "TOKEN=#{token.token}"
