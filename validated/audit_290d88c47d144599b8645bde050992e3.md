Confirmed: `StartHTTPEndpoint` in `networks/rpc/endpoints.go` is the production path, and it calls `NewHTTPServer(cors, vhosts, timeouts, handler).Serve(listener)` [1](#0-0) , which is the same `NewHTTPServer` that installs the NewRelic/Datadog wrappers when the corresponding env vars are set [2](#0-1) .

### Title
Unbounded in-memory buffering of JSON-RPC HTTP request bodies before size validation when APM tracing is enabled - (File: `networks/rpc/http_newrelic.go`, `networks/rpc/http_datadog.go`)

### Summary
When the Kaia node's JSON-RPC HTTP endpoint is started with NewRelic (`NEWRELIC_APP_NAME`/`NEWRELIC_LICENSE`) or Datadog (`DD_TRACE_ENABLED`) APM tracing enabled, every inbound HTTP JSON-RPC request is fully buffered into memory via `io.ReadAll(r.Body)` with no size limit, before the existing `MaxRequestContentLength` (512 KiB) check is ever applied. This mirrors the Starlette CVE-2024-47874 bug class: request bodies are buffered unbounded in memory ahead of any size enforcement, enabling a denial-of-service via memory exhaustion.

### Finding Description
`NewHTTPServer` wraps the base RPC handler (`*Server`, whose `ServeHTTP` performs `validateRequest`, which rejects requests whose `Content-Length` exceeds `common.MaxRequestContentLength` = 512 KiB) with an *outer* handler when tracing is enabled: [3](#0-2) 

The outer wrapper, `newNewRelicHTTPHandler` (and analogously `newDatadogHTTPHandler`), calls `getRPCRequests(r)` **before** invoking the inner `handler.ServeHTTP(dupW, r)` that performs the `validateRequest` size check: [4](#0-3) [5](#0-4) 

`getRPCRequests` reads the entire request body into memory with no bound whatsoever: [6](#0-5) 

Because this read happens prior to `validateRequest`'s `r.ContentLength > MaxRequestContentLength` check [7](#0-6) , an attacker's request body of arbitrary size (e.g. multi-gigabyte, or an unbounded chunked-encoded stream where `r.ContentLength == -1`, which bypasses the length pre-check entirely) is fully materialized in memory by `io.ReadAll` regardless of the configured 512 KiB limit. The `net/http` server used by `NewHTTPServer` (unlike the `fasthttp` variant, which sets `MaxRequestBodySize`) has no `http.MaxBytesReader` or similar hard cap on `r.Body`, so nothing else in the stack stops the read.

This is structurally identical to the Starlette DoS: text/body fields (here, the raw JSON-RPC HTTP body) are read into an in-memory byte buffer with no size limit, ahead of size-limiting logic, allowing memory/CPU exhaustion from a single client.

### Impact Explanation
A single unprivileged HTTP client sending one or more large-bodied (or chunked, unbounded) POST requests to the public JSON-RPC endpoint can force the node to allocate and copy arbitrarily large byte buffers per request via `io.ReadAll`. Multiple concurrent requests can exhaust node memory, causing the process to slow to a crawl or be OOM-killed, disrupting RPC availability for all legitimate users, transaction submitters, and dependent services (e.g., wallets, dApps, explorers) relying on that node. This is a genuine node-availability DoS reachable from any external caller once the operator has enabled either supported APM integration — a supported, documented production feature, not a hypothetical misconfiguration.

### Likelihood Explanation
Exploitation requires only that the operator has set `NEWRELIC_APP_NAME`/`NEWRELIC_LICENSE` or `DD_TRACE_ENABLED=true` (both are documented, legitimate observability options, and the presence of `KASAttrs`/`x-chain-id` header parsing suggests this code path is used in real production deployments) [8](#0-7) . Once enabled, no authentication or special privilege is needed — any public RPC caller can send an oversized or chunked POST request to trigger unbounded buffering. The attack requires no knowledge of internal state and is trivially reproducible with a single `curl`-style request with a large or chunked body.

### Recommendation
Enforce a hard limit on the request body read in `getRPCRequests` (e.g., wrap `r.Body` with `http.MaxBytesReader(w, r.Body, common.MaxRequestContentLength)` or `io.LimitReader(r.Body, common.MaxRequestContentLength+1)` and reject oversized bodies) so that the APM tracing wrappers cannot read more than the configured `MaxRequestContentLength` regardless of `Content-Length` header or chunked encoding. Alternatively, move `validateRequest`'s size/content-type checks to execute before `getRPCRequests` is invoked in both `newNewRelicHTTPHandler` and `newDatadogHTTPHandler`, so tracing never reads an unvalidated, unbounded body.

### Proof of Concept
1. Start a Kaia node with an HTTP RPC endpoint enabled and set `NEWRELIC_APP_NAME=test`, `NEWRELIC_LICENSE=test` (or `DD_TRACE_ENABLED=true`) so `NewHTTPServer` installs the tracing wrapper [9](#0-8) .
2. Send a POST request to the RPC endpoint with a body far exceeding 512 KiB, either with a correct `Content-Length` header equal to the large size, or using `Transfer-Encoding: chunked` (so `r.ContentLength == -1`):
```sh
curl -X POST http://localhost:8551 \
  -H "Content-Type: application/json" \
  --data-binary @large_payload.json   # e.g. several hundred MB
```
3. Observe that `getRPCRequests` (`networks/rpc/http_newrelic.go:144-159`) reads the full body into memory via `io.ReadAll` before `validateRequest`'s length check ever executes, causing large memory allocation per request; repeating this concurrently drives the node toward memory exhaustion/OOM.

### Citations

**File:** networks/rpc/endpoints.go (L41-63)
```go
	handler := NewServer()
	for _, api := range apis {
		if api.Namespace == "klay" {
			api.Namespace = "kaia"
		}

		if !api.IPCOnly && (whitelist[api.Namespace] || (len(whitelist) == 0 && api.Public)) {
			if err := handler.RegisterName(api.Namespace, api.Service); err != nil {
				return nil, nil, err
			}
			logger.Debug("HTTP registered", "namespace", api.Namespace)
		}
	}
	// All APIs registered, start the HTTP listener
	var (
		listener net.Listener
		err      error
	)
	if listener, err = net.Listen("tcp", endpoint); err != nil {
		return nil, nil, err
	}
	go NewHTTPServer(cors, vhosts, timeouts, handler).Serve(listener)
	return listener, handler, err
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

**File:** networks/rpc/http_newrelic.go (L39-58)
```go
// KASAttrs contains identifications for a KAS request
type KASAttrs struct {
	ChainID      string `json:"x-chain-id"`
	AccountID    string `json:"x-account-id"`
	RequestID    string `json:"x-request-id"`
	ParentSpanID string `json:"x-b3-parentspanid,omitempty"`
	SpanID       string `json:"x-b3-spanid,omitempty"`
	TraceID      string `json:"x-b3-traceid,omitempty"`
}

func parseKASHeader(r *http.Request) KASAttrs {
	return KASAttrs{
		ChainID:      r.Header.Get("x-chain-id"),
		AccountID:    r.Header.Get("x-account-id"),
		RequestID:    r.Header.Get("x-request-id"),
		ParentSpanID: r.Header.Get("x-b3-parentspanid"),
		SpanID:       r.Header.Get("x-b3-spanid"),
		TraceID:      r.Header.Get("x-b3-traceid"),
	}
}
```

**File:** networks/rpc/http_newrelic.go (L84-121)
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
			reqMethod = reqs[0].Method
			if isBatch {
				reqMethod += "_batch"
			}
		}

		// new relic transaction name contains the first API method of the request
		txn := nrApp.StartTransaction(r.Method + " " + r.URL.String() + " " + reqMethod)
		defer txn.End()

		w = txn.SetWebResponse(w)
		txn.SetWebRequestHTTP(r)
		r = newrelic.RequestWithTransactionContext(r, txn)

		// duplicate writer
		dupW := &dupWriter{
			ResponseWriter: w,
			body:           bytes.NewBufferString(""),
		}

		// serve HTTP
		handler.ServeHTTP(dupW, r)
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

**File:** networks/rpc/http_datadog.go (L86-98)
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
```
