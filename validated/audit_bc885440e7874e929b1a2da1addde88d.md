### Title
Unbounded EVM-only JSON-RPC HTTP server allows connection/goroutine exhaustion DoS via slow/hanging requests - (File: giga/evmonly/rpc/server.go)

### Summary
The Autobahn "EVM-only" JSON-RPC server that listens on `0.0.0.0:8545` is constructed with an `http.Server` that only sets `ReadHeaderTimeout`, and leaves `ReadTimeout`, `WriteTimeout`, `IdleTimeout`, `MaxHeaderBytes`, and any request-body size cap unset, and binds a plain `net.Listener` with no connection-count limit. [1](#0-0)  This mirrors the Next.js Server Actions issue (CVE-2024-56332): a client can open a connection, complete only the headers (satisfying the 5s `ReadHeaderTimeout`), then stall or trickle the declared body indefinitely, pinning the server goroutine and TCP slot with no backstop timeout to cut it off.

### Finding Description
`Start()` builds the listener directly from `(&net.ListenConfig{}).Listen(...)` with no `netutil.LimitListener`-style connection cap, and constructs:
```go
http: &http.Server{
    Handler:           rpcServer,
    ReadHeaderTimeout: 5 * time.Second,
},
``` [2](#0-1) 

Contrast this with the hardened main EVM JSON-RPC stack in `evmrpc/`, which explicitly sets `ReadTimeout`, `WriteTimeout`, `IdleTimeout`, wraps the listener with a connection cap (`MaxOpenConnections`), applies `http.MaxBytesReader` per request, and adds a `BodyReadIdleTimeout` that actively cuts stalled body reads with HTTP 408 and releases any held byte budget. [3](#0-2) [4](#0-3) [5](#0-4)  The `AGENTS.md` for `evmrpc/` documents this middleware chain (`requestSizeLimiter → rateLimitMiddleware → seiLegacyHTTPGate → gzip → vhost → cors → rpc.Server`) as a deliberate mitigation for exactly this class of slow/oversize-request DoS. [6](#0-5) 

None of these protections exist on the EVM-only executor's RPC server. Because Go's `net/http.Server` only enforces `ReadTimeout`/`WriteTimeout` when explicitly set (a zero value means "no timeout"), a request whose headers arrive within 5 seconds but whose body (e.g., a large `sendRawTransaction` payload declared via `Content-Length`) is sent one byte at a time, or never fully sent, will hold the accepted connection and its serving goroutine open indefinitely. Because there is also no `MaxOpenConnections`/listener limiter, an attacker can repeat this across many TCP connections to exhaust file descriptors and goroutines on the node running this RPC server.

### Impact Explanation
This endpoint serves `eth_sendRawTransaction`-class calls and receipt/balance lookups for the Autobahn "EVM-only executor" shard and is invoked over `sei-tendermint/node/node.go` wiring, making it a public-RPC surface reachable by any unprivileged client with network access, matching the "public EVM JSON-RPC surface" reachability class explicitly in scope. An attacker who opens enough slow/hanging connections can exhaust the process's file descriptors and worker goroutines, causing the RPC server (and potentially the hosting node process, depending on OS FD limits) to stop accepting or servicing new connections — a resource-exhaustion Denial of Service against this RPC node, analogous to the underlying CWE-770 root cause in the Next.js advisory (unbounded resource allocation while server actions/handler goroutines wait for a client that never finishes sending).

### Likelihood Explanation
No authentication, special privileges, or malicious-peer/validator status is required — any public network client that can reach port 8545 can trigger the condition using ordinary raw TCP writes (equivalent to a classic slow-POST/slowloris pattern), which the codebase's own test suite (`TestMaxOpenConns` in `evmrpc/rpcstack_test.go`) demonstrates as an effective technique against a comparable but *protected* HTTP RPC server. [7](#0-6)  Since the `giga/evmonly/rpc/server.go` server has no equivalent read-timeout/connection-limit protections, the same technique succeeds without any budget guard stopping it.

### Recommendation
Apply the same hardening already used for the main EVM RPC stack to `giga/evmonly/rpc/server.go`: set `ReadTimeout`, `WriteTimeout`, and `IdleTimeout` on the `http.Server`, wrap the listener with a connection-count limiter (e.g., `netutil.LimitListener`, matching `evmrpc`'s `MaxOpenConnections`), and cap the request body with `http.MaxBytesReader` plus a body-read idle timeout consistent with `evmrpc/request_limiter.go`'s `requestSizeLimiter`.

### Proof of Concept
1. Start a node running the Autobahn EVM-only executor so `giga/evmonly/rpc/server.go`'s `Start()`/`Serve()` binds `0.0.0.0:8545`.
2. From an attacker host, open a TCP connection and send a valid HTTP request line/headers only, declaring a large `Content-Length` (e.g., `Content-Length: 10000000`), matching the pattern used in the repo's own `TestMaxOpenConns` test:
   ```
   POST / HTTP/1.1\r\nHost: localhost\r\nContent-Type: application/json\r\nContent-Length: 10000000\r\n\r\n
   ``` [8](#0-7) 
3. Never send the body (or trickle a few bytes every few seconds). Because `ReadTimeout` is unset on this server's `http.Server`, the connection and its handling goroutine remain open past the 5s `ReadHeaderTimeout` (already satisfied) indefinitely.
4. Repeat across many connections (no `MaxOpenConnections`/listener cap exists here) to exhaust file descriptors/goroutines, denying service to legitimate `eth_sendRawTransaction`/receipt/balance RPC clients on this node.

### Citations

**File:** giga/evmonly/rpc/server.go (L90-109)
```go
// Start binds the EVM-only JSON-RPC listener and returns its server.
func Start(backend Backend, receiptStore receipt.ReceiptStore) (*Server, error) {
	rpcServer, err := newHandler(backend, receiptStore)
	if err != nil {
		return nil, err
	}
	listener, err := (&net.ListenConfig{}).Listen(context.Background(), "tcp", listenAddress)
	if err != nil {
		rpcServer.Stop()
		return nil, fmt.Errorf("listen for EVM-only RPC on %s: %w", listenAddress, err)
	}
	return &Server{
		listener: listener,
		http: &http.Server{
			Handler:           rpcServer,
			ReadHeaderTimeout: 5 * time.Second,
		},
		rpc: rpcServer,
	}, nil
}
```

**File:** evmrpc/config/config.go (L277-306)
```go
	// MaxRequestBodyBytes is the maximum size, in bytes, of a single HTTP (:8545)
	// or WebSocket (:8546) JSON-RPC request body/frame. HTTP requests larger than
	// this are rejected (HTTP 413) before the body is buffered or JSON-decoded,
	// including at the rate limiter method-extraction layer. WebSocket frames
	// exceeding this limit close the connection with WebSocket close code 1009
	// (no JSON-RPC error response). Oversize WS rejections are recorded on
	// evmrpc_requests_rejected_total{protocol="ws",reason="oversize"}. 0 uses the
	// go-ethereum default (5 MiB). Upgrade note: WS previously used a hardcoded
	// 10 MiB frame cap. With default config both planes now use 5 MiB.
	MaxRequestBodyBytes int64 `mapstructure:"max_request_body_bytes"`

	// MaxConcurrentRequestBytes bounds the total size, in bytes, of HTTP and
	// WebSocket JSON-RPC request bodies admitted for processing concurrently.
	// HTTP (:8545) and WebSocket (:8546) each get an independent budget, so peak
	// in-flight request bytes process-wide can reach 2× this value (e.g. 256 MiB
	// when set to the 128 MiB default). HTTP charges the budget incrementally as
	// body bytes are read, bounding what a slow/stalled upload can pin to about
	// one read batch rather than the full declared Content-Length; requests that
	// would exceed the budget mid-read are rejected (HTTP 429). WebSocket blocks
	// until budget frees or WSAdmissionTimeout elapses; on timeout the peer
	// receives JSON-RPC error -32005 and the connection is closed (active
	// subscriptions are dropped with the connection). Set to 0 to disable the
	// limit on either protocol.
	MaxConcurrentRequestBytes int64 `mapstructure:"max_concurrent_request_bytes"`

	// BodyReadIdleTimeout is the maximum idle time allowed between body chunks
	// while reading an HTTP JSON-RPC request. Stalled body reads are cut with
	// HTTP 408 and release any byte budget held so far. Zero disables the
	// per-chunk idle guard (http.Server ReadTimeout remains the backstop).
	BodyReadIdleTimeout time.Duration `mapstructure:"body_read_idle_timeout"`
```

**File:** evmrpc/request_limiter.go (L65-93)
```go
func (l *requestSizeLimiter) ServeHTTP(w http.ResponseWriter, r *http.Request) {
	// Per-request cap on the declared length (header-only, before any body read).
	if r.ContentLength > l.maxBody {
		recordRequestRejected(r.Context(), rejectReasonOversize)
		http.Error(w, "request body too large", http.StatusRequestEntityTooLarge)
		return
	}
	// Backstop for chunked / mis-declared bodies: cap the bytes actually readable.
	r.Body = http.MaxBytesReader(w, r.Body, l.maxBody)

	var outcome limiterOutcome
	var budgetWrapped *budgetBody
	if l.budget != nil || l.bodyReadIdleTimeout > 0 {
		budgetWrapped = &budgetBody{
			inner:       r.Body,
			budget:      l.budget,
			rc:          http.NewResponseController(w),
			idleTimeout: l.bodyReadIdleTimeout,
			outcome:     &outcome,
		}
		r.Body = budgetWrapped
		// Deferred so a panic anywhere in the inner handler chain (legacy gate,
		// gzip/vhost/cors wrappers) still releases the reserved bytes instead of
		// leaking them from the shared max_concurrent_request_bytes semaphore.
		defer func() {
			_ = budgetWrapped.Close()
			budgetWrapped.release()
		}()
	}
```

**File:** evmrpc/rpcstack_test.go (L584-633)
```go
// TestMaxOpenConns verifies that SetMaxOpenConns wraps the listener so that no
// more than the configured number of connections are accepted at once. With a
// cap of 1, a second connection is not served until the first one is closed.
func TestMaxOpenConns(t *testing.T) {
	srv := evmrpc.NewHTTPServer(rpc.DefaultHTTPTimeouts)
	srv.SetMaxOpenConns(1)
	assert.NoError(t, srv.EnableRPC(apis(), evmrpc.HTTPConfig{}))
	assert.NoError(t, srv.SetListenAddr("localhost", 0))
	assert.NoError(t, srv.Start())
	defer srv.Stop()

	addr := srv.ListenAddr()

	// Open the first connection and send only request headers advertising a body
	// that never arrives. The server accepts it (consuming the single slot), and
	// its serving goroutine blocks reading the body, holding the slot open.
	c1, err := net.Dial("tcp", addr)
	assert.NoError(t, err)
	defer func() {
		_ = c1.Close()
	}()
	_, err = c1.Write([]byte("POST / HTTP/1.1\r\nHost: localhost\r\nContent-Type: application/json\r\nContent-Length: 4096\r\n\r\n"))
	assert.NoError(t, err)

	// Give the accepting loop time to accept c1 and consume the slot.
	time.Sleep(200 * time.Millisecond)

	// While c1 holds the only slot, a second connection is not accepted, so a
	// complete request over it receives no response before the read deadline.
	body := `{"jsonrpc":"2.0","id":1,"method":"test_greet","params":[]}`
	req := fmt.Sprintf("POST / HTTP/1.1\r\nHost: localhost\r\nContent-Type: application/json\r\nContent-Length: %d\r\n\r\n%s", len(body), body)
	c2, err := net.DialTimeout("tcp", addr, time.Second)
	assert.NoError(t, err)
	defer func() {
		_ = c2.Close()
	}()
	_, err = c2.Write([]byte(req))
	assert.NoError(t, err)
	assert.NoError(t, c2.SetReadDeadline(time.Now().Add(500*time.Millisecond)))
	buf := make([]byte, 64)
	_, err = c2.Read(buf)
	assert.Error(t, err, "second connection should not be served while the slot is held")

	// Closing c1 frees the slot; c2 is then accepted and served.
	assert.NoError(t, c1.Close())
	assert.NoError(t, c2.SetReadDeadline(time.Now().Add(5*time.Second)))
	n, err := c2.Read(buf)
	assert.NoError(t, err)
	assert.Greater(t, n, 0)
}
```

**File:** evmrpc/AGENTS.md (L4-24)
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

`requestSizeLimiter` caps each body with `http.MaxBytesReader`, charges the
global `max_concurrent_request_bytes` budget incrementally as body bytes are
read (64 KiB batches), and enforces `body_read_idle_timeout` between body
chunks via an idle timer that only sets the connection read deadline when a
stall actually expires (HTTP 408 on stall, HTTP 429 on mid-read budget
exhaustion). net/http's `ReadTimeout` is left untouched during normal reads.
```
