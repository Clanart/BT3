### Title
EVM JSON-RPC HTTP server hardcodes `Vhosts: []string{"*"}`, permanently disabling the Host-header (DNS-rebinding) defense - ([File: evmrpc/server.go])

### Summary
The evmrpc HTTP handler stack ports go-ethereum's `virtualHostHandler`/CORS layering, whose documented purpose is exactly the mitigation the java-sdk advisory calls for: validating the incoming request's `Host`/`Origin` to stop DNS-rebinding attacks from browser-based clients reaching a locally/network-bound RPC server. In sei-chain, `NewEVMHTTPServer` unconditionally sets `Vhosts: []string{"*"}` when building `HTTPConfig`, which disables that check for every EVM JSON-RPC HTTP endpoint regardless of operator configuration.

### Finding Description
`evmrpc/rpcstack.go` implements `virtualHostHandler`, explicitly documented as a DNS-rebinding countermeasure: [1](#0-0) 

The `ServeHTTP` logic shows that if `"*"` is present in the vhosts set, every request — regardless of its `Host` header — is passed straight through to the RPC handler, i.e. Host validation is a no-op: [2](#0-1) 

`NewHTTPHandlerStack` composes `newCorsHandler` (Origin-based CORS) with `newVHostHandler` (Host-based check) as two independent layers protecting against DNS rebinding: [3](#0-2) 

`NewEVMHTTPServer`, however, statically builds the `HTTPConfig` with `Vhosts: []string{"*"}` — there is no configuration key that lets a node operator set a restrictive vhost list; the wildcard is compiled in: [4](#0-3) 

This means the Host-header defense layer described in the advisory ("Servers MUST validate the Origin/Host header...to prevent DNS rebinding attacks") is permanently disabled on the sei-chain EVM JSON-RPC HTTP server, leaving CORS (`config.CORSOrigins`, default likely permissive or trivially spoofable by non-browser clients) as the only remaining line of defense. CORS checks are enforced by browsers using the `Origin` header on cross-origin `fetch`/`XHR`, but they do not stop a DNS-rebinding attack from reaching the server at the network layer the way Host-header validation does — the whole point of `virtualHostHandler` is to add a second, harder-to-bypass check. With `Vhosts` fixed to `"*"`, an attacker who rebinds a DNS name to a victim's local/private IP (where a sei-chain EVM RPC node listens, e.g. `0.0.0.0:HTTPPort`) can have the victim's browser send JSON-RPC requests that pass Host validation trivially, and CORS is the only remaining barrier — which can itself be permissive or is not defense-in-depth against browser-based rebinding techniques that manipulate CORS preflight timing/caching.

### Impact Explanation
If reachable via a browser, this pattern allows unauthorized JSON-RPC calls (e.g., `eth_sendTransaction`, `eth_sendRawTransaction`, `personal_*`, `debug_*`, or account-management APIs exposed on the same HTTP surface) to be issued against a node's EVM RPC server that the operator believed was protected by the vhost/CORS layering already present in the codebase. Depending on which RPC modules and accounts are enabled/unlocked on the target node, this can result in unauthorized transaction submission and fund loss, matching the "unauthorized transfer" and "fee abuse" impact categories in scope.

### Likelihood Explanation
Exploitation requires (1) a victim (or automated client) whose browser can reach the target sei-chain EVM RPC HTTP endpoint (locally-run node or same-network node), and (2) the target having enabled RPC methods that can transfer value or otherwise act on behalf of accounts. Because `Vhosts` is hardcoded and not operator-configurable, every default-configuration EVM RPC HTTP node on sei-chain running this code is affected without any misconfiguration by the operator — this raises likelihood relative to the upstream advisory, where at least some frameworks default to a safe posture.

### Recommendation
Make `Vhosts` configurable (mirroring `CORSOrigins`/`WSOrigins`), default it to a safe, non-wildcard value such as `localhost`/the bind address, and only allow `"*"` when explicitly opted into by the operator (with a clear warning, as go-ethereum does with its `--http.vhosts` flag). At minimum, do not hardcode the wildcard in `NewEVMHTTPServer`.

### Proof of Concept
1. Start a node with default EVM RPC HTTP config (`--evm.http-port` reachable on the local network/loopback).
2. Confirm `evmrpc/server.go` constructs `HTTPConfig{Vhosts: []string{"*"}}` — no CLI/config flag path currently exists to override this before `EnableRPC` is called.
3. Send an HTTP JSON-RPC POST with an arbitrary `Host` header (simulating a DNS-rebound domain) to the RPC port; per `virtualHostHandler.ServeHTTP`, since `"*"` is in `h.vhosts`, the request is forwarded to the RPC handler regardless of Host header — confirmed by the existing test `TestVhosts` in `evmrpc/rpcstack_test.go` showing non-wildcard vhosts correctly reject unknown hosts with 403, which does not happen when vhosts is `"*"`.
4. Combine with a browser-based DNS rebinding technique to have a victim's browser issue same-origin-appearing requests to the node's RPC port, bypassing Host validation entirely and relying solely on CORS (which does not block simple-request `Origin`-agnostic fetches for endpoints that don't require credentials, or which malicious sites can manipulate via crafted CORS-safelisted requests).

### Citations

**File:** evmrpc/rpcstack.go (L444-453)
```go
// NewHTTPHandlerStack returns wrapped http-related handlers.
func NewHTTPHandlerStack(srv http.Handler, cors []string, vhosts []string, JwtSecret []byte) http.Handler {
	// Wrap the CORS-handler within a host-handler
	handler := newCorsHandler(srv, cors)
	handler = newVHostHandler(vhosts, handler)
	if len(JwtSecret) != 0 {
		handler = newJWTHandler(JwtSecret, handler)
	}
	return NewGzipHandler(handler)
}
```

**File:** evmrpc/rpcstack.go (L478-522)
```go
// virtualHostHandler is a handler which validates the Host-header of incoming requests.
// Using virtual hosts can help prevent DNS rebinding attacks, where a 'random' domain name points to
// the service ip address (but without CORS headers). By verifying the targeted virtual host, we can
// ensure that it's a destination that the node operator has defined.
type virtualHostHandler struct {
	vhosts map[string]struct{}
	next   http.Handler
}

func newVHostHandler(vhosts []string, next http.Handler) http.Handler {
	vhostMap := make(map[string]struct{})
	for _, allowedHost := range vhosts {
		vhostMap[strings.ToLower(allowedHost)] = struct{}{}
	}
	return &virtualHostHandler{vhostMap, next}
}

// ServeHTTP serves JSON-RPC requests over HTTP, implements http.Handler
func (h *virtualHostHandler) ServeHTTP(w http.ResponseWriter, r *http.Request) {
	// if r.Host is not set, we can continue serving since a browser would set the Host header
	if r.Host == "" {
		h.next.ServeHTTP(w, r)
		return
	}
	host, _, err := net.SplitHostPort(r.Host)
	if err != nil {
		// Either invalid (too many colons) or no port specified
		host = r.Host
	}
	if ipAddr := net.ParseIP(host); ipAddr != nil {
		// It's an IP address, we can serve that
		h.next.ServeHTTP(w, r)
		return
	}
	// Not an IP address, but a hostname. Need to validate
	if _, exist := h.vhosts["*"]; exist {
		h.next.ServeHTTP(w, r)
		return
	}
	if _, exist := h.vhosts[host]; exist {
		h.next.ServeHTTP(w, r)
		return
	}
	http.Error(w, "invalid host specified", http.StatusForbidden)
}
```

**File:** evmrpc/server.go (L210-215)
```go
	httpConfig := HTTPConfig{
		CorsAllowedOrigins: strings.Split(config.CORSOrigins, ","),
		Vhosts:             []string{"*"},
		DenyList:           config.DenyList,
		SeiLegacyAllowlist: seiLegacyAllowlist,
	}
```
