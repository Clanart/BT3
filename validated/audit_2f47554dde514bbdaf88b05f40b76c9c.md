This confirms the rate-limit middleware `rateLimitMiddleware.ServeHTTP` in `sei-tendermint/rpc/jsonrpc/server/rate_limit_middleware.go:32-93` only guards the initial HTTP request path (JSON-RPC POST body, CometBFT URI routes) via `m.gate.CheckPOST`/`m.gate.CheckURI`. This middleware wraps the `mux` that dispatches to `/websocket` in `sei-tendermint/internal/rpc/core/env.go:374-394`, but once a client completes the WebSocket upgrade at that single HTTP request, all subsequent messages on that persistent connection are handled entirely inside `wsConnection.readRoutine` in `sei-tendermint/rpc/jsonrpc/server/ws_handler.go:249-347`, which is never passed back through `rateLimitMiddleware` and has no per-message or per-connection throttling — only a maximum message *size* (`readLimit`) and an idle-connection *timeout* (`readWait`).

### Title
Unauthenticated log-flooding DoS via unthrottled Tendermint WebSocket JSON-RPC error logging - (File: sei-tendermint/rpc/jsonrpc/server/ws_handler.go)

### Summary
Any public client that opens the CometBFT/Tendermint `/websocket` RPC endpoint can, on a single connection, send an unbounded stream of malformed or invalid JSON-RPC requests. Each malformed message causes `wsConnection.readRoutine` to invoke `logger.Error(...)` (or trigger a write-failure log), and this path is never subject to the per-IP `RateLimitGate` that protects the plain HTTP JSON-RPC and URI-RPC routes. This is directly analogous to the OSC/OpenOnDemand shell-app CVE-2025-53636, where an unprivileged, unauthenticated user could flood application logs with errors to exhaust disk space and DoS the service.

### Finding Description
`NewRateLimitMiddleware`/`rateLimitMiddleware.ServeHTTP` (`sei-tendermint/rpc/jsonrpc/server/rate_limit_middleware.go:32-93`) only inspects `isCometBFTRootPath` (JSON-RPC POST to `/`) and `isCometBFTURIRPCRequest` (GET-style URI routes). Requests to `/websocket` fall through to `m.inner.ServeHTTP(w, r)` at line 92 with no admission check, and — critically — the rate limiter only fires on the single HTTP upgrade request, not on the messages exchanged after the WebSocket handshake completes.

Once upgraded (`WebsocketHandler`, `sei-tendermint/rpc/jsonrpc/server/ws_handler.go:60-89`), all further traffic is handled by `wsConnection.readRoutine` (`ws_handler.go:249-347`). For every message an attacker sends on this open connection, one of several `logger.Error` calls can fire unconditionally and with no backoff or per-connection budget:
- JSON decode failures: `logger.Error("error writing RPC response", "err", err)` at line 305 (when the immediate error-response write fails) is reachable repeatedly since decode failures continue the loop (`continue` at line 307) rather than closing the connection. [1](#0-0) 
- Unknown-method requests likewise loop and can trigger repeated log writes on write failures. [2](#0-1) 
- A panic inside any RPC handler is caught, logged with a full stack trace via `debug.Stack()`, and the read loop is explicitly *restarted* (`go wsc.readRoutine(ctx)` at line 267), meaning an attacker who can trigger a handler panic repeatedly (e.g., via crafted params to any registered RPC method) can force unlimited stack-trace-sized log entries per connection, and can open many such connections in parallel. [3](#0-2) 

The only guard on this loop is `readLimit` (bounds a single message's byte size) and `readWait` (an idle timeout, not a rate cap) — see `wsc.baseConn.SetReadLimit(wsc.readLimit)` at `ws_handler.go:143` and the `readWait`/`pingPeriod` fields at lines 110-113. Neither bounds message *frequency*, so an attacker's connection can pump messages as fast as the network allows, generating log volume proportional to attacker bandwidth rather than being capped by the node's admission control.

### Impact Explanation
Validator and full/RPC nodes typically write structured logs to local disk; unauthenticated remote log flooding via repeated errors/panics on the WebSocket RPC endpoint can exhaust disk space on default-configuration nodes, which can crash the node process or the underlying OS once disk is full, satisfying the "crash of default-configuration RPC nodes" impact bar. If exploited against validator nodes exposing the RPC/WS endpoint (a common default topology), this can contribute to block delay or validator halt beyond the threshold.

### Likelihood Explanation
The `/websocket` endpoint is public, requires no authentication, and is enabled by default whenever `experimental-disable-websocket` is false (the default) per `sei-tendermint/internal/rpc/core/env.go:369-384`. Establishing a WebSocket connection and streaming malformed requests or panic-inducing calls requires no special privileges — only network access to the RPC port, matching the "unprivileged... public-RPC client" threat model.

### Recommendation
Apply the same `RateLimitGate` admission control used for HTTP JSON-RPC/URI routes to per-message traffic on established WebSocket connections (e.g., wrap `readRoutine`'s per-message processing with a per-IP/per-connection token bucket), rate-limit or downgrade the log level for repeated decode/method-not-found/panic errors from the same connection, and consider disconnecting clients that exceed an error-rate threshold rather than looping indefinitely (especially for the panic-recovery path that currently restarts `readRoutine` unconditionally).

### Proof of Concept
1. Open a WebSocket connection to a node's `/websocket` RPC endpoint (no auth required).
2. In a loop, send thousands of malformed JSON-RPC frames per second (e.g., truncated JSON, or valid JSON with an unknown `method`, or a method call with parameters crafted to panic inside its handler).
3. Observe `wsConnection.readRoutine` logging an `Error`-level entry per malformed/panicking message with no throttling, at a rate limited only by the attacker's outbound bandwidth and the connection's `readLimit`.
4. Repeat with multiple concurrent connections (each independently exempt from the HTTP-request-level `RateLimitGate`) to multiply log volume and accelerate disk exhaustion on the target node.

### Citations

**File:** sei-tendermint/rpc/jsonrpc/server/ws_handler.go (L255-269)
```go
	defer func() {
		if r := recover(); r != nil {
			err, ok := r.(error)
			if !ok {
				err = fmt.Errorf("WSJSONRPC: %v", r)
			}
			req := rpctypes.NewRequest(uriReqID)
			logger.Error("Panic in WSJSONRPC handler", "err", err, "stack", string(debug.Stack()))
			if err := wsc.WriteRPCResponse(writeCtx,
				req.MakeErrorf(rpctypes.CodeInternalError, "Panic in handler: %v", err)); err != nil {
				logger.Error("error writing RPC response", "err", err)
			}
			go wsc.readRoutine(ctx)
		}
	}()
```

**File:** sei-tendermint/rpc/jsonrpc/server/ws_handler.go (L299-308)
```go
			dec := json.NewDecoder(r)
			var request rpctypes.RPCRequest
			err = dec.Decode(&request)
			if err != nil {
				if err := wsc.WriteRPCResponse(writeCtx,
					request.MakeErrorf(rpctypes.CodeParseError, "unmarshaling request: %v", err)); err != nil {
					logger.Error("error writing RPC response", "err", err)
				}
				continue
			}
```

**File:** sei-tendermint/rpc/jsonrpc/server/ws_handler.go (L320-328)
```go
			// Now, fetch the RPCFunc and execute it.
			rpcFunc := wsc.funcMap[request.Method]
			if rpcFunc == nil {
				if err := wsc.WriteRPCResponse(writeCtx,
					request.MakeErrorf(rpctypes.CodeMethodNotFound, "method %s not found", request.Method)); err != nil {
					logger.Error("error writing RPC response", "err", err)
				}
				continue
			}
```
