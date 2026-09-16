### Title
Unbounded HTTP request body read bypasses `MaxRequestContentLength` when NewRelic/Datadog RPC tracing is enabled, enabling memory-exhaustion DoS against the public JSON-RPC endpoint - ([File: networks/rpc/http_newrelic.go])

### Summary
When a Kaia node's HTTP-RPC server is started with NewRelic or Datadog tracing enabled (`newNewRelicApp()` / `newDatadogTracer()` activated via env vars), every incoming HTTP request is first passed through `newNewRelicHTTPHandler` / `newDatadogHTTPHandler`, which call `getRPCRequests(r)`. That helper does `io.ReadAll(r.Body)` with **no size limit** [1](#0-0)  before the request ever reaches the size-checking code path. The intended 512KB cap (`common.MaxRequestContentLength`) is only enforced later, in `validateRequest`/`newHTTPServerConn`, which is executed *after* `getRPCRequests` has already buffered the full body in memory [2](#0-1) [3](#0-2) .

### Finding Description
`NewHTTPServer` builds the request handler chain by wrapping the base `srv` (which performs the `MaxRequestContentLength`-bounded read via `newHTTPServerConn`) with CORS, vhost, timeout, and — if configured — NewRelic and Datadog handlers, in that order, with Datadog outermost: [4](#0-3) 

Both telemetry wrappers unconditionally call `getRPCRequests(r)` before invoking the inner `handler.ServeHTTP`: [5](#0-4) [6](#0-5) 

`getRPCRequests` reads the entire `r.Body` with `io.ReadAll`, which has no upper bound, unlike the properly bounded path used elsewhere (`io.LimitReader(r.Body, int64(common.MaxRequestContentLength))` in `newHTTPServerConn`, and the explicit `r.ContentLength` check in `validateRequest`): [7](#0-6) [3](#0-2) [2](#0-1) 

Because Go's `net/http` server does not itself cap `r.Body` size (a server can accept chunked transfer-encoding with no declared `Content-Length`, or a spoofed one), `io.ReadAll` will keep allocating memory for the entire request body regardless of size, and this buffering happens *before* the `MaxRequestContentLength` check that is supposed to reject oversized requests. This mirrors the CVE-2018-8409 bug class: a network-facing request-parsing/buffering layer that improperly handles the incoming stream, allowing memory to be exhausted before size limits are applied.

### Impact Explanation
An unauthenticated, unprivileged caller of the public JSON-RPC HTTP endpoint can send one or more large/streamed request bodies (e.g., multi-gigabyte payloads via chunked transfer-encoding) to a node that has NewRelic or Datadog RPC tracing enabled. Each such request causes the node to buffer the entire body in memory via `io.ReadAll` before the `1024*512`-byte cap (`common.MaxRequestContentLength`, [8](#0-7) ) is ever checked. Concurrent requests of this kind can drive the node's memory usage far beyond the intended bound, leading to OOM crashes or severe service degradation — a denial of service against a validator/full node's public RPC interface, consistent with the "High" severity DoS class in the referenced advisory.

### Likelihood Explanation
Exploitation requires only a single unauthenticated HTTP POST/PUT request to a reachable RPC port; no special privileges, valid transaction, or state are needed. The only precondition is that the operator has enabled NewRelic (`NEWRELIC_APP_NAME`/`NEWRELIC_LICENSE`) or Datadog (`DD_TRACE_ENABLED=true`) tracing, which is a documented, supported production configuration for API providers (the code even references KAS — Kaia's hosted API service — headers, e.g. `parseKASHeader`), making this a realistic operational deployment rather than a hypothetical one. Once enabled, every request through the affected node is exposed, so likelihood of triggering (once the feature is enabled) is high; likelihood of the feature being enabled in a given deployment is the main variable.

### Recommendation
Apply the same bound used elsewhere in the codebase before reading the body in `getRPCRequests`: wrap `r.Body` with `io.LimitReader(r.Body, int64(common.MaxRequestContentLength+1))` (mirroring `newHTTPServerConn`), and reject/short-circuit if the limit is exceeded, before calling `io.ReadAll`. Alternatively, perform the `validateRequest`/content-length checks prior to invoking the NewRelic/Datadog wrappers, or use `http.MaxBytesReader` at the outermost handler layer so the limit is enforced regardless of handler ordering.

### Proof of Concept
1. Start a `kaia` node with HTTP-RPC enabled and set `DD_TRACE_ENABLED=true` (or `NEWRELIC_APP_NAME`/`NEWRELIC_LICENSE`) so `newDatadogHTTPHandler`/`newNewRelicHTTPHandler` wrap the RPC server, per `NewHTTPServer` [4](#0-3) .
2. From an unprivileged client, send an HTTP POST to the RPC endpoint with `Transfer-Encoding: chunked` (no `Content-Length` header) and stream an arbitrarily large body (e.g., several GB of padding bytes) with `Content-Type: application/json`.
3. Observe that `getRPCRequests` (invoked at the very start of the Datadog/NewRelic wrapper) calls `io.ReadAll(r.Body)` [1](#0-0)  and buffers the entire streamed body into memory before the downstream `srv.ServeHTTP`/`validateRequest` content-length check (`1024*512` bytes) is ever reached [2](#0-1) .
4. Repeat with multiple concurrent connections to drive the node's memory consumption upward, causing degraded performance or an OOM kill of the node process.

### Citations

**File:** networks/rpc/http_newrelic.go (L92-98)
```go
		reqMethod := ""

		// parse RPC requests
		reqs, isBatch, err := getRPCRequests(r)
		if err != nil || len(reqs) < 1 {
			// The error will be handled in `handler.ServeHTTP()` and printed with `printRPCErrorLog()`
			logger.Debug("failed to parse RPC request", "err", err, "len(reqs)", len(reqs))
```

**File:** networks/rpc/http_newrelic.go (L141-159)
```go
// getRPCRequests copies a http request body data and parses RPC requests from the data.
// It returns a slice of RPC request, an indication if these requests are in batch, and an error.
// Ethereum returns []*jsonrpcMessage, which replaces []rpcRequest
func getRPCRequests(r *http.Request) ([]*jsonrpcMessage, bool, error) {
	reqBody, err := io.ReadAll(r.Body)
	if err != nil {
		logger.Error("cannot read a request body", "err", err)
		return nil, false, err
	}

	r.Body = io.NopCloser(bytes.NewReader(reqBody))
	conn := &httpServerConn{Reader: io.NopCloser(bytes.NewReader(reqBody)), Writer: bytes.NewBufferString(""), r: r}

	codec := NewCodec(conn)

	defer codec.close()

	return codec.readBatch()
}
```

**File:** networks/rpc/http.go (L249-253)
```go
func newHTTPServerConn(r *http.Request, w http.ResponseWriter) ServerCodec {
	body := io.LimitReader(r.Body, int64(common.MaxRequestContentLength))
	conn := &httpServerConn{Reader: body, Writer: w, r: r}
	return NewCodec(conn)
}
```

**File:** networks/rpc/http.go (L271-296)
```go
func NewHTTPServer(cors []string, vhosts []string, timeouts HTTPTimeouts, srv http.Handler) *http.Server {
	timeouts = sanitizeTimeouts(timeouts)
	// Wrap the CORS-handler within a host-handler
	handler := newCorsHandler(srv, cors)
	handler = newVHostHandler(vhosts, handler)
	handler = http.TimeoutHandler(handler, timeouts.ExecutionTimeout, "timeout")

	// If os environment variables for NewRelic exist, register the NewRelicHTTPHandler
	nrApp := newNewRelicApp()
	if nrApp != nil {
		handler = newNewRelicHTTPHandler(nrApp, handler)
	}

	// If os environment variables for Datadog exist, register the NewDatadogHTTPHandler
	ddTracer := newDatadogTracer()
	if ddTracer != nil {
		handler = newDatadogHTTPHandler(ddTracer, handler)
	}

	return &http.Server{
		Handler:      handler,
		ReadTimeout:  timeouts.ReadTimeout,
		WriteTimeout: timeouts.WriteTimeout,
		IdleTimeout:  timeouts.IdleTimeout,
	}
}
```

**File:** networks/rpc/http.go (L411-418)
```go
func validateRequest(r *http.Request) (int, error) {
	if r.Method == http.MethodPut || r.Method == http.MethodDelete {
		return http.StatusMethodNotAllowed, errors.New("method not allowed")
	}
	if r.ContentLength > int64(common.MaxRequestContentLength) {
		err := fmt.Errorf("content length too large (%d>%d)", r.ContentLength, int64(common.MaxRequestContentLength))
		return http.StatusRequestEntityTooLarge, err
	}
```

**File:** networks/rpc/http_datadog.go (L94-101)
```go
		reqMethod := ""
		reqParam := ""

		// parse RPC requests
		reqs, isBatch, err := getRPCRequests(r)
		if err != nil || len(reqs) < 1 {
			// The error will be handled in `handler.ServeHTTP()` and printed with `printRPCErrorLog()`
			logger.Debug("failed to parse RPC request", "err", err, "len(reqs)", len(reqs))
```

**File:** common/variables.go (L21-21)
```go
var MaxRequestContentLength = 1024 * 512
```
