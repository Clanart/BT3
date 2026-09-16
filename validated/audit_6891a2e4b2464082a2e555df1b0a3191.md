## Finding [1](#0-0) 

Kaia's JSON-RPC HTTP server enforces a request body cap of `MaxRequestContentLength` (512 KiB) via `validateRequest()` and a `LimitReader`-wrapped codec in the core `ServeHTTP` handler [2](#0-1) [3](#0-2) . However, when the optional NewRelic or Datadog tracing middlewares are enabled, they are wrapped **around** this size-limited handler and unconditionally read the entire raw request body into memory before the size check is ever reached. [4](#0-3) 

`newNewRelicHTTPHandler` and `newDatadogHTTPHandler` both call `getRPCRequests(r)` prior to invoking the inner (size-limited) handler: [5](#0-4) 

```go
func getRPCRequests(r *http.Request) ([]*jsonrpcMessage, bool, error) {
	reqBody, err := io.ReadAll(r.Body)   // <-- unbounded read, no MaxRequestContentLength check
	...
}
```

This `io.ReadAll` has no size limit at all — it is invoked before `validateRequest()`'s `r.ContentLength > MaxRequestContentLength` check [6](#0-5)  and before the `io.LimitReader`-bounded codec is constructed in `newHTTPServerConn` [3](#0-2) . The same unbounded pattern is duplicated in `http_datadog.go`, which calls the identical `getRPCRequests` before tracing/serving [7](#0-6) .

### Title
Unbounded request body read in NewRelic/Datadog RPC tracing middleware bypasses `MaxRequestContentLength`, enabling unauthenticated memory-exhaustion DoS - (File: `networks/rpc/http_newrelic.go`)

### Summary
When NewRelic (`NEWRELIC_APP_NAME`/`NEWRELIC_LICENSE`) or Datadog (`DD_TRACE_ENABLED`) tracing is enabled on the public JSON-RPC HTTP endpoint, `getRPCRequests()` reads the full HTTP request body with `io.ReadAll(r.Body)` with no size bound, before the RPC server's normal `MaxRequestContentLength` (512 KiB) enforcement in `validateRequest`/`newHTTPServerConn` executes.

### Finding Description
`NewHTTPServer` builds its handler chain as: `srv` (size-checked RPC handler) → CORS/vhost/timeout wrappers → optional NewRelic wrapper → optional Datadog wrapper [8](#0-7) . Because the tracing wrappers sit outermost, they receive the raw, un-truncated `http.Request` body. Both `newNewRelicHTTPHandler` [9](#0-8)  and `newDatadogHTTPHandler` [10](#0-9)  call `getRPCRequests(r)` to peek at the RPC method name for tracing purposes, which fully buffers `r.Body` into memory via `io.ReadAll` with no `io.LimitReader` and no content-length pre-check [11](#0-10) . Only after this unbounded allocation does control reach `handler.ServeHTTP(...)`, where the legitimate `validateRequest` 512 KiB cap and `LimitReader`-based codec would otherwise apply [12](#0-11) . This is structurally identical to the reported Rancher bug class: a middleware positioned earlier in the handler chain performs an unbounded body copy/read, bypassing the downstream body-size cap.

### Impact Explanation
Any unauthenticated caller to the public JSON-RPC HTTP endpoint can send an arbitrarily large POST body. Each such request forces the node to allocate memory proportional to the attacker-supplied body size, fully bypassing the intended 512 KiB `MaxRequestContentLength` guard [13](#0-12) . A small number of concurrent large-body requests can exhaust available memory and crash or OOM-kill the node process, taking down RPC availability for the affected Kaia endpoint node — a public, network-reachable, unauthenticated Denial-of-Service.

### Likelihood Explanation
The vulnerable code path is only active when the operator has enabled NewRelic (`NEWRELIC_APP_NAME`/`NEWRELIC_LICENSE`) or Datadog (`DD_TRACE_ENABLED=true`) tracing for the RPC HTTP server, which is a supported, documented configuration option rather than a default-disabled experimental path. Once enabled, exploitation requires only unauthenticated HTTP POST requests with large bodies to the public RPC port — no special privileges, accounts, or transactions are required.

### Recommendation
Wrap `r.Body` with an `io.LimitReader(r.Body, common.MaxRequestContentLength)` (or use `http.MaxBytesReader`) before calling `getRPCRequests` in both `newNewRelicHTTPHandler` and `newDatadogHTTPHandler`, and/or move the size validation (`validateRequest`) to execute before any tracing middleware runs, so the bound is enforced regardless of handler-chain ordering.

### Proof of Concept
1. Start a Kaia node's HTTP RPC server with `NEWRELIC_APP_NAME`/`NEWRELIC_LICENSE` (or `DD_TRACE_ENABLED=true`) set.
2. Send an unauthenticated `POST` request to the RPC endpoint with a body several hundred MB to multiple GB in size (Content-Type `application/json`), e.g. via `curl --data-binary @largefile http://node:8551`.
3. Observe that `getRPCRequests` in `networks/rpc/http_newrelic.go` (or `http_datadog.go`) calls `io.ReadAll(r.Body)` and buffers the entire payload into memory before any size validation, unlike a normal request which would be rejected by `validateRequest` at 512 KiB.
4. Repeating with several concurrent large-body connections drives memory usage up until the process is OOM-killed, denying RPC service.

### Citations

**File:** common/variables.go (L19-23)
```go
package common

var MaxRequestContentLength = 1024 * 512


```

**File:** networks/rpc/http.go (L249-253)
```go
func newHTTPServerConn(r *http.Request, w http.ResponseWriter) ServerCodec {
	body := io.LimitReader(r.Body, int64(common.MaxRequestContentLength))
	conn := &httpServerConn{Reader: body, Writer: w, r: r}
	return NewCodec(conn)
}
```

**File:** networks/rpc/http.go (L268-296)
```go
// NewHTTPServer creates a new HTTP RPC server around an API provider.
//
// Deprecated: Server implements http.Handler
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

**File:** networks/rpc/http.go (L348-374)
```go
func (s *Server) ServeHTTP(w http.ResponseWriter, r *http.Request) {
	// Permit dumb empty requests for remote health-checks (AWS)
	if r.Method == http.MethodGet && r.ContentLength == 0 && r.URL.RawQuery == "" {
		return
	}
	if code, err := validateRequest(r); err != nil {
		http.Error(w, err.Error(), code)
		return
	}
	// All checks passed, create a codec that reads direct from the request body
	// untilEOF and writes the response to w and order the server to process a
	// single request.
	ctx := r.Context()
	ctx = context.WithValue(ctx, "remote", r.RemoteAddr)
	ctx = context.WithValue(ctx, "scheme", r.Proto)
	ctx = context.WithValue(ctx, "local", r.Host)
	if ua := r.Header.Get("User-Agent"); ua != "" {
		ctx = context.WithValue(ctx, "User-Agent", ua)
	}
	if origin := r.Header.Get("Origin"); origin != "" {
		ctx = context.WithValue(ctx, "Origin", origin)
	}

	w.Header().Set("content-type", contentType)
	codec := newHTTPServerConn(r, w)
	defer codec.close()
	s.ServeSingleRequest(ctx, codec)
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

**File:** networks/rpc/http_newrelic.go (L84-104)
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

**File:** networks/rpc/http_datadog.go (L86-101)
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
```
