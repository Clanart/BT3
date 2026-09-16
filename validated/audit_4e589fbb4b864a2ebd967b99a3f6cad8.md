Confirmed: `getRPCRequests` in both `networks/rpc/http_newrelic.go` and `networks/rpc/http_datadog.go` calls `io.ReadAll(r.Body)` directly on the raw HTTP request body, and this middleware wraps the handler chain **outside** (i.e., executes *before*) the innermost `srv.ServeHTTP`, where `validateRequest`/`common.MaxRequestContentLength` is actually enforced. [1](#0-0) [2](#0-1) [3](#0-2) 

### Title
Unbounded HTTP RPC request-body read bypasses `MaxRequestContentLength` when NewRelic/Datadog tracing is enabled - (File: networks/rpc/http_newrelic.go, networks/rpc/http_datadog.go)

### Summary
Kaia's JSON-RPC HTTP endpoint enforces a request-body size cap (`common.MaxRequestContentLength`, default 512 KiB) via `validateRequest`/`validateFastRequest` inside `Server.ServeHTTP`/`HandleFastHTTP` [4](#0-3) , and the body reader passed to the JSON-RPC codec is wrapped with `io.LimitReader(r.Body, MaxRequestContentLength)` [5](#0-4) . However, when NewRelic APM or Datadog tracing is enabled (via `NEWRELIC_APP_NAME`/`NEWRELIC_LICENSE` or `DD_TRACE_ENABLED` environment variables), `NewHTTPServer` wraps the entire handler chain with `newNewRelicHTTPHandler`/`newDatadogHTTPHandler` as the outermost layer [6](#0-5) . These wrapper handlers call `getRPCRequests(r)` **before** invoking the inner handler, and `getRPCRequests` performs `io.ReadAll(r.Body)` with no size bound whatsoever [7](#0-6) .

### Finding Description
This is directly analogous to CVE-2020-15839: Liferay failed to restrict the size of a `multipart/form-data` POST body, allowing unbounded upload/allocation. Here, Kaia's public JSON-RPC HTTP server does implement a body-size restriction (`MaxRequestContentLength`), but that restriction is applied only at the innermost `Server.ServeHTTP`/`HandleFastHTTP` layer via `validateRequest` (which checks the declared `Content-Length` header, not the actual streamed body size) and `io.LimitReader`. When request-tracing integrations are enabled, `getRPCRequests` runs first and unconditionally buffers the entire client-supplied body into memory with `io.ReadAll(r.Body)`, with no `http.MaxBytesReader`, no length check, and no `LimitReader`. An attacker can send a request using chunked transfer-encoding (so `r.ContentLength` is unset/`-1`, causing `validateRequest`'s size check to be ineffective when it eventually runs) with an arbitrarily large body. Because `getRPCRequests` reads the whole body before any size restriction is applied, this happens irrespective of the downstream `validateRequest` check, entirely defeating the intended cap.

### Impact Explanation
Any unauthenticated, unprivileged public-RPC caller can send oversized POST bodies to the JSON-RPC HTTP endpoint, forcing the node to allocate arbitrarily large amounts of memory per request via `io.ReadAll`. Repeated/concurrent requests can exhaust node memory, causing crashes or severe service degradation of full nodes, consensus nodes' RPC/monitoring plane, or public RPC gateways — a resource-exhaustion denial-of-service matching the "unrestricted upload" bug class and CWE-434/CVSS `AV:N/AC:L/PR:L/UI:N/S:U/C:N/I:N/A:H` profile of the referenced advisory (availability impact only).

### Likelihood Explanation
Exploitability is gated on the operator having enabled NewRelic or Datadog tracing via environment variables (`newNewRelicApp()`/`newDatadogTracer()` return non-nil only in that case) [8](#0-7) [9](#0-8) . These are commonly enabled in production observability setups for infrastructure/RPC providers. Once enabled, exploitation requires only a single crafted HTTP POST from any public caller — no authentication, special privileges, or races are needed.

### Recommendation
Wrap `r.Body` with `http.MaxBytesReader(w, r.Body, int64(common.MaxRequestContentLength))` (or an equivalent `io.LimitReader`) before calling `io.ReadAll` inside `getRPCRequests` in both `networks/rpc/http_newrelic.go` and `networks/rpc/http_datadog.go`, so the size restriction is enforced at the very first point the body is consumed, regardless of which handler layer executes first.

### Proof of Concept
1. Start a Kaia node with the HTTP RPC endpoint enabled and set `NEWRELIC_APP_NAME`/`NEWRELIC_LICENSE` (or `DD_TRACE_ENABLED=true`) so `newNewRelicHTTPHandler`/`newDatadogHTTPHandler` wraps the RPC handler.
2. From a remote unauthenticated client, send an HTTP POST to the JSON-RPC endpoint using `Transfer-Encoding: chunked` (omitting `Content-Length`) with a body far exceeding `common.MaxRequestContentLength` (e.g., several hundred MB of padding data before/around a JSON-RPC payload).
3. Observe that `getRPCRequests` → `io.ReadAll(r.Body)` buffers the entire oversized body into memory before `validateRequest`'s `Content-Length` check is ever reached, unlike a request sent without the tracing handler enabled (which is rejected early via `io.LimitReader`/`validateRequest`).
4. Repeating the request concurrently drives node memory usage up, demonstrating the DoS bypass of the intended `MaxRequestContentLength` restriction.

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

**File:** networks/rpc/http_newrelic.go (L60-80)
```go
func newNewRelicApp() *newrelic.Application {
	appName := os.Getenv("NEWRELIC_APP_NAME")
	license := os.Getenv("NEWRELIC_LICENSE")
	if appName == "" && license == "" {
		return nil
	}

	nrApp, err := newrelic.NewApplication(
		newrelic.ConfigAppName(appName),
		newrelic.ConfigLicense(license),
		newrelic.ConfigDistributedTracerEnabled(true),
	)
	if err != nil {
		logger.Crit("failed to create NewRelic application. If you want to register a NewRelic HTTP handler," +
			" specify NEWRELIC_APP_NAME and NEWRELIC_LICENSE os environment variables with valid values. " +
			"If you don't want to register the handler, specify them with an empty string.")
	}

	logger.Info("NewRelic APM is enabled", "appName", appName)
	return nrApp
}
```

**File:** networks/rpc/http_newrelic.go (L82-159)
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

		// print RPC error logs if errors exist
		if isBatch {
			var rpcReturns []interface{}
			if err := json.Unmarshal(dupW.body.Bytes(), &rpcReturns); err == nil {
				for i, rpcReturn := range rpcReturns {
					if data, err := json.Marshal(rpcReturn); err == nil {
						// TODO-Kaia: make the log level configurable or separate module name of the logger
						printRPCErrorLog(data, reqs[i].Method, r)
					}
				}
			}
		} else {
			// TODO-Kaia: make the log level configurable or separate module name of the logger
			printRPCErrorLog(dupW.body.Bytes(), reqMethod, r)
		}
	})
}

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

**File:** networks/rpc/http_datadog.go (L43-51)
```go
func newDatadogTracer() *DatadogTracer {
	v := os.Getenv("DD_TRACE_ENABLED")
	if v == "" {
		return nil
	}

	if ddTraceEnabled, err := strconv.ParseBool(v); ddTraceEnabled == false || err != nil {
		return nil
	}
```
