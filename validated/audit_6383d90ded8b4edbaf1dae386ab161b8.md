### Title
Unbounded body read allows connection-exhaustion DoS in the frozen-RPC-router - ([File: cmd/frozen-rpc-router/main.go])

### Summary
`cmd/frozen-rpc-router` is a standalone public-facing JSON-RPC proxy (deployed via `docker/docker-compose.frozen-rpc-router.yml`) that fronts the live/frozen EVM nodes. Its `http.Server` is configured with `ReadHeaderTimeout`, `WriteTimeout`, and `IdleTimeout`, but **no `ReadTimeout`** [1](#0-0) . Every POST request handled by `router.ServeHTTP` is read fully into memory via `io.ReadAll(request.Body)`, bounded only by `http.MaxBytesReader` (a size cap, not a time cap) [2](#0-1) . Because `ReadTimeout` is zero (unbounded) and there is no per-chunk idle-read timeout, a client that sends a `Content-Length` header but withholds or trickles the body causes `io.ReadAll` to block indefinitely, exactly the bug class described in the Traefik advisory (GHSA-4vwx-54mw-vqfw / CVE-2024-28869).

### Finding Description
`main.go`'s `http.Server` only sets:
```go
server := &http.Server{
    Addr:              cfg.listenAddress,
    Handler:           router,
    ReadHeaderTimeout: 10 * time.Second,
    WriteTimeout:      cfg.writeTimeout,
    IdleTimeout:       2 * time.Minute,
}
``` [1](#0-0) 

`ReadHeaderTimeout` only bounds reading the HTTP headers; once headers are read, Go's `net/http` resets the deadline and, without `ReadTimeout`, no deadline governs subsequent body reads. The handler then blocks on `io.ReadAll(request.Body)` with only a byte-size cap from `http.MaxBytesReader`, never a time cap:
```go
request.Body = http.MaxBytesReader(w, request.Body, r.maxRequestBodySize)
body, err := io.ReadAll(request.Body)
``` [3](#0-2) 

An attacker can open a TCP connection, send valid headers with a `Content-Length` (or use `Transfer-Encoding: chunked`) and then never finish sending the body (or trickle bytes extremely slowly). The goroutine handling that request — and the underlying socket/file descriptor — is pinned indefinitely. There is also no `netutil.LimitListener` / `MaxOpenConnections` cap in this binary, so an attacker can repeat this across many connections to exhaust file descriptors/goroutines on the router process.

This is in stark contrast to the primary EVM JSON-RPC server (`evmrpc/rpcstack.go`), which sets a full `ReadTimeout`/`ReadHeaderTimeout`/`WriteTimeout`/`IdleTimeout` set plus a dedicated `requestSizeLimiter` that enforces a `body_read_idle_timeout` per-chunk stall guard (HTTP 408 on stall) and a connection cap via `MaxOpenConnections` [4](#0-3) [5](#0-4) . That server was clearly hardened specifically against this bug class (see tests `TestRequestSizeLimiter_bodyReadIdleTimeout` and `TestReadHeaderTimeoutSlowloris`) [6](#0-5) [7](#0-6) . The frozen-rpc-router binary was not given the same treatment.

### Impact Explanation
This is a public-facing (per the docker-compose deployment) JSON-RPC gateway process. An unauthenticated remote client can pin arbitrary numbers of goroutines/file descriptors indefinitely with minimal effort (one connection per stalled request), eventually exhausting file descriptors or goroutines and causing the router process to stop accepting/serving new connections — a denial of service against the RPC surface it fronts.

### Likelihood Explanation
Trivial to trigger: any TCP client can open a connection, send a well-formed HTTP header block with `Content-Length` set, and simply stop sending body bytes. No authentication, special payload, or protocol knowledge is required (this is precisely the class of bug in the referenced Traefik/CVE-2024-28869 advisory). Repeating the attack scales linearly with attacker connections and needs no rate-limiting bypass since the router does not cap concurrent connections.

### Recommendation
- Set a bounded `ReadTimeout` on the `http.Server` in `cmd/frozen-rpc-router/main.go` (mirroring the pattern already used in `evmrpc/rpcstack.go` and the tendermint JSON-RPC server's `DefaultConfig`).
- Wrap `request.Body` with a per-chunk idle-read timeout (as done by `budgetBody`/`requestSizeLimiter` in `evmrpc/request_limiter.go`) before calling `io.ReadAll`, so stalled bodies are cut off with an HTTP 408 rather than hanging forever.
- Add a connection cap (`netutil.LimitListener`) to bound the number of concurrently accepted connections, consistent with `MaxOpenConnections` used elsewhere in the codebase.

### Proof of Concept
1. Start the router per `docker/docker-compose.frozen-rpc-router.yml`.
2. From an attacking host, open a raw TCP connection to the router's listen address and send:
```
POST / HTTP/1.1
Host: <router-host>
Content-Length: 1000000

```
(headers terminated with `\r\n\r\n`, but never send the declared body bytes, or send them at 1 byte/second).
3. Observe that the request's handling goroutine and the underlying TCP connection remain open past `ReadHeaderTimeout` (10s) and past `WriteTimeout`/`IdleTimeout` bounds indefinitely, since no `ReadTimeout` governs the body-read phase.
4. Repeat across many connections to exhaust the process's file descriptors/goroutines, denying service to legitimate JSON-RPC clients.

### Citations

**File:** cmd/frozen-rpc-router/main.go (L40-46)
```go
	server := &http.Server{
		Addr:              cfg.listenAddress,
		Handler:           router,
		ReadHeaderTimeout: 10 * time.Second,
		WriteTimeout:      cfg.writeTimeout,
		IdleTimeout:       2 * time.Minute,
	}
```

**File:** cmd/frozen-rpc-router/router.go (L174-198)
```go
func (r *router) ServeHTTP(w http.ResponseWriter, request *http.Request) {
	if request.Method != http.MethodPost {
		r.liveProxy.ServeHTTP(w, request)
		return
	}

	request.Body = http.MaxBytesReader(w, request.Body, r.maxRequestBodySize)
	body, err := io.ReadAll(request.Body)
	if err != nil {
		var maxBytesErr *http.MaxBytesError
		if errors.As(err, &maxBytesErr) {
			http.Error(w, "request body too large", http.StatusRequestEntityTooLarge)
			return
		}
		http.Error(w, "failed to read request body", http.StatusBadRequest)
		return
	}

	trimmed := bytes.TrimSpace(body)
	if len(trimmed) > 0 && trimmed[0] == '[' {
		r.serveBatch(w, request, body)
		return
	}
	r.serveSingle(w, request, body)
}
```

**File:** evmrpc/rpcstack.go (L172-192)
```go
	h.server = &http.Server{
		Handler:           h,
		ReadTimeout:       h.timeouts.ReadTimeout,
		ReadHeaderTimeout: h.timeouts.ReadHeaderTimeout,
		WriteTimeout:      h.timeouts.WriteTimeout,
		IdleTimeout:       h.timeouts.IdleTimeout,
	}

	// Start the server.
	listener, err := net.Listen("tcp", h.endpoint)
	if err != nil {
		// If the server fails to start, we need to clear out the RPC and WS
		// configuration so they can be configured another time.
		h.disableRPC()
		h.disableWS()
		return err
	}
	if h.maxOpenConns > 0 {
		listener = netutil.LimitListener(listener, h.maxOpenConns)
	}
	h.listener = listener
```

**File:** evmrpc/request_limiter.go (L65-111)
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

	// cw suppresses the inner handler's own response once outcome is set, so the
	// status/message below always wins over whatever the inner handler wrote.
	cw := &captureResponseWriter{ResponseWriter: w, outcome: &outcome}
	l.inner.ServeHTTP(cw, r)

	if outcome.status != 0 && !cw.wroteHeader {
		recordRequestRejected(r.Context(), outcome.reason)
		// The inner handler chain may have already mutated the shared response header
		// map before a failing body read caused its status/body to be suppressed. For
		// example, the gzip wrapper can set Content-Encoding: gzip. The limiter's
		// replacement http.Error response is written as plain text, not through that
		// gzip wrapper, so remove Content-Encoding to avoid advertising compression
		// that the bytes on the wire do not use.
		w.Header().Del("Content-Encoding")
		http.Error(w, outcome.message, outcome.status)
	}
}
```

**File:** evmrpc/request_limiter_test.go (L264-298)
```go
func TestRequestSizeLimiter_bodyReadIdleTimeout(t *testing.T) {
	idle := 50 * time.Millisecond
	var completed atomic.Bool

	srv := httptest.NewServer(newRequestSizeLimiter(
		http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
			_, err := io.ReadAll(r.Body)
			if err == nil {
				completed.Store(true)
				w.WriteHeader(http.StatusOK)
			}
		}),
		1024,
		4096,
		idle,
	))
	t.Cleanup(srv.Close)

	conn, err := net.Dial("tcp", srv.Listener.Addr().String())
	require.NoError(t, err)
	t.Cleanup(func() { _ = conn.Close() })

	host := srv.Listener.Addr().String()
	req := fmt.Sprintf("POST / HTTP/1.1\r\nHost: %s\r\nContent-Length: 100\r\n\r\nx", host)
	_, err = conn.Write([]byte(req))
	require.NoError(t, err)

	time.Sleep(idle + 150*time.Millisecond)

	resp, err := http.ReadResponse(bufio.NewReader(conn), &http.Request{Method: http.MethodPost})
	require.NoError(t, err)
	t.Cleanup(func() { _ = resp.Body.Close() })
	require.Equal(t, http.StatusRequestTimeout, resp.StatusCode)
	require.False(t, completed.Load())
}
```

**File:** sei-tendermint/rpc/jsonrpc/server/http_server_test.go (L125-171)
```go
func TestReadHeaderTimeoutSlowloris(t *testing.T) {
	t.Cleanup(leaktest.Check(t))

	ctx := t.Context()

	mux := http.NewServeMux()
	mux.HandleFunc("/", func(w http.ResponseWriter, r *http.Request) {
		fmt.Fprint(w, "ok")
	})

	cfg := DefaultConfig()
	cfg.ReadHeaderTimeout = 100 * time.Millisecond

	l, err := Listen("tcp://127.0.0.1:0", 0)
	require.NoError(t, err)
	defer l.Close()

	go Serve(ctx, l, mux, cfg) //nolint:errcheck

	// Open a raw connection and send partial headers without the terminal \r\n\r\n.
	conn, err := net.Dial("tcp", l.Addr().String())
	require.NoError(t, err)
	defer conn.Close()

	_, err = fmt.Fprint(conn, "GET / HTTP/1.1\r\nHost: localhost\r\n")
	require.NoError(t, err)

	// The server should close or respond (408) after ReadHeaderTimeout fires.
	// The 1s deadline is 10× the ReadHeaderTimeout, so if it fires first
	// the server never acted and the test would be a false pass.
	require.NoError(t, conn.SetReadDeadline(time.Now().Add(1*time.Second)))
	buf := make([]byte, 256)
	n, err := conn.Read(buf)
	if err == nil {
		// Server sent a response before closing — must be 408.
		assert.Contains(t, string(buf[:n]), "408")
	} else {
		// If our deadline fired before the server acted, the error is a net
		// timeout — that means ReadHeaderTimeout did not fire and the test
		// would be meaningless.
		var netErr net.Error
		if errors.As(err, &netErr) && netErr.Timeout() {
			t.Fatal("read deadline expired before server closed connection: ReadHeaderTimeout may not have fired")
		}
		// EOF or connection reset means the server closed the connection — expected.
	}
}
```
