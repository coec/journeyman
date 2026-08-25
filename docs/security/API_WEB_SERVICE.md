# API and Web Service Security

Journeyman exposes two authenticated machine-facing HTTP interfaces: the registered-runner control API and the versioned `/api/v1` external automation API used by the supported Ansible collections. The browser-facing application also includes JSON and Server-Sent Events endpoints, but Journeyman does not currently expose GraphQL or WebSocket interfaces. The external API contract is documented in `docs/API.md`.

## Runner API trust model

Runner API endpoints are authenticated with a registered runner identity and bearer secret. Job- and slice-specific operations additionally require the immutable dispatch token associated with the assigned execution unit. Authorization checks bind the requested Job or slice to the authenticated runner before execution data, repository artefacts, control state, output updates, or completion operations are accepted.

The runner API is intentionally exempt from browser CSRF protection because it does not authenticate with browser cookies. This exemption does not remove runner authentication or assignment authorization requirements.

## JSON request structure

Endpoints which consume a request body require `Content-Type: application/json` and require the decoded top-level value to be a JSON object. Form data, plain text, malformed JSON, arrays, and scalar JSON values are rejected instead of being silently interpreted as an empty object.

Field-level validation remains the responsibility of the receiving endpoint or lifecycle service. Unknown or invalid state transitions are rejected rather than inferred from client data.

## HTTP and HTTPS

The packaged application server binds only to loopback and is fronted by the managed Nginx reverse proxy. Production `ProxyFix` configuration trusts exactly one proxy hop. Nginx overwrites the forwarded protocol/host/port headers and appends the actual peer address to `X-Forwarded-For`; callers cannot directly reach the packaged Gunicorn listener over the network.

When HTTP-to-HTTPS redirection is enabled, human-facing browser paths may redirect from HTTP to HTTPS. `/api/` paths do **not** transparently redirect from an insecure first request: the managed HTTP listener returns HTTP 426 instead. Runner configuration independently requires an `https://` Journeyman control-plane URL.

## Methods and response framing

Flask route declarations constrain the HTTP methods accepted by each endpoint, and unsupported methods receive HTTP 405. JSON responses are emitted with the JSON media type and framework-generated response framing. Repository artefacts are served with an explicit gzip media type.

Journeyman relies on Nginx, Gunicorn and the underlying HTTP libraries for low-level HTTP/1.x message-boundary validation. A dedicated request-smuggling integration assessment across the deployed proxy/server stack remains deferred.

## Interfaces not implemented

Journeyman does not currently implement GraphQL or WebSocket. Its live browser updates use Server-Sent Events over ordinary HTTPS rather than WebSocket.

## Deferred higher-level controls

The current API does not provide per-message digital signatures in addition to TLS, runner authentication, dispatch tokens, and authenticated encryption of sensitive execution-data envelopes. The application also does not yet enforce comprehensive maximum lengths for all outbound backend URIs and HTTP header values. These controls remain explicitly deferred in the ASVS matrix rather than being assumed from transport security.
