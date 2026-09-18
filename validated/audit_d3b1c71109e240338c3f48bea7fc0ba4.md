Found it: `Vhosts: []string{"*"}` is hardcoded at [1](#0-0)  for the EVM JSON-RPC HTTP server, and this feeds into `virtualHostHandler` in `NewHTTPHandlerStack` [2](#0-1) , whose `ServeHTTP` explicitly bypasses Host-header validation whenever `"*"` is in the vhost set: `if _, exist := h.vhosts["*"]; exist { h.next.ServeHTTP(w, r); return }` [3](#0-2) . This is exactly the go-ethereum DNS-rebinding mitigation this bug class targets (comment at [4](#0-3)  confirms the vhost check exists specifically to "help prevent DNS rebinding attacks"), but sei-chain's default wiring disables it unconditionally by always passing `"*"`. There is no config knob exposed (`evmrpc/config/config.go` has `CORSOrigins`/`WSOrigins` but no `Vhosts`/allowed-hosts field), so operators cannot turn this protection on for the HTTP JSON-RPC listener even if they wanted to — it's baked into `NewEVMHTTPServer`.

CORS is a separate, unrelated control (it protects browser-originated cross-origin reads of the response, not raw HTTP POSTs from malicious pages via DNS rebinding, since DNS rebinding requests are simple `Content-Type: application/json` POSTs that don't trigger CORS preflight and don't need to read the CORS header to succeed against a JSON-RPC POST endpoint). The listener binds on `0.0.0.0` by default (`LocalAddress = "0.0.0.0"` at [5](#0-4) , used for both HTTP and WS at [6](#0-5) ), so the port is reachable on the node's real network interface, not just loopback — a prerequisite for DNS rebinding to be meaningful (rebinding to `127.0.0.1` specifically would only be interesting if the service also listened on loopback only, but many real-world rebinding targets bind on all interfaces and rely on Host-header validation as the actual control, which is what go-ethereum's fix added and what sei-chain disables here).

### Title
EVM JSON-RPC HTTP server disables Host-header (vhost) validation, exposing it to DNS-rebinding-style access - ([File: evmrpc/server.go])

### Summary
`NewEVMHTTPServer` hardcodes `Vhosts: []string{"*"}` when building the `HTTPConfig` for the EVM JSON-RPC HTTP listener, which unconditionally disables the `virtualHostHandler` Host-header check that go-ethereum (and this codebase's own `rpcstack.go`, copied from go-ethereum) implements specifically to defend against DNS rebinding attacks.

### Finding Description
`evmrpc/rpcstack.go` ports go-ethereum's HTTP handler stack, including `virtualHostHandler`, whose doc comment states it exists to "help prevent DNS rebinding attacks, where a 'random' domain name points to the service ip address" [7](#0-6) . The handler only serves requests whose `Host` header is an IP address, or a hostname present in the configured `vhosts` allow-list, or when `"*"` is present in the allow-list [8](#0-7) .

`NewEVMHTTPServer` (the production entry point that stands up the EVM JSON-RPC HTTP server on port 8545) builds its `HTTPConfig` with `Vhosts: []string{"*"}` unconditionally [1](#0-0) . That value is passed straight into `EnableRPC` → `NewHTTPHandlerStack` → `newVHostHandler`, which converts `"*"` into the `vhosts["*"]` bypass branch, meaning every request with any Host header (or none) reaches the RPC handler. There is no config field in `evmrpc/config/config.go`'s `Config` struct (checked `CORSOrigins`, `WSOrigins`, and the rest of the field list at [9](#0-8) ) to override this — an operator cannot restrict vhosts without a code change.

The listener also binds `0.0.0.0` by default via `LocalAddress` [5](#0-4)  and `SetListenAddr(LocalAddress, config.HTTPPort)` [10](#0-9) , so it is reachable beyond loopback, which is the network condition DNS rebinding is meant to exploit.

### Impact Explanation
An attacker who lures a victim (who has network access to a Sei full node's EVM JSON-RPC port — e.g., same LAN, cloud VPC, or a rebinding chain that still resolves into the node's reachable network) into visiting a malicious webpage can use DNS rebinding to make the victim's browser send authenticated-looking JSON-RPC POST requests to the node's `:8545` endpoint that are accepted regardless of Host header. Depending on which accounts/keys are unlocked or reachable via that node's RPC (e.g., `eth_sendTransaction`-style flows, admin/debug APIs, or account-management surfaces if enabled), this can enable unauthorized transaction submission or state manipulation through the RPC surface — a public-RPC-client-reachable vector explicitly in scope. The severity is bounded by what the specific RPC surface exposes without a signed transaction, but the missing defense-in-depth control is a direct, unconditional regression of an intentionally-ported security mitigation.

### Likelihood Explanation
Likelihood is dependent on network topology (the port must be reachable from the attacker-controlled DNS-rebinding target), but the vulnerable code path requires zero attacker privilege on-chain and no configuration mistake by the operator — the bypass is hardcoded in `NewEVMHTTPServer`, so every node running the standard code is affected by default with no way to opt out.

### Recommendation
Make `Vhosts` configurable via `evmrpc/config/config.go` (e.g., an `HTTPVirtualHosts` field defaulting to `localhost` or the node's own configured RPC hostname rather than `"*"`), and stop hardcoding `[]string{"*"}` in `NewEVMHTTPServer`. At minimum, default to a safer allow-list (loopback/localhost) and require explicit opt-in to `"*"` for operators who intentionally want to disable the Host-header check.

### Proof of Concept
1. Start a Sei node with EVM RPC enabled on the default port 8545, reachable from an attacker-influenced network path.
2. Attacker registers a DNS name whose TTL they control and initially resolves to their own server, then rebinds to the node's IP.
3. Victim (with a browser that can reach the node's IP) visits attacker's page, which issues `fetch()` POST requests with `Content-Type: application/json` to `http://<rebound-domain>:8545` containing a JSON-RPC payload (e.g., `eth_sendTransaction` or an unlock/account-management call if such method is registered and unauthenticated on this listener).
4. Because `Vhosts` is `["*"]`, `virtualHostHandler` at [3](#0-2)  lets the request through regardless of the rebound Host header; existing tests confirm the mechanism works as designed when `Vhosts` is restricted (`TestVhosts` at [11](#0-10) ) — the production code simply never applies that restriction.

### Citations

**File:** evmrpc/server.go (L28-28)
```go
const LocalAddress = "0.0.0.0"
```

**File:** evmrpc/server.go (L90-94)
```go
	methodTimeout := tmutils.Some(httpServer.timeouts.WriteTimeout)
	httpServer.SetMaxOpenConns(config.MaxOpenConnections)
	if err := httpServer.SetListenAddr(LocalAddress, config.HTTPPort); err != nil {
		return nil, err
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

**File:** evmrpc/rpcstack.go (L444-453)
```go
// NewHTTPHandlerStack returns wrapped http-related handlers.
func NewHTTPHandlerStack(srv http.Handler, cors []string, vhosts []string, JwtSecret []byte) http.Handler {
	// Wrap the CORS-handler within a host-handler
	handler := newCorsHandler(srv, cors)
	handler = newVHostHandler(vhosts, handler)
	if len(JwtSecret) != 0 {
		handler = newJWTHandler(JwtSecret, handler)
	}
	return NewGzipHandler(handler)
}
```

**File:** evmrpc/rpcstack.go (L478-485)
```go
// virtualHostHandler is a handler which validates the Host-header of incoming requests.
// Using virtual hosts can help prevent DNS rebinding attacks, where a 'random' domain name points to
// the service ip address (but without CORS headers). By verifying the targeted virtual host, we can
// ensure that it's a destination that the node operator has defined.
type virtualHostHandler struct {
	vhosts map[string]struct{}
	next   http.Handler
}
```

**File:** evmrpc/rpcstack.go (L496-522)
```go
func (h *virtualHostHandler) ServeHTTP(w http.ResponseWriter, r *http.Request) {
	// if r.Host is not set, we can continue serving since a browser would set the Host header
	if r.Host == "" {
		h.next.ServeHTTP(w, r)
		return
	}
	host, _, err := net.SplitHostPort(r.Host)
	if err != nil {
		// Either invalid (too many colons) or no port specified
		host = r.Host
	}
	if ipAddr := net.ParseIP(host); ipAddr != nil {
		// It's an IP address, we can serve that
		h.next.ServeHTTP(w, r)
		return
	}
	// Not an IP address, but a hostname. Need to validate
	if _, exist := h.vhosts["*"]; exist {
		h.next.ServeHTTP(w, r)
		return
	}
	if _, exist := h.vhosts[host]; exist {
		h.next.ServeHTTP(w, r)
		return
	}
	http.Error(w, "invalid host specified", http.StatusForbidden)
}
```

**File:** evmrpc/config/config.go (L72-161)
```go
}

// EVMRPC Config defines configurations for EVM RPC server on this node
type Config struct {
	// controls whether an HTTP EVM server is enabled
	HTTPEnabled bool `mapstructure:"http_enabled"`
	HTTPPort    int  `mapstructure:"http_port"`

	// controls whether a websocket server is enabled
	WSEnabled bool `mapstructure:"ws_enabled"`
	WSPort    int  `mapstructure:"ws_port"`

	// ReadTimeout is the maximum duration for reading the entire
	// request, including the body.
	//
	// Because ReadTimeout does not let Handlers make per-request
	// decisions on each request body's acceptable deadline or
	// upload rate, most users will prefer to use
	// ReadHeaderTimeout. It is valid to use them both.
	ReadTimeout time.Duration `mapstructure:"read_timeout"`

	// ReadHeaderTimeout is the amount of time allowed to read
	// request headers. The connection's read deadline is reset
	// after reading the headers and the Handler can decide what
	// is considered too slow for the body. If ReadHeaderTimeout
	// is zero, the value of ReadTimeout is used. If both are
	// zero, there is no timeout.
	ReadHeaderTimeout time.Duration `mapstructure:"read_header_timeout"`

	// WriteTimeout is the maximum duration before timing out
	// writes of the response. It is reset whenever a new
	// request's header is read. Like ReadTimeout, it does not
	// let Handlers make decisions on a per-request basis.
	WriteTimeout time.Duration `mapstructure:"write_timeout"`

	// IdleTimeout is the maximum amount of time to wait for the
	// next request when keep-alives are enabled. If IdleTimeout
	// is zero, the value of ReadTimeout is used. If both are
	// zero, ReadHeaderTimeout is used.
	IdleTimeout time.Duration `mapstructure:"idle_timeout"`

	// Maximum gas limit for simulation
	SimulationGasLimit uint64 `mapstructure:"simulation_gas_limit"`

	// Timeout for EVM call in simulation
	SimulationEVMTimeout time.Duration `mapstructure:"simulation_evm_timeout"`

	// list of CORS allowed origins, separated by comma
	CORSOrigins string `mapstructure:"cors_origins"`

	// list of WS origins, separated by comma
	WSOrigins string `mapstructure:"ws_origins"`

	// timeout for filters
	FilterTimeout time.Duration `mapstructure:"filter_timeout"`

	// maximum number of active eth_newFilter and eth_newBlockFilter filters
	MaxFilters uint64 `mapstructure:"max_filters"`

	// maximum number of unpolled hashes retained by an eth_newBlockFilter filter
	MaxBlockFilterHashes uint64 `mapstructure:"max_block_filter_hashes"`

	// checkTx timeout for sig verify
	CheckTxTimeout time.Duration `mapstructure:"checktx_timeout"`

	// max number of txs to pull from mempool
	MaxTxPoolTxs uint64 `mapstructure:"max_tx_pool_txs"`

	// controls whether to have txns go through one by one
	Slow bool `mapstructure:"slow"`

	// Enable simulation before broadcasting EVM RPC sendRawTransaction.
	EnableSimulation bool `mapstructure:"enable_simulation"`

	// Deny list defines list of methods that EVM RPC should fail fast
	DenyList []string `mapstructure:"deny_list"`

	// max number of logs a single eth_getLogs query may match before it errors,
	// for both bounded and open-ended block ranges (a non-positive value falls
	// back to DefaultMaxLogLimit)
	MaxLogNoBlock int64 `mapstructure:"max_log_no_block"`

	// max estimated heap bytes of matched logs a single eth_getLogs query may
	// materialize before it errors (a non-positive value falls back to the
	// receipt store default)
	MaxLogBytes int64 `mapstructure:"max_log_bytes"`

	// max number of blocks to query logs for
	MaxBlocksForLog int64 `mapstructure:"max_blocks_for_log"`

```

**File:** evmrpc/rpcstack_test.go (L54-65)
```go
// TestVhosts makes sure vhosts are properly handled on the http server.
func TestVhosts(t *testing.T) {
	srv := createAndStartServer(t, &evmrpc.HTTPConfig{Vhosts: []string{"test"}}, false, &evmrpc.WsConfig{}, nil)
	defer srv.Stop()
	url := "http://" + srv.ListenAddr()

	resp := rpcRequest(t, url, testMethod, "host", "test")
	assert.Equal(t, resp.StatusCode, http.StatusOK)

	resp2 := rpcRequest(t, url, testMethod, "host", "bad")
	assert.Equal(t, resp2.StatusCode, http.StatusForbidden)
}
```
