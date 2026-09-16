### Title
Unbounded HTTP request body read in RPC APM instrumentation bypasses `MaxRequestContentLength` and enables memory-exhaustion DoS - (File: networks/rpc/http_newrelic.go, networks/rpc/http_datadog.go)

### Summary
When New Relic (`NEWRELIC_APP_NAME`/`NEWRELIC_LICENSE`) or Datadog (`DD_TRACE_ENABLED`) instrumentation is enabled on the JSON-RPC HTTP server, every incoming request is first passed through `getRPCRequests(r)`, which calls `io.ReadAll(r.Body)` with no size limit, before the server's normal `Content-Length`/`MaxRequestContentLength` validation ever runs. This mirrors the `am_read_post_data` flaw in CVE-2016-2146, where POST data was read without any bound, enabling worker crash / memory exhaustion DoS.

### Finding Description
The RPC HTTP server is supposed to bound request bodies to `common.MaxRequestContentLength` (512 KiB) via `validateRequest`'s `Content-Length` check and via `io.LimitReader` in `newHTTPServerConn`: [1](#0-0) [2](#0-1) 

However, when APM instrumentation is enabled, `NewHTTPServer` wraps the entire handler chain (including the code that performs the above validation) inside `newNewRelicHTTPHandler` or `newDatadogHTTPHandler`: [3](#0-2) 

Both of these outer handlers unconditionally call `getRPCRequests(r)` *before* invoking the inner `handler.ServeHTTP` (where the real size check lives): [4](#0-3) [5](#0-4) 

`getRPCRequests` itself reads the full body with no bound at all: [6](#0-5) 

`io.ReadAll(r.Body)` will buffer the entire request body in memory regardless of `Content-Length` (an attacker can also send `Transfer-Encoding: chunked` with no declared length, or lie about `Content-Length`, since Go's `net/http` server does not enforce a length limit on its own). The `MaxRequestContentLength` enforcement in `validateRequest` and `newHTTPServerConn`'s `io.LimitReader` only run afterward, inside the wrapped inner handler — by then the unbounded read has already completed and the memory has already been allocated/consumed.

### Impact Explanation
Any unprivileged public-RPC caller can send arbitrarily large (or slow, chunked, unbounded) POST bodies to a Kaia node's JSON-RPC HTTP endpoint. Because reading happens fully in memory with no cap, concurrent requests with large bodies can drive the node to excessive memory consumption, triggering OOM kill, GC thrashing, or worker crash — a denial of service against the node's public RPC service, directly analogous to the availability impact in CVE-2016-2146 (CVSS A:H). This affects any Kaia node operator who enables the built-in New Relic or Datadog APM integration, which is a normal production configuration, not an adversarial one.

### Likelihood Explanation
Exploitation requires no authentication or special privilege — a single POST request to the RPC HTTP endpoint is sufficient, and the only precondition is that the node has New Relic or Datadog tracing enabled via environment variables, which is a common, supported production deployment pattern documented in the codebase itself.

### Recommendation
Enforce the same request-size bound in `getRPCRequests` that is applied elsewhere, e.g., wrap `r.Body` with `io.LimitReader(r.Body, int64(common.MaxRequestContentLength))` (or use `http.MaxBytesReader`) before calling `io.ReadAll`, and reject/short-circuit when the limit is exceeded, mirroring the check in `validateRequest`. Apply this consistently in both `http_newrelic.go` and `http_datadog.go` so that APM instrumentation cannot bypass the content-length limit enforced by the base RPC server.

### Proof of Concept
1. Start a Kaia node with `NEWRELIC_APP_NAME`/`NEWRELIC_LICENSE` (or `DD_TRACE_ENABLED=true`) set so `newNewRelicHTTPHandler`/`newDatadogHTTPHandler` wraps the RPC HTTP server.
2. From an unprivileged client, send a POST request to the JSON-RPC HTTP endpoint with a very large body (e.g., multiple GB, optionally via chunked transfer-encoding without a `Content-Length` header) — e.g. `curl -X POST --data-binary @large_payload.json http://node:8551`.
3. Observe that `getRPCRequests` in `networks/rpc/http_newrelic.go`/`http_datadog.go` calls `io.ReadAll(r.Body)` and buffers the full payload in memory before the `MaxRequestContentLength` check in `validateRequest` ever executes, unlike a request sent when APM is disabled (which is correctly rejected with `413 Request Entity Too Large` per `TestHTTPErrorResponseWithMaxContentLength` in `networks/rpc/http_test.go`).
4. Repeating with concurrent large/slow requests exhausts node memory, producing a DoS.

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

**File:** networks/rpc/http.go (L415-418)
```go
	if r.ContentLength > int64(common.MaxRequestContentLength) {
		err := fmt.Errorf("content length too large (%d>%d)", r.ContentLength, int64(common.MaxRequestContentLength))
		return http.StatusRequestEntityTooLarge, err
	}
```

**File:** networks/rpc/http_newrelic.go (L94-98)
```go
		// parse RPC requests
		reqs, isBatch, err := getRPCRequests(r)
		if err != nil || len(reqs) < 1 {
			// The error will be handled in `handler.ServeHTTP()` and printed with `printRPCErrorLog()`
			logger.Debug("failed to parse RPC request", "err", err, "len(reqs)", len(reqs))
```

**File:** networks/rpc/http_newrelic.go (L144-149)
```go
func getRPCRequests(r *http.Request) ([]*jsonrpcMessage, bool, error) {
	reqBody, err := io.ReadAll(r.Body)
	if err != nil {
		logger.Error("cannot read a request body", "err", err)
		return nil, false, err
	}
```

**File:** networks/rpc/http_datadog.go (L97-101)
```go
		// parse RPC requests
		reqs, isBatch, err := getRPCRequests(r)
		if err != nil || len(reqs) < 1 {
			// The error will be handled in `handler.ServeHTTP()` and printed with `printRPCErrorLog()`
			logger.Debug("failed to parse RPC request", "err", err, "len(reqs)", len(reqs))
```
