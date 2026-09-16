### Title
Unbounded `io.ReadAll(r.Body)` in RPC APM tracing wrappers allows unauthenticated memory-exhaustion DoS - ([File: networks/rpc/http_newrelic.go])

### Summary
When Phoenix's LongPoll `application/x-ndjson` POST handler is hit, it reads and splits the entire request body into memory before any size limit is enforced, letting a remote client force large allocations with a handful of concurrent requests. Kaia's JSON-RPC HTTP server has an analogous, exploitable pattern: when NewRelic or Datadog APM tracing is enabled, every RPC POST request's body is fully buffered into memory via `io.ReadAll(r.Body)` **before** the server's own content-length/size validation (`validateRequest`) ever runs.

### Finding Description
`getRPCRequests` reads the complete HTTP request body with no size cap: [1](#0-0) 

This function is invoked by both the NewRelic wrapper and the Datadog wrapper, and in both cases it is called as the very first step of request handling — prior to delegating to the inner handler chain that eventually reaches `Server.ServeHTTP`, which is the place that actually enforces `common.MaxRequestContentLength` via `validateRequest`: [2](#0-1) [3](#0-2) 

The size check that is supposed to protect the server exists only deeper in the stack: [4](#0-3) [5](#0-4) 

These wrapper handlers are installed unconditionally in front of the whole handler chain whenever the corresponding environment variables are set: [6](#0-5) [7](#0-6) 

Because `io.ReadAll` has no bound and runs before `validateRequest`'s `r.ContentLength > MaxRequestContentLength` check (or any other size gate) is consulted, a client can send an oversized (or `Transfer-Encoding: chunked`, unbounded `ContentLength == -1`) POST body to any public JSON-RPC HTTP endpoint and force the node to allocate memory proportional to the body it sends, exactly mirroring the root cause of GHSA-628h-q48j-jr6q (unoptimized body materialization ahead of any size enforcement).

### Impact Explanation
This is reachable by any unauthenticated public-RPC caller — no transaction signing, staking, or special privilege is required, only a normal HTTP POST to the node's RPC endpoint. A handful of concurrent large-body requests can force multiple full-body buffers to be held in memory simultaneously (`bytes` in `getRPCRequests` plus the duplicate write buffers used by the tracer wrappers), which can exhaust node memory and crash or stall the JSON-RPC service, denying service to legitimate transaction senders, gasless/auction participants, and other RPC consumers relying on that endpoint (CWE-770, uncontrolled resource consumption), consistent with the High severity of the source Phoenix advisory.

### Likelihood Explanation
The vulnerable code path is only active when the node operator has enabled NewRelic (`NEWRELIC_APP_NAME`/`NEWRELIC_LICENSE`) or Datadog (`DD_TRACE_ENABLED`) tracing on the HTTP-RPC server, which is an optional but realistic production configuration for observability. Once enabled, exploitation requires no authentication and no special client capability — any HTTP client can trigger it with ordinary POST requests, making likelihood high for nodes running with APM tracing turned on.

### Recommendation
Wrap `r.Body` with `io.LimitReader(r.Body, common.MaxRequestContentLength)` (or use `http.MaxBytesReader`) inside `getRPCRequests` before calling `io.ReadAll`, and reject/short-circuit the tracer wrapper immediately once the limit is exceeded, so the size check is enforced at the very first point the body is read, not only in `Server.ServeHTTP`/`validateRequest` after the entire body has already been buffered.

### Proof of Concept
1. Start a Kaia node with HTTP-RPC enabled and set `NEWRELIC_APP_NAME`/`NEWRELIC_LICENSE` (or `DD_TRACE_ENABLED=true`) to activate the APM handler wrapper.
2. Send a POST request to the RPC endpoint with `Transfer-Encoding: chunked` (or an oversized `Content-Length`) and a very large streamed body (e.g., several hundred MB) as the JSON-RPC payload.
3. Observe that `getRPCRequests` → `io.ReadAll(r.Body)` fully buffers the body in memory before `validateRequest`'s content-length check is ever consulted; repeat with several concurrent connections to exhaust node memory, denying RPC service to legitimate callers.

### Citations

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

**File:** networks/rpc/http.go (L301-345)
```go
func NewFastHTTPServer(cors []string, vhosts []string, timeouts HTTPTimeouts, srv *Server) *fasthttp.Server {
	timeouts = sanitizeTimeouts(timeouts)
	if len(cors) == 0 {
		if slices.Contains(vhosts, "*") {
			return &fasthttp.Server{
				Concurrency:        ConcurrencyLimit,
				Handler:            fasthttp.TimeoutHandler(srv.HandleFastHTTP, timeouts.ExecutionTimeout, "timeout"),
				ReadTimeout:        timeouts.ReadTimeout,
				WriteTimeout:       timeouts.WriteTimeout,
				IdleTimeout:        timeouts.IdleTimeout,
				MaxRequestBodySize: common.MaxRequestContentLength,
				ReduceMemoryUsage:  true,
			}
		}
	}
	// Wrap the CORS-handler within a host-handler
	handler := newCorsHandler(srv, cors)
	handler = newVHostHandler(vhosts, handler)

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

	fhandler := fasthttpadaptor.NewFastHTTPHandler(handler)
	fhandler = fasthttp.TimeoutHandler(fhandler, timeouts.ExecutionTimeout, "timeout")

	// TODO-Kaia concurreny default (256 * 1024), goroutine limit (8192)
	return &fasthttp.Server{
		Concurrency:        ConcurrencyLimit,
		Handler:            fhandler,
		ReadTimeout:        timeouts.ReadTimeout,
		WriteTimeout:       timeouts.WriteTimeout,
		IdleTimeout:        timeouts.IdleTimeout,
		MaxRequestBodySize: common.MaxRequestContentLength,
		ReduceMemoryUsage:  true,
	}
}
```

**File:** networks/rpc/http.go (L347-356)
```go
// ServeHTTP serves JSON-RPC requests over HTTP.
func (s *Server) ServeHTTP(w http.ResponseWriter, r *http.Request) {
	// Permit dumb empty requests for remote health-checks (AWS)
	if r.Method == http.MethodGet && r.ContentLength == 0 && r.URL.RawQuery == "" {
		return
	}
	if code, err := validateRequest(r); err != nil {
		http.Error(w, err.Error(), code)
		return
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
