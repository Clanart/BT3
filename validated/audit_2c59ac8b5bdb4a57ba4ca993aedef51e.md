This is confirmed by the codebase's own documentation, which explicitly describes the pre-auth JWT check bypassing the rate limiter as intentional design.

### Title
Sei EVM JSON-RPC JWT Pre-Auth Bypasses Per-IP Rate Limiter, Enabling Unthrottled Brute-Force Guessing of the RPC JWT Secret - (File: evmrpc/rpcstack.go)

### Summary
When an operator enables JWT-protected access to the Sei EVM HTTP/WS RPC endpoint (`RPCEndpointConfig.JwtSecret`), every request's `Authorization: Bearer <token>` is validated by `jwtHandler.ServeHTTP` before the request ever reaches `newRateLimitMiddleware` / `ratelimiter.Gate`. Failed-auth responses (`401 Unauthorized`) are returned directly by the JWT layer without charging any per-IP token bucket, so an unauthenticated client can send unlimited forged/guessed JWTs against the node with no throttling, mirroring the OpenClaw Synology webhook advisory's "pre-auth rate-limit bypass enabling brute-force guessing of a weak token" bug class.

### Finding Description
`HTTPServer.EnableRPC` explicitly builds the JWT check as the **outermost** layer of the HTTP handler chain, wrapping the rate limiter rather than being wrapped by it: [1](#0-0) 

The in-repo spec confirms this is by design and documents the exact ordering — `jwt → requestSizeLimiter → rateLimitMiddleware → ...`: [2](#0-1) 

The same pattern is used for the WebSocket handler stack, where JWT also wraps the connection handler with no limiter in front of it: [3](#0-2) 

`jwtHandler.ServeHTTP` performs the actual token check (`jwt.ParseWithClaims` + expiry validation) and returns `401` on any failure — wrong secret, malformed token, expired/future `iat`, etc. — with no delay, counter, lockout, or rate-limit charge of any kind: [4](#0-3) 

By contrast, the rate limiter that *is* present in the stack (`ratelimiter.Gate` / `rateLimitMiddleware`) only ever sees requests that already passed JWT validation, so it provides zero protection against JWT-guessing traffic: [5](#0-4) 

This is structurally identical to the reported OpenClaw bug: a pre-auth check rejects invalid credentials without any throttling, allowing brute-force guessing of a shared secret before any rate-limit gate is reached.

### Impact Explanation
If a node operator relies on the JWT secret as an access-control mechanism for the EVM RPC/WS endpoint (e.g., to restrict `debug_*`/admin-adjacent methods, or to gate RPC access entirely on a semi-public listener), an attacker can send unlimited unauthenticated requests with random/guessed tokens without being throttled by the node's own rate limiter, since the JWT layer sits in front of it and rejects before any bucket is charged. This does not itself cause fund loss, but it undermines an access-control primitive that operators may depend upon, and it means the node's advertised IP-based rate limiting provides no actual protection against credential-guessing traffic hitting the JWT layer — an unbounded number of 401s can also be generated cheaply as an unthrottled request stream against the listener.

### Likelihood Explanation
Reaching this path requires nothing more than sending arbitrary HTTP/WS requests to a JWT-configured EVM RPC endpoint — the exact "public-RPC client" surface in scope. The ordering (`jwt` before `rateLimitMiddleware`) is unconditional whenever `JwtSecret` is configured, per `EnableRPC`'s own comment, so any deployment using JWT auth is affected without needing any other precondition.

### Recommendation
Move JWT validation failures behind (or make them consume from) the same per-IP rate-limit gate used for JSON-RPC method admission, so repeated invalid-token attempts are throttled per source IP before/alongside the token parse, matching the fix pattern in the referenced OpenClaw commit (throttle before returning the auth-failure response). At minimum, charge `ChargeAdmissionRejection`-equivalent cost for JWT auth failures.

### Proof of Concept
1. Configure `evmRPCConfig.HTTPEnabled = true` with a non-empty `JwtSecret` (as in `TestJWT`, `evmrpc/rpcstack_test.go:318-334`).
2. Repeatedly send POST requests to the HTTP RPC endpoint with `Authorization: Bearer <random-or-guessed-token>`.
3. Observe that each request returns `401 Unauthorized` from `jwtHandler.ServeHTTP` immediately, with no `429 Too Many Requests` ever returned regardless of request volume from the same IP, since `newRateLimitMiddleware`/`ratelimiter.Gate` (which would return 429) is never reached for these requests — confirmed by the documented handler order `jwt → requestSizeLimiter → rateLimitMiddleware → ...` in `evmrpc/AGENTS.md`.

### Citations

**File:** evmrpc/rpcstack.go (L352-372)
```go
	h.HTTPConfig = config
	// JWT runs before the byte limiter so unauthenticated clients get 401 without
	// touching the global body budget. The inner stack omits JWT when configured here.
	base := NewHTTPHandlerStack(srv, config.CorsAllowedOrigins, config.Vhosts, nil)

	// maxRequestBodyBytes feeds all three body-cap layers (requestSizeLimiter, the gate, and
	// srv.SetHTTPBodyLimit above) so they agree; change the cap via the config value, not one layer.
	// requestSizeLimiter is outermost (after JWT) so declared oversize bodies are rejected from
	// Content-Length before the rate limiter reads the full body (bounded by max_request_body_bytes).
	handler := newRequestSizeLimiter(
		newRateLimitMiddleware(
			wrapSeiLegacyHTTP(base, config.SeiLegacyAllowlist, config.maxRequestBodyBytes),
			config.rateLimitGate,
		),
		config.maxRequestBodyBytes,
		config.maxConcurrentRequestBytes,
		config.bodyReadIdleTimeout,
	)
	if len(config.JwtSecret) != 0 {
		handler = newJWTHandler(config.JwtSecret, handler)
	}
```

**File:** evmrpc/rpcstack.go (L455-462)
```go
// NewWSHandlerStack returns a wrapped ws-related handler.
func NewWSHandlerStack(srv http.Handler, JwtSecret []byte) http.Handler {
	handler := srv
	if len(JwtSecret) != 0 {
		handler = newJWTHandler(JwtSecret, handler)
	}
	return NewWSConnectionHandler(handler)
}
```

**File:** evmrpc/AGENTS.md (L4-17)
```markdown
## HTTP middleware order (JSON-RPC)

When JWT is configured, unauthenticated requests are rejected before the byte
budget is touched:

```
jwt → requestSizeLimiter → rateLimitMiddleware → seiLegacyHTTPGate → gzip → vhost → cors → rpc.Server
```

Without JWT:

```
requestSizeLimiter → rateLimitMiddleware → seiLegacyHTTPGate → gzip → vhost → cors → rpc.Server
```
```

**File:** evmrpc/jwt_handler.go (L44-80)
```go
// ServeHTTP implements http.Handler
func (handler *jwtHandler) ServeHTTP(out http.ResponseWriter, r *http.Request) {
	var (
		strToken string
		claims   jwt.RegisteredClaims
	)
	if auth := r.Header.Get("Authorization"); strings.HasPrefix(auth, "Bearer ") {
		strToken = strings.TrimPrefix(auth, "Bearer ")
	}
	if len(strToken) == 0 {
		http.Error(out, "missing token", http.StatusUnauthorized)
		return
	}
	// We explicitly set only HS256 allowed, and also disables the
	// claim-check: the RegisteredClaims internally requires 'iat' to
	// be no later than 'now', but we allow for a bit of drift.
	token, err := jwt.ParseWithClaims(strToken, &claims, handler.keyFunc,
		jwt.WithValidMethods([]string{"HS256"}),
		jwt.WithoutClaimsValidation())

	switch {
	case err != nil:
		http.Error(out, err.Error(), http.StatusUnauthorized)
	case !token.Valid:
		http.Error(out, "invalid token", http.StatusUnauthorized)
	case !claims.VerifyExpiresAt(time.Now(), false): // optional
		http.Error(out, "token is expired", http.StatusUnauthorized)
	case claims.IssuedAt == nil:
		http.Error(out, "missing issued-at", http.StatusUnauthorized)
	case time.Since(claims.IssuedAt.Time) > JwtExpiryTimeout:
		http.Error(out, "stale token", http.StatusUnauthorized)
	case time.Until(claims.IssuedAt.Time) > JwtExpiryTimeout:
		http.Error(out, "future token", http.StatusUnauthorized)
	default:
		handler.next.ServeHTTP(out, r)
	}
}
```

**File:** ratelimiter/gate.go (L36-42)
```go

// ChargeAdmissionRejection consumes one token for a fail-closed rejection that
// never reaches method parsing (oversize body, read error). Returns true when
// the bucket is exhausted and the caller should respond with HTTP 429.
func (g *Gate) ChargeAdmissionRejection(ctx context.Context, ip string) bool {
	return !g.registry.Allow(ctx, ip, g.plane, MethodInvalid)
}
```
