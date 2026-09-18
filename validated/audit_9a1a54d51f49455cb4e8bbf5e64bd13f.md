## Analysis

Confirmed root cause: the `[evm].deny_list` config option — the operator's mechanism to block specific EVM JSON-RPC methods from being served — is only wired into the HTTP path, not the WebSocket path.

- `HTTPConfig` carries a `DenyList []string` field [1](#0-0) , and `EnableRPC` registers every entry via `srv.RegisterDenyList(method)` before serving HTTP requests [2](#0-1) .
- `WsConfig` has **no** `DenyList` field at all [3](#0-2) , and `EnableWS` never calls `RegisterDenyList` on the WebSocket `rpc.Server` instance [4](#0-3) .
- `NewEVMHTTPServer` explicitly plumbs `config.DenyList` into `httpConfig.DenyList` [5](#0-4) , while `NewEVMWebSocketServer`'s `wsConfig` construction has no equivalent line copying `config.DenyList` [6](#0-5) .

Both servers register largely the same API surface (`eth_*`, `debug_*`, `txpool_*`, etc.) — including `debug` on HTTP and the equivalent debug/trace-capable namespaces exposed under WS in the same process — so any method an operator lists in `deny_list` to "fail fast" (the config comment: *"Deny list defines list of methods that EVM RPC should fail fast"*) is still fully reachable over the WebSocket listener.

This exactly mirrors the CVE-2019-3806 bug class: a security/admission policy (Lua hooks / here, `deny_list`) is applied on one transport (TCP-with-certain-settings / here, HTTP) but silently skipped on another reachable transport (UDP / here, WebSocket) for the same logical service, letting a remote client bypass the intended policy simply by choosing the other transport.

### Title
EVM RPC `deny_list` method-gating is enforced only on the HTTP JSON-RPC listener, not on WebSocket - (File: evmrpc/rpcstack.go)

### Summary
The `[evm].deny_list` config is meant to block specific, presumably expensive or unsafe, JSON-RPC methods ("fail fast") on the node's public EVM RPC surface. It is registered on the HTTP `rpc.Server` in `EnableRPC` but is never registered — and cannot be, since `WsConfig` has no `DenyList` field — on the WebSocket `rpc.Server` in `EnableWS`.

### Finding Description
`HTTPServer.EnableRPC` builds an `rpc.Server`, registers the configured APIs, then loops over `config.DenyList` calling `srv.RegisterDenyList(method)` [7](#0-6) . `HTTPServer.EnableWS` builds a separate `rpc.Server` for the WS listener, registers the same set of APIs, but has no deny-list step whatsoever [8](#0-7) . This is consistent with the type definitions: `HTTPConfig.DenyList` exists but `WsConfig` carries only `Origins`, `Modules`, and the shared `RPCEndpointConfig` [9](#0-8) . At the call-site level, `NewEVMHTTPServer` forwards `config.DenyList` into `httpConfig.DenyList` [5](#0-4) , but `NewEVMWebSocketServer`'s `wsConfig` initialization never references `config.DenyList` [10](#0-9) , so the value from `app.toml` is simply dropped on the WS path. Both listeners register overlapping namespaces (`eth`, `debug`-equivalent behavior, `net`, etc.), so any method an operator denies on one transport remains fully callable on the other.

### Impact Explanation
An operator who sets `deny_list = ["debug_traceBlockByNumber", ...]` (the example given in the node's own documentation) to protect a public-facing default-configuration RPC node from expensive/abuse-prone calls gets no protection at all if the WebSocket endpoint (enabled by default, `ws_enabled = true`, port 8546) is reachable. A public-RPC client can simply reissue the denied call over WS and reach the same handler that HTTP was configured to block, defeating the operator's DoS mitigation and potentially crashing or stalling a default-configuration RPC node via the very method the deny list was meant to stop.

### Likelihood Explanation
High: WS is enabled by default alongside HTTP, and no additional privilege or special network position is needed — any client capable of reaching the RPC ports can simply choose the WebSocket protocol instead of HTTP to route around the deny list. No other conditions or races are required.

### Recommendation
Add a `DenyList []string` field to `WsConfig`, populate it from `config.DenyList` in `NewEVMWebSocketServer` (mirroring `NewEVMHTTPServer`), and call `srv.RegisterDenyList(method)` for each entry inside `EnableWS`, so the same deny list is enforced consistently across both transports.

### Proof of Concept
1. Configure a node with `deny_list = ["debug_traceBlockByNumber"]` and default `ws_enabled = true`.
2. Send `{"jsonrpc":"2.0","id":1,"method":"debug_traceBlockByNumber","params":[...]}` over HTTP :8545 → rejected per `TestHttpDenyList`'s pattern (`-32601 ... does not exist/is not available`) [11](#0-10) .
3. Send the identical request over WebSocket :8546 → the method is registered normally (same `debug` API is wired into WS APIs the same way HTTP is) and executes successfully, because `EnableWS` never calls `RegisterDenyList` [8](#0-7) , demonstrating the bypass.

### Citations

**File:** evmrpc/rpcstack.go (L42-64)
```go
// HTTPConfig is the JSON-RPC/HTTP configuration.
type HTTPConfig struct {
	Modules            []string
	CorsAllowedOrigins []string
	Vhosts             []string
	DenyList           []string
	// SeiLegacyAllowlist is BuildSeiLegacyEnabledSet(app.toml enabled_legacy_sei_apis); nil skips the HTTP gate
	// for gated sei_* methods.
	SeiLegacyAllowlist map[string]struct{}
	prefix             string // path prefix on which to mount http handler
	RPCEndpointConfig
	// rateLimitGate applies per-IP JSON-RPC rate limiting when non-nil.
	rateLimitGate *ratelimiter.Gate
}

// WsConfig is the JSON-RPC/Websocket configuration
type WsConfig struct {
	Origins            []string
	Modules            []string
	prefix             string // path prefix on which to mount ws handler
	wsAdmissionTimeout time.Duration
	RPCEndpointConfig
}
```

**File:** evmrpc/rpcstack.go (L325-351)
```go
// EnableRPC turns on JSON-RPC over HTTP on the server.
func (h *HTTPServer) EnableRPC(apis []rpc.API, config HTTPConfig) error {
	h.mu.Lock()
	defer h.mu.Unlock()

	if h.rpcAllowed() {
		return fmt.Errorf("JSON-RPC over HTTP is already enabled")
	}

	// Create RPC server and handler.
	srv := rpc.NewServer()
	srv.SetBatchLimits(config.batchItemLimit, config.batchResponseSizeLimit)
	if config.maxRequestBodyBytes > 0 {
		bodyLimit := config.maxRequestBodyBytes
		if bodyLimit > math.MaxInt {
			bodyLimit = math.MaxInt
		}
		srv.SetHTTPBodyLimit(int(bodyLimit))
	}
	logger.Info("Registering apis for evm rpc")
	if err := RegisterApis(apis, config.Modules, srv); err != nil {
		return err
	}
	logger.Info("Registering deny list for evm rpc", "deny-list", config.DenyList)
	for _, method := range config.DenyList {
		srv.RegisterDenyList(method)
	}
```

**File:** evmrpc/rpcstack.go (L390-422)
```go
// EnableWS turns on JSON-RPC over WebSocket on the server.
func (h *HTTPServer) EnableWS(apis []rpc.API, config WsConfig) error {
	h.mu.Lock()
	defer h.mu.Unlock()

	if h.wsAllowed() {
		return fmt.Errorf("JSON-RPC over WebSocket is already enabled")
	}
	// Create RPC server and handler.
	srv := rpc.NewServer()
	srv.SetBatchLimits(config.batchItemLimit, config.batchResponseSizeLimit)
	readLimit := effectiveMaxRequestBodyBytes(config.readLimit)
	srv.SetReadLimits(readLimit)
	// maxConcurrentRequestBytes is passed through raw; rpc.Server.recomputeWSConcurrentBudget
	// raises it to readLimit when smaller, matching
	// newRequestSizeLimiter's max(budget, maxBody) rule on the HTTP protocol.
	srv.SetWSConcurrentRequestBytes(config.maxConcurrentRequestBytes)
	srv.SetWSAdmissionTimeout(config.wsAdmissionTimeout)
	srv.SetWSAdmissionEventHook(func(reason string) {
		// Hook carries no request context, and the fork's own connCtx is context.Background() too.
		recordWSAdmissionRejected(context.Background(), reason)
	})
	logger.Info("Registering apis for evm websocket")
	if err := RegisterApis(apis, config.Modules, srv); err != nil {
		return err
	}
	h.WsConfig = config
	h.wsHandler.Store(&rpcHandler{
		Handler: NewWSHandlerStack(srv.WebsocketHandler(config.Origins), config.JwtSecret),
		server:  srv,
	})
	return nil
}
```

**File:** evmrpc/server.go (L210-215)
```go
	httpConfig := HTTPConfig{
		CorsAllowedOrigins: strings.Split(config.CORSOrigins, ","),
		Vhosts:             []string{"*"},
		DenyList:           config.DenyList,
		SeiLegacyAllowlist: seiLegacyAllowlist,
	}
```

**File:** evmrpc/server.go (L344-352)
```go
	wsConfig := WsConfig{Origins: strings.Split(config.WSOrigins, ",")}
	wsConfig.readLimit = config.MaxRequestBodyBytes
	wsConfig.maxConcurrentRequestBytes = config.MaxConcurrentRequestBytes
	wsConfig.wsAdmissionTimeout = config.WSAdmissionTimeout
	wsConfig.batchItemLimit = config.BatchRequestLimit
	wsConfig.batchResponseSizeLimit = config.BatchResponseMaxSize
	if err := httpServer.EnableWS(apis, wsConfig); err != nil {
		return nil, err
	}
```

**File:** evmrpc/rpcstack_test.go (L654-677)
```go
func TestHttpDenyList(t *testing.T) {
	const (
		expectRes = `{"jsonrpc":"2.0","id":null,"error":{"code":-32601,"message":"the method test_sleep does not exist/is not available"}}`
	)
	// Set-up server
	timeouts := rpc.DefaultHTTPTimeouts
	timeouts.WriteTimeout = time.Second
	srv := createAndStartServer(t, &evmrpc.HTTPConfig{
		DenyList: []string{"test_sleep"},
		Modules:  []string{"test"}}, false, &evmrpc.WsConfig{}, &timeouts)
	url := fmt.Sprintf("http://%v", srv.ListenAddr())
	// Send normal request
	t.Run("message", func(t *testing.T) {
		resp := rpcRequest(t, url, "test_sleep")
		defer resp.Body.Close()
		body, err := io.ReadAll(resp.Body)
		if err != nil {
			t.Fatal(err)
		}
		if string(body) != expectRes {
			t.Errorf("wrong response. have %s, want %s", string(body), expectRes)
		}
	})
}
```
