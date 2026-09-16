## Finding

### Title
Pre-Validation Memory Exhaustion via Unbounded `io.ReadAll` in RPC APM Tracing Wrappers - (File: `networks/rpc/http_newrelic.go`, `networks/rpc/http_datadog.go`)

### Summary
When Kaia's JSON-RPC HTTP server is deployed with New Relic or Datadog APM tracing enabled (a supported, documented deployment option), every incoming HTTP request — including unauthenticated public RPC calls — is first passed through a tracing wrapper that buffers the *entire* request body into memory with `io.ReadAll(r.Body)` and **no size limit**, before the normal request-size validation (`validateRequest`) and the size-capped codec (`newHTTPServerConn`) ever run.

### Finding Description
`NewHTTPServer` builds the handler chain as: [1](#0-0) 

The order matters: `newNewRelicHTTPHandler`/`newDatadogHTTPHandler` wrap the *outside* of the chain, meaning they execute **before** `srv.ServeHTTP` (and therefore before `validateRequest`, which enforces `common.MaxRequestContentLength` via `r.ContentLength`) and before `newHTTPServerConn`, which wraps the body in a `io.LimitReader(r.Body, int64(common.MaxRequestContentLength))`: [2](#0-1) [3](#0-2) 

Both tracing wrappers call `getRPCRequests(r)` at the very start of the handler, purely to extract the RPC method name for span/tag metadata: [4](#0-3) [5](#0-4) 

`getRPCRequests` reads the complete body with `io.ReadAll(r.Body)`, with no size cap of any kind: [6](#0-5) 

Because `r.ContentLength` has not yet been checked at this point, an attacker can send a request with a spoofed/absent `Content-Length` (e.g. via chunked transfer-encoding) and stream an arbitrarily large body. `net/http.Server` does not itself cap body size (only `ReadTimeout`/`ReadHeaderTimeout` bound duration, not bytes), so within the configured read timeout the server will allocate memory proportional to whatever the client sends, unbounded by `common.MaxRequestContentLength` (`TestHTTPErrorResponseWithMaxContentLength` shows that limit is only enforced in `validateRequest`, which this path bypasses): [7](#0-6) 

This is structurally identical to the Sliver bug class: a specific, reachable request-handling code path bypasses the size-limited/authenticated body reader and instead performs an unbounded `io.ReadAll`, letting any remote caller drive server memory allocation to exhaustion.

### Impact Explanation
Any unauthenticated public RPC caller can send oversized/chunked HTTP requests to a Kaia node that has New Relic (`NEWRELIC_APP_NAME`/`NEWRELIC_LICENSE`) or Datadog (`DD_TRACE_ENABLED=true`) tracing enabled — a realistic and commonly-used production configuration for observability (e.g., KAS-style deployments, given the `parseKASHeader` helper in the same file). This can exhaust node memory and crash/hang the JSON-RPC HTTP listener, denying service to all RPC clients, independent of the normal `MaxRequestContentLength` protection that the rest of the RPC stack relies on.

### Likelihood Explanation
Exploitation requires no authentication, no special transaction, and no privileged access — only that the target node has APM tracing enabled, which is an explicit supported deployment mode (not a misconfiguration or "operator-only" bug in the excluded sense, since it is triggered by an ordinary unprivileged public RPC caller). Any node operator running with tracing enabled for observability is silently exposed.

### Recommendation
Wrap `r.Body` with `io.LimitReader(r.Body, int64(common.MaxRequestContentLength))` (or `http.MaxBytesReader`) inside `getRPCRequests` before calling `io.ReadAll`, and/or move `validateRequest`'s content-length/size check to run before the New Relic/Datadog wrappers so the size guard cannot be bypassed by any handler in the chain.

### Proof of Concept
1. Start a Kaia node with `NEWRELIC_APP_NAME`/`NEWRELIC_LICENSE` (or `DD_TRACE_ENABLED=true`) set so `newNewRelicHTTPHandler`/`newDatadogHTTPHandler` wrap the RPC HTTP handler.
2. Send an HTTP POST to the RPC endpoint using chunked transfer-encoding (no `Content-Length` header) with a very large streamed body (far exceeding `common.MaxRequestContentLength`).
3. `getRPCRequests` executes `io.ReadAll(r.Body)` before `validateRequest`'s size check runs, causing the server to buffer the entire attacker-controlled stream in memory.
4. Repeating/parallelizing such requests exhausts node memory, crashing or hanging the RPC listener.

### Citations

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

**File:** networks/rpc/http_newrelic.go (L84-98)
```go
func newNewRelicHTTPHandler(nrApp *newrelic.Application, handler http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		defer func() {
			if err := recover(); err != nil {
				logger.ErrorWithStack("NewRelic http handler panic", "err", err)
			}
		}()

		reqMethod := ""

		// parse RPC requests
		reqs, isBatch, err := getRPCRequests(r)
		if err != nil || len(reqs) < 1 {
			// The error will be handled in `handler.ServeHTTP()` and printed with `printRPCErrorLog()`
			logger.Debug("failed to parse RPC request", "err", err, "len(reqs)", len(reqs))
```

**File:** networks/rpc/http_newrelic.go (L144-159)
```go
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

**File:** networks/rpc/http_datadog.go (L86-99)
```go
func newDatadogHTTPHandler(ddTracer *DatadogTracer, handler http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		defer func() {
			if err := recover(); err != nil {
				logger.ErrorWithStack("Datadog http handler panic", "err", err)
			}
		}()

		reqMethod := ""
		reqParam := ""

		// parse RPC requests
		reqs, isBatch, err := getRPCRequests(r)
		if err != nil || len(reqs) < 1 {
```

**File:** networks/rpc/http_test.go (L36-40)
```go
func TestHTTPErrorResponseWithMaxContentLength(t *testing.T) {
	body := make([]rune, common.MaxRequestContentLength+1)
	testHTTPErrorResponse(t,
		http.MethodPost, contentType, string(body), http.StatusRequestEntityTooLarge)
}
```
