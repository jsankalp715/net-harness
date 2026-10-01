# Runner image for hosts where containerlab cannot run natively (Docker Desktop on
# Windows/macOS). It is the official containerlab image plus Python + make, so the
# exact same `make test` used in CI runs inside it.
FROM ghcr.io/srl-labs/clab:0.79.0

RUN apk add --no-cache python3 py3-pip bash make iproute2 \
 && python3 --version
