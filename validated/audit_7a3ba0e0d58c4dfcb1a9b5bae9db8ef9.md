## Title
Unbounded request body buffering in NewRelic RPC instrumentation bypasses `MaxRequestContentLength` cap, enabling memory exhaustion by public RPC callers - (File: `networks/rpc/http_newrelic.go`)

### Summary
The Apache Traffic Server CVE describes a bug class where a per-stream/per-request buffer size cap is dropped during body decoding, letting a slow/unbounded client exhaust server memory. Kaia's HTTP JSON-RPC server has an analogous flaw: when NewRelic instrumentation is enabled, the outermost handler `newNewRelicHTTPHandler` reads the entire HTTP request body into memory with `io.ReadAll(r.Body)` in `getRPCRequests`, before the inner handler chain ever applies the `common.MaxRequestContentLength` cap.

### Finding Description
Kaia's normal HTTP RPC path enforces a body-size cap in two ways:
- `validateRequest` rejects requests whose `Content-Length` header exceeds `common.MaxRequestContentLength` (512 KiB) [1](#0-0) 
- `newHTTPServerConn` wraps the body in `io.LimitReader(r.Body, int64(common.MaxRequestContentLength))` before it is consumed [2](#0-1) 

However, when NewRelic monitoring is configured (`NEWRELIC_APP_NAME`/`NEWRELIC_LICENSE`), the server wraps the whole handler chain with `newNewRelicHTTPHandler`, which runs *before* `validateRequest`/`newHTTPServerConn`: [3](#0-2) 

Inside that handler, `getRPCRequests` reads the complete request body with no size limit at all: [4](#0-3) 

Since `r.ContentLength` is attacker-controlled (and is `-1`/unknown for chunked or unspecified bodies), `validateRequest`'s check on `r.ContentLength` never runs against the actual bytes transferred, and `getRPCRequests`'s `io.ReadAll` has no `io.LimitReader` wrapper, no `MaxRequestContentLength` cap, and no incremental buffer ceiling — mirroring the CVE's description of a per-request buffer cap being dropped. A caller can stream an arbitrarily large (or chunked, slow) body; the server buffers the entire thing in memory before any size validation occurs.

### Impact Explanation
Because this handler sits at the top of the middleware chain for every JSON-RPC HTTP request, any unauthenticated/public RPC caller can trigger unbounded in-memory buffering per request. Concurrent slow/large requests can exhaust server memory, causing denial of service to the public RPC endpoint, block production/consensus-adjacent services sharing the same process, and other RPC consumers (fee-delegation callers, gasless/auction bidders, etc. depending on deployment). This matches the CVE's "memory exhaustion via dropped buffer cap" impact class and fits the required "public RPC caller" reachable path.

### Likelihood Explanation
Exploitation only requires sending a normal-looking POST to the RPC endpoint with a large or chunked body — no special privileges, valid signatures, or protocol knowledge beyond HTTP are needed. The condition is gated on NewRelic instrumentation being enabled via environment variables, which is a supported production configuration option (used for observability in KAS-style deployments), not an edge case.

### Recommendation
Wrap `r.Body` with `io.LimitReader(r.Body, int64(common.MaxRequestContentLength))` (or reuse `newHTTPServerConn`'s existing capped reader) before calling `io.ReadAll` in `getRPCRequests`, and/or move body-size validation (`validateRequest`) ahead of the NewRelic middleware in the handler chain so oversized bodies are rejected before any full-body buffering occurs.

### Proof of Concept
1. Deploy a Kaia node with `NEWRELIC_APP_NAME` and `NEWRELIC_LICENSE` set so `newNewRelicHTTPHandler` is registered.
2. Send an HTTP POST to the JSON-RPC endpoint using `Transfer-Encoding: chunked` (or a very large `Content-Length`) with a multi-hundred-MB body, at low bandwidth to remain under `ReadTimeout`.
3. Observe that `getRPCRequests` unconditionally buffers the full body via `io.ReadAll(r.Body)` before `validateRequest`'s size check is applied downstream, and that repeating this from multiple connections drives server memory usage up without bound relative to `common.MaxRequestContentLength`.

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

**File:** networks/rpc/http_newrelic.go (L144-151)
```go
func getRPCRequests(r *http.Request) ([]*jsonrpcMessage, bool, error) {
	reqBody, err := io.ReadAll(r.Body)
	if err != nil {
		logger.Error("cannot read a request body", "err", err)
		return nil, false, err
	}

	r.Body = io.NopCloser(bytes.NewReader(reqBody))
```
