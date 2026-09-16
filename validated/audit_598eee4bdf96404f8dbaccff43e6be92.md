The main HTTP RPC entry point already enforces `common.MaxRequestContentLength` both via header check and via `io.LimitReader` before decoding, so it does not reproduce the Twisted unbounded-read pattern.### Title
Unbounded read of the HTTP RPC request body before size validation in NewRelic instrumentation causes memory-exhaustion DoS - (File: networks/rpc/http_newrelic.go)

### Summary
When APM instrumentation (`NEWRELIC_APP_NAME`/`NEWRELIC_LICENSE`) is enabled, the outer `newNewRelicHTTPHandler` wrapper reads the entire HTTP request body into memory with `io.ReadAll(r.Body)` **before** any content-length validation or size-limiting is applied. This mirrors the Twisted SSH version-banner CVE-2022-21716 pattern: unbounded consumption of attacker-controlled input into memory prior to any bound/validation check, allowing a remote unauthenticated caller to exhaust node memory.

### Finding Description
The normal JSON-RPC HTTP path enforces a request-size limit twice: `validateRequest` rejects any request whose `Content-Length` exceeds `common.MaxRequestContentLength`, and `newHTTPServerConn` wraps the body in `io.LimitReader(r.Body, int64(common.MaxRequestContentLength))` before it is ever decoded: [1](#0-0) [2](#0-1) 

However, `NewHTTPServer` wraps the entire handler chain (CORS → vhost → timeout → `ServeHTTP`) inside `newNewRelicHTTPHandler`, which executes **first** for every incoming request when NewRelic env vars are configured: [3](#0-2) 

That handler calls `getRPCRequests(r)` to derive a transaction name/log label, and this function reads the full body unconditionally, with no `Content-Length` check and no `io.LimitReader`: [4](#0-3) [5](#0-4) 

Because `io.ReadAll` has no upper bound and runs *before* `validateRequest`'s `Content-Length` check (which only happens later, inside `handler.ServeHTTP(dupW, r)`), an attacker can:
- send a POST with a spoofed/absent `Content-Length` and a chunked-encoded body of unbounded size, or
- simply stream an extremely large body regardless of the eventual `Content-Length` check, since the buffering already happened in `getRPCRequests`.

This is functionally identical to the Twisted defect: an unbounded `read()`/buffer accumulation on network-supplied data occurring ahead of the protocol's own size-limiting logic, leading to memory exhaustion and node crash (denial of service). Unlike the excluded p2p/handshake bug classes, this path is reachable via the standard public JSON-RPC HTTP endpoint by any unauthenticated caller — no special network position, validator role, or peer relationship is required.

### Impact Explanation
A single unauthenticated HTTP POST to any Kaia public RPC endpoint (`ken`/`kcn`/`kpn` JSON-RPC HTTP servers) with an oversized or infinitely-chunked body can force the node to buffer the entire payload in memory via `io.ReadAll`, independent of the node's configured `MaxRequestContentLength`. Repeated or concurrent requests can exhaust available memory and crash the RPC-serving node, disrupting transaction submission, fee-delegation flows, gasless/auction settlement, and any other RPC-dependent service on that node — a Denial of Service consistent with CWE-770/CWE-120.

### Likelihood Explanation
The vulnerable code path is only active when the node operator has configured NewRelic APM (`NEWRELIC_APP_NAME` and `NEWRELIC_LICENSE` environment variables), which is common in production deployments (e.g., managed/hosted RPC providers) for observability. When active, exploitation requires only a single crafted HTTP request from any external caller — no authentication, staking, or special privileges needed, making likelihood high whenever the feature is enabled.

### Recommendation
- Apply the same `io.LimitReader(r.Body, common.MaxRequestContentLength)` bound (or equivalent) in `getRPCRequests` before calling `io.ReadAll`, matching the protection already used in `newHTTPServerConn`.
- Move/duplicate the `Content-Length` / size validation (`validateRequest`) so it executes before the NewRelic (and Datadog, if similarly implemented) wrapper reads the body, or have the wrapper reuse the already-limited body reader instead of reading `r.Body` directly a second time.

### Proof of Concept
1. Deploy/point a Kaia `ken` node with `NEWRELIC_APP_NAME` and `NEWRELIC_LICENSE` set (enabling `newNewRelicHTTPHandler`).
2. Send an HTTP POST to the JSON-RPC endpoint with `Transfer-Encoding: chunked` and continuously stream chunks of arbitrary data (e.g., zero bytes) without ever terminating the chunked body, or send a body far larger than `common.MaxRequestContentLength` while suppressing/mismatching the `Content-Length` header.
3. Observe that `getRPCRequests` → `io.ReadAll(r.Body)` in `networks/rpc/http_newrelic.go` accumulates the entire stream in memory before `validateRequest`'s size check is ever reached, growing the node's memory usage unboundedly and eventually causing OOM/crash.

### Citations

**File:** networks/rpc/http.go (L249-253)
```go
func newHTTPServerConn(r *http.Request, w http.ResponseWriter) ServerCodec {
	body := io.LimitReader(r.Body, int64(common.MaxRequestContentLength))
	conn := &httpServerConn{Reader: body, Writer: w, r: r}
	return NewCodec(conn)
}
```

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

**File:** networks/rpc/http_newrelic.go (L82-99)
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
