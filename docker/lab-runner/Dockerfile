FROM docker:27-cli

# A tiny HTTP wrapper around `containerlab deploy|destroy`, so the NetBox plugin can start and
# stop the lab without NetBox itself ever holding Docker access.
#
# Why a separate service rather than giving NetBox the socket: the entire security posture of
# this plugin is that NetBox cannot touch Docker beyond a read-only proxy. This container is the
# one thing that holds the socket, it exposes exactly two verbs, and it is on the internal
# network only. That is a far smaller surface than "NetBox can run anything".
#
# It shells out to `docker compose run clab containerlab ...` — the same path `run.ps1` uses —
# so containerlab still runs inside the repo's own privileged clab runner and this service
# gains no networking privileges of its own.

RUN apk add --no-cache python3

WORKDIR /runner
COPY runner.py /runner/runner.py

# Bounded on purpose: deploy and destroy can take minutes, and an unbounded request would hang
# a page. The plugin streams its own progress separately.
ENV REQUEST_TIMEOUT=900

EXPOSE 8077
CMD ["python3", "/runner/runner.py"]
