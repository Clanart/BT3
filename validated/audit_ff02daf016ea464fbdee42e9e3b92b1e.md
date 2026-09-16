Confirmed root cause. The `getRPCRequests` helper reads the entire HTTP request body with `io.ReadAll(r.Body)` — no size cap, no `Content-Length` check — and this runs in the NewRelic and Datadog APM middleware wrappers, which are installed *outside* (before) the `Server.ServeHTTP` handler that performs `validateRequest` (the `MaxRequestContentLength` check) and applies `io.LimitReader` in `newHTTPServerConn`.

### Title
Unbounded JSON-RPC request body read in NewRelic/Datadog APM middleware bypasses `MaxRequestContentLength`, enabling memory-exhaustion DoS against public RPC - (File: networks/rpc/http_newrelic.go, networks/rpc/http_datadog.go)

### Summary
When a node operator enables the built-in NewRelic (`NEWRELIC_APP_NAME`/`NEWRELIC_LICENSE`) or Datadog (`DD_TRACE_ENABLED`) APM integrations, `NewHTTPServer` wraps the JSON-RPC handler with `newNewRelicHTTPHandler`/`newDatadogHTTPHandler` [1](#0-0) . Both wrappers call `getRPCRequests(r)` before delegating to the inner handler, and `getRPCRequests` reads the full request body via `io.ReadAll(r.Body)` with no upper bound [2](#0-1) . This happens before `Server.ServeHTTP` ever runs `validateRequest`, which is where the `common.MaxRequestContentLength` (512 KiB) check and the `io.LimitReader`-wrapped codec are applied [3](#0-2) [4](#0-3) . Any unauthenticated public-RPC caller can therefore send an arbitrarily large POST body to bypass the intended size cap.

### Finding Description
Kaia's HTTP JSON-RPC path is designed so every body read is bounded: `newHTTPServerConn` wraps `r.Body` in `io.LimitReader(r.Body, int64(common.MaxRequestContentLength))` [5](#0-4) , and `validateRequest` additionally rejects any request whose declared `Content-Length` exceeds `common.MaxRequestContentLength` before the body is even touched [6](#0-5) . `common.MaxRequestContentLength` defaults to 512 KiB [7](#0-6) .

However, `NewHTTPServer` conditionally layers APM instrumentation handlers *outside* the handler chain that performs this validation:

```go
handler = http.TimeoutHandler(handler, timeouts.ExecutionTimeout, "timeout")
nrApp := newNewRelicApp()
if nrApp != nil {
    handler = newNewRelicHTTPHandler(nrApp, handler)
}
ddTracer := newDatadogTracer()
if ddTracer != nil {
    handler = newDatadogHTTPHandler(ddTracer, handler)
}
``` [1](#0-0) 

Both `newNewRelicHTTPHandler` and `newDatadogHTTPHandler` call `getRPCRequests(r)` to extract the method name for tracing/logging purposes, prior to invoking `handler.ServeHTTP` (the real request path that contains `validateRequest`) [8](#0-7) [9](#0-8) . `getRPCRequests` itself performs an unbounded read:

```go
func getRPCRequests(r *http.Request) ([]*jsonrpcMessage, bool, error) {
	reqBody, err := io.ReadAll(r.Body)
	...
``` [10](#0-9) 

`io.ReadAll` has no size limit and will grow a Go slice to accommodate whatever the client streams, exactly the class of bug described in the reference advisory (unbounded `JSON.parse`/body read with no `Content-Length` pre-check). Since `r.ContentLength` is never checked here (no `validateRequest`-equivalent guard runs first), a client can either declare a huge `Content-Length` or stream a chunked/unbounded body, forcing the node process to allocate memory proportional to the attacker-controlled payload size before any size validation occurs.

### Impact Explanation
Any unauthenticated public-RPC caller reaching the HTTP JSON-RPC endpoint of a node that has NewRelic or Datadog APM enabled (a supported, documented operational configuration, not a hypothetical) can send a single oversized POST to force uncontrolled memory allocation in the node's RPC-serving goroutine. Repeated or concurrent requests can exhaust available memory and crash or OOM-kill the node process, denying service to legitimate transaction submitters, dApp backends, and other public RPC consumers — directly analogous to the CWE-770 impact in the reference advisory. This degrades availability of a Kaia full/endpoint node's public RPC surface with no authentication or prior interaction required.

### Likelihood Explanation
Likelihood is dependent on operator configuration: the vulnerable code path is only active when `NEWRELIC_APP_NAME`/`NEWRELIC_LICENSE` or `DD_TRACE_ENABLED` environment variables are set, which is a common practice for production RPC endpoint operators (e.g., KAS-style deployments, given the presence of `KASAttrs`/`parseKASHeader` in the same file) [11](#0-10) . When enabled, exploitation requires nothing beyond TCP reachability to the RPC port and a single crafted HTTP POST — no special privileges, valid transaction, or prior session.

### Recommendation
Apply the same bound used elsewhere in the RPC stack to `getRPCRequests`: wrap `r.Body` with `io.LimitReader(r.Body, int64(common.MaxRequestContentLength))` before calling `io.ReadAll`, and/or invoke `validateRequest`-equivalent `Content-Length` checks at the very start of `newNewRelicHTTPHandler`/`newDatadogHTTPHandler` before any body read occurs, rejecting oversized requests with `413 Request Entity Too Large` exactly as `validateRequest` already does for the non-APM path [12](#0-11) .

### Proof of Concept
1. Start a Kaia node with the HTTP RPC server enabled and either `NEWRELIC_APP_NAME`+`NEWRELIC_LICENSE` or `DD_TRACE_ENABLED=true` set, so `newNewRelicHTTPHandler`/`newDatadogHTTPHandler` is installed per `NewHTTPServer` [1](#0-0) .
2. As an unauthenticated remote client, send a single `POST /` to the RPC endpoint with a `Content-Length` far exceeding `common.MaxRequestContentLength` (512 KiB), e.g. several hundred MB, either via a large `Content-Length` with a matching body or via chunked transfer encoding.
3. Because `getRPCRequests` runs `io.ReadAll(r.Body)` before `validateRequest` is ever invoked [13](#0-12) , the node allocates memory proportional to the full attacker-supplied body size, unlike the non-APM path where the same request would be rejected at `validateRequest`'s `Content-Length` check [12](#0-11)  or truncated by `io.LimitReader` [14](#0-13) .
4. Repeating the request (or sending it concurrently) drives the node's RSS up until the OS OOM-kills the process or memory pressure degrades service for all other RPC clients.

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

**File:** networks/rpc/http.go (L348-375)
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
}
```

**File:** networks/rpc/http.go (L409-432)
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
	// Allow OPTIONS (regardless of content-type)
	if r.Method == http.MethodOptions {
		return 0, nil
	}
	// Check content-type
	if mt, _, err := mime.ParseMediaType(r.Header.Get("content-type")); err == nil {
		if slices.Contains(acceptedContentTypes, mt) {
			return 0, nil
		}
	}
	// Invalid content-type
	err := fmt.Errorf("invalid content type, only %s is supported", contentType)
	return http.StatusUnsupportedMediaType, err
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

**File:** networks/rpc/http_newrelic.go (L82-121)
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

**File:** common/variables.go (L21-21)
```go
var MaxRequestContentLength = 1024 * 512
```

**File:** networks/rpc/http_datadog.go (L86-136)
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

		// datadog transaction name contains the first API method of the request
		resource := fmt.Sprintf("%s %s %s", r.Method, r.URL.String(), reqMethod)

		// duplicate writer
		dupW := &dupWriter{
			ResponseWriter: w,
			body:           bytes.NewBufferString(""),
		}

		spanOpts := []ddtrace.StartSpanOption{
			tracer.Tag("request.method", reqMethod),
			tracer.Tag("request.params", reqParam),
		}

		for _, ti := range ddTracer.Tags {
			var tag tracer.StartSpanOption
			if ti.header == "remote_addr" {
				tag = tracer.Tag(ti.key, r.RemoteAddr)
			} else {
				tag = tracer.Tag(ti.key, r.Header.Get(ti.header))
			}
			spanOpts = append(spanOpts, tag)
		}

		responseHandler := http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
			handler.ServeHTTP(w, r)
```
