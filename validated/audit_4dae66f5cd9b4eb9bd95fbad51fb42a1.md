Confirmed: both `newNewRelicHTTPHandler` and `newDatadogHTTPHandler` call `getRPCRequests(r)` — which does `io.ReadAll(r.Body)` with no size limit — before the wrapped `handler.ServeHTTP` (which contains `validateRequest`'s `Content-Length` check) ever runs.### Title
Unbounded in-memory read of JSON-RPC HTTP request body via NewRelic/Datadog tracing wrapper bypasses `MaxRequestContentLength` check - ([File: networks/rpc/http_newrelic.go])

### Summary
Kaia's JSON-RPC HTTP server normally protects itself from oversized request bodies by checking `Content-Length` against `common.MaxRequestContentLength` in `validateRequest` before reading the body, and by wrapping the body reader in `io.LimitReader`. However, when NewRelic or Datadog tracing is enabled on the node operator's public RPC endpoint, an additional outer HTTP handler is installed that calls `io.ReadAll(r.Body)` on the *entire* raw request body — with no size bound — before the inner handler (which contains the `Content-Length`/size validation) ever runs. This mirrors the FreeIPA CVE-2026-73197 bug class: an unauthenticated, unbounded read of an attacker-controlled request body into memory before any validation.

### Finding Description
The public JSON-RPC HTTP server is composed in `NewHTTPServer`: [1](#0-0) 

When NewRelic (env vars `NEWRELIC_APP_NAME`/`NEWRELIC_LICENSE`) or Datadog (`DD_TRACE_ENABLED`) tracing is configured, the corresponding handler is wrapped *outermost*, meaning it runs first for every incoming request, before the inner `srv` (the `Server.ServeHTTP` that performs `validateRequest`'s size/content-type checks) is invoked.

Both tracing wrappers call the shared helper `getRPCRequests(r)`: [2](#0-1) 

This function performs `io.ReadAll(r.Body)` with **no size limit whatsoever** — unlike the legitimate request path, which uses `io.LimitReader(r.Body, int64(common.MaxRequestContentLength))` inside `newHTTPServerConn`: [3](#0-2) 

and enforces `Content-Length` bounds in `validateRequest`: [4](#0-3) 

Both `newNewRelicHTTPHandler` and `newDatadogHTTPHandler` invoke `getRPCRequests(r)` unconditionally at the very start of the outer handler, prior to calling the inner `handler.ServeHTTP`: [5](#0-4) [6](#0-5) 

Because Go's `net/http.Server` does not impose a default body size limit and `MaxBytesReader`/`MaxHeaderBytes` are not configured anywhere in the RPC server setup (confirmed absent from the codebase), an attacker who sends a POST request with an arbitrarily large body (e.g., multiple GB, with or without a truthful `Content-Length` header, or using chunked transfer-encoding) to the JSON-RPC HTTP endpoint will have that entire body buffered fully into memory by `io.ReadAll` in `getRPCRequests`, regardless of `common.MaxRequestContentLength`. The size check in `validateRequest` never gets a chance to reject the request early because it executes only in the inner handler, invoked after the unbounded read has already completed.

### Impact Explanation
This allows any unauthenticated, unprivileged public RPC caller to force the node to allocate memory proportional to the attacker-supplied request body size on every request, with no upper bound enforced by the tracing wrapper path. Repeated or concurrent oversized requests can exhaust node memory, causing garbage-collection pressure, request-handling slowdowns, or an out-of-memory crash — a denial-of-service condition against the node's public RPC endpoint. This matches the "Availability: High" impact and reachable-by-single-request nature of CVE-2026-73197.

### Likelihood Explanation
The vulnerable path is only active when the node operator has enabled NewRelic (`NEWRELIC_APP_NAME`/`NEWRELIC_LICENSE`) or Datadog (`DD_TRACE_ENABLED`) monitoring on the HTTP RPC server, which is a common operational practice for production Kaia nodes to gain observability. When enabled, exploitation requires nothing more than sending a single large HTTP POST request to the JSON-RPC endpoint — no authentication, special privileges, or crafted transaction is needed, making the likelihood high once the pre-condition (tracing enabled) is met.

### Recommendation
Apply the same body-size bound used in the primary request path to the tracing wrapper's body read. Specifically, wrap `r.Body` with `io.LimitReader(r.Body, int64(common.MaxRequestContentLength))` (or an explicit `http.MaxBytesReader`) before calling `io.ReadAll` in `getRPCRequests`, and/or move the `Content-Length`/size validation (`validateRequest`) to run in the outermost handler so it executes before any tracing-related body buffering occurs.

### Proof of Concept
1. Start a Kaia node with the HTTP RPC endpoint enabled and set `NEWRELIC_APP_NAME`/`NEWRELIC_LICENSE` (or `DD_TRACE_ENABLED=true`) so `newNewRelicHTTPHandler`/`newDatadogHTTPHandler` wraps the RPC server, per `NewHTTPServer` (`networks/rpc/http.go:271-296`).
2. From an unauthenticated remote client, send a POST request to the RPC endpoint (e.g., `http://<node>:8551`) with a body several times larger than `common.MaxRequestContentLength` (e.g., using chunked transfer-encoding or an artificially large body), such as:
   ```
   curl -X POST http://<node>:8551 -H "Content-Type: application/json" \
        --data-binary @huge_payload.json
   ```
   where `huge_payload.json` is several hundred MB to GB of padding data.
3. Observe that `getRPCRequests` (`networks/rpc/http_newrelic.go:144-159`) fully reads the oversized body into memory via `io.ReadAll` before `validateRequest`'s Content-Length check (`networks/rpc/http.go:411-418`) is ever reached, causing elevated memory consumption on the node.
4. Repeat with multiple concurrent connections to amplify memory pressure and observe degraded RPC responsiveness or crash.

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

**File:** networks/rpc/http.go (L409-418)
```go
// validateRequest returns a non-zero response code and error message if the
// request is invalid.
func validateRequest(r *http.Request) (int, error) {
	if r.Method == http.MethodPut || r.Method == http.MethodDelete {
		return http.StatusMethodNotAllowed, errors.New("method not allowed")
	}
	if r.ContentLength > int64(common.MaxRequestContentLength) {
		err := fmt.Errorf("content length too large (%d>%d)", r.ContentLength, int64(common.MaxRequestContentLength))
		return http.StatusRequestEntityTooLarge, err
	}
```

**File:** networks/rpc/http_newrelic.go (L82-104)
```go
// newNewRelicHTTPHandler enables NewRelic web transaction monitor.
// It also prints error logs when RPC returns contains error messages.
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
		} else {
			reqMethod = reqs[0].Method
			if isBatch {
				reqMethod += "_batch"
			}
		}
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

**File:** networks/rpc/http_datadog.go (L86-109)
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
			// The error will be handled in `handler.ServeHTTP()` and printed with `printRPCErrorLog()`
			logger.Debug("failed to parse RPC request", "err", err, "len(reqs)", len(reqs))
		} else {
			reqMethod = reqs[0].Method
			if isBatch {
				reqMethod += "_batch"
			}
			encoded, _ := json.Marshal(reqs[0].Params)
			reqParam = string(encoded)
		}
```
