Based on my research, I found a directly analogous vulnerability in the kaia RPC server's optional APM tracing middleware.

### Title
Unbounded `io.ReadAll` of JSON-RPC request body in NewRelic/Datadog HTTP middleware causes node DoS via memory exhaustion - (File: `networks/rpc/http_newrelic.go`)

### Summary
The kaia JSON-RPC HTTP server normally guards against oversized request bodies in two ways: `validateRequest` rejects requests whose `Content-Length` header exceeds `common.MaxRequestContentLength` (512 KiB), and `newHTTPServerConn` wraps the body in `io.LimitReader(r.Body, common.MaxRequestContentLength)` before the JSON-RPC codec reads it.

However, when the optional NewRelic or Datadog APM tracing middlewares are enabled (via `NEWRELIC_APP_NAME`/`NEWRELIC_LICENSE` or Datadog env vars), these wrap the JSON-RPC handler and call `getRPCRequests(r)`, which performs `io.ReadAll(r.Body)` with no size bound whatsoever — before `validateRequest` or the length-limited codec are ever reached.

### Finding Description
`NewHTTPServer` builds the handler chain as `Datadog(NewRelic(TimeoutHandler(vhost(cors(srv)))))` when the corresponding env vars are set: [1](#0-0) 

Both `newNewRelicHTTPHandler` and `newDatadogHTTPHandler` call `getRPCRequests(r)` on every incoming request before delegating to the inner `handler.ServeHTTP` (which is where `validateRequest`'s content-length check and the `io.LimitReader`-bounded codec actually live): [2](#0-1) 

`getRPCRequests` itself reads the entire body into memory with no cap: [3](#0-2) 

The same unbounded pattern is used identically in the Datadog handler: [4](#0-3) 

Crucially, Go's `net/http` sets `r.ContentLength` to `-1` for chunked-transfer-encoded requests without an explicit `Content-Length` header, so even the downstream `validateRequest` length check (`r.ContentLength > int64(common.MaxRequestContentLength)`) in `networks/rpc/http.go` provides no protection against a chunked request — and that check is never even reached here since `getRPCRequests` runs first and reads the full body regardless. This is precisely the CWE-770 pattern in the reference advisory: an `io.ReadAll` on `r.Body` with no `http.MaxBytesReader`/`io.LimitReader` cap applied first.

### Impact Explanation
Any caller of the node's public JSON-RPC HTTP endpoint (e.g. `eth_call`, `klay_sendRawTransaction`, or any other RPC method) can send an arbitrarily large or chunked POST body. When APM tracing is enabled, this body is fully buffered into memory via `io.ReadAll` before any size validation occurs, allowing an unauthenticated/unprivileged RPC caller to exhaust node memory and crash the `coderd`-equivalent process — here, the kaia node process that also hosts block sync, transaction pool, and other consensus-adjacent services in the same process. This is a denial-of-service against public RPC availability, matching the CVSS profile of the reference advisory (`AC:L/PR:N or L/UI:N/A:H`).

### Likelihood Explanation
Exploitation requires only a single HTTP POST to the node's RPC endpoint; no special privileges beyond RPC access are needed. The condition is gated on the optional APM instrumentation being enabled (`NEWRELIC_APP_NAME`/`NEWRELIC_LICENSE` or Datadog tracer env vars), which is an operator configuration choice, analogous to how the original advisory's AI Bridge feature must be enabled. Where enabled — commonly for operational observability in production RPC endpoint deployments — the vulnerability is trivially and repeatedly triggerable.

### Recommendation
Wrap `r.Body` with a bounded reader (e.g., `io.LimitReader(r.Body, int64(common.MaxRequestContentLength+1))` or `http.MaxBytesReader`) before calling `io.ReadAll` in `getRPCRequests` (`networks/rpc/http_newrelic.go`), and detect/reject bodies exceeding the limit early, mirroring the protection already present in `newHTTPServerConn`/`validateRequest`. Apply the same fix to the Datadog handler's identical call path.

### Proof of Concept
1. Start a kaia node with `NEWRELIC_APP_NAME` and `NEWRELIC_LICENSE` set (or Datadog tracer env vars), enabling the corresponding wrapping middleware in `NewHTTPServer`.
2. Send a POST request to the JSON-RPC HTTP endpoint using chunked transfer-encoding (omitting `Content-Length`) with a body of several GB of arbitrary JSON-RPC-shaped or garbage data.
3. Observe that `getRPCRequests` (`networks/rpc/http_newrelic.go:144-159`) calls `io.ReadAll(r.Body)` and buffers the entire multi-GB body into a Go byte slice before any size check runs, driving heap growth until the OS OOM-kills the node process — taking down RPC, sync, and all other in-process services.

### Citations

**File:** networks/rpc/http.go (L271-288)
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
```

**File:** networks/rpc/http_newrelic.go (L84-99)
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
		} else {
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
