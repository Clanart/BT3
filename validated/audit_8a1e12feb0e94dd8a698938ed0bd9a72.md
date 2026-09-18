### Title
Unbounded concurrent-connection memory amplification in the frozen RPC router (no admission control on aggregate buffered request bytes) - (File: cmd/frozen-rpc-router/router.go)

### Summary
`router.ServeHTTP` in `cmd/frozen-rpc-router/router.go` fully buffers every incoming JSON-RPC POST body into memory (`io.ReadAll(request.Body)`, bounded per-request only by `maxRequestBodySize`, default 5 MiB) before dialing/forwarding to the backend live or frozen node. [1](#0-0) 
Unlike the main EVM JSON-RPC server, which caps the *aggregate* in-flight request bytes across all connections via `MaxConcurrentRequestBytes` (an explicit admission-control budget), the frozen RPC router has no equivalent global memory budget. [2](#0-1) 
Each buffered body is held in memory for the full round-trip to the backend via `r.client.Do(...)`, using an `http.Client{}` with no `Timeout` set. [3](#0-2) [4](#0-3) 
This mirrors the CVE-2026-64773 bug class: a forwarding process that buffers per-client request data for the entire duration of a backend round-trip with no process-wide cap on how much can accumulate concurrently.

### Finding Description
`newRouter` is constructed with `client = &http.Client{}` when `nil` is passed (as done unconditionally in `main.go`), leaving no per-request `Timeout`. [5](#0-4) [6](#0-5) 
`http.Server` is configured only with `ReadHeaderTimeout` (headers only) and `WriteTimeout` (30s default), but no `ReadTimeout`, meaning body reads are not independently time-bounded and rely solely on the overall write-deadline accounting. [7](#0-6) 
Each accepted connection can buffer up to `maxRequestBodySize` (5 MiB by default, operator-configurable but with no upper bound enforced) fully in memory via `io.ReadAll`, then hold that buffer alive as `bytes.NewReader(body)` for the entire `r.client.Do()` call to the live/frozen upstream, including batch fan-out goroutines spawned per batch group with no concurrency cap. [8](#0-7) 
There is no mechanism anywhere in `router`, `config`, or `main` that limits the number of concurrent in-flight requests or the aggregate bytes buffered across all connections — the only per-request protection is the individual 5 MiB cap in `router.ServeHTTP`. [9](#0-8) 
By contrast, the primary EVM JSON-RPC surface explicitly closes this exact gap with `MaxConcurrentRequestBytes`, which bounds total HTTP/WS bytes admitted for concurrent processing process-wide. [2](#0-1) 
The frozen RPC router, added as a production-facing component for serving historical/frozen EVM RPC data, [10](#0-9) 
has no such control, so a client opening many concurrent large POST/batch requests can pin `N × up-to-5MiB` in router memory simultaneously, bounded only by OS connection limits, not by any router-level admission policy.

### Impact Explanation
An unprivileged public-RPC client reaching the frozen RPC router's listen address can open many concurrent connections, each submitting a near-maximum-size JSON-RPC body (single or batch), forcing the router to hold that data in memory for the full backend round-trip. With no global concurrent-byte budget (unlike the EVM RPC server's `MaxConcurrentRequestBytes`), memory usage scales linearly with the number of concurrent connections the attacker can sustain, which can exhaust process memory and crash the router — a "crash of default-configuration RPC nodes," which is an explicitly in-scope impact.

### Likelihood Explanation
The frozen RPC router listens on a public address (`--listen-address`, default `127.0.0.1:8545` but intended to front live traffic per its README/changelog description) and requires no authentication for any POST request. Triggering the condition requires only ordinary HTTP client capability to open many concurrent connections with near-max-size bodies — no privileged access, contract deployment, or special protocol knowledge is needed, making this straightforward for any external client with modest bandwidth/connection concurrency.

### Recommendation
Add a process-wide admission-control budget for aggregate buffered request bytes in `router.ServeHTTP` (mirroring `evmrpc`'s `MaxConcurrentRequestBytes` design), reject or defer new requests once the budget is exhausted, and configure `http.Server.ReadTimeout` and an explicit `http.Client.Timeout`/dial timeout on `r.client` so buffered request data cannot be held indefinitely while waiting on a backend response.

### Proof of Concept
1. Deploy `cmd/frozen-rpc-router` with default flags (`--max-request-body-bytes` default 5 MiB, no other concurrency limits).
2. From an attacking host, open a large number of concurrent TCP connections to the router's listen address and issue POST requests with valid JSON-RPC bodies sized close to 5 MiB each (e.g., large batch arrays under `--batch-request-limit`).
3. Each connection causes the router to fully buffer its body via `io.ReadAll` in `ServeHTTP` and hold it in memory through the entire `r.client.Do()` round-trip to the live/frozen upstream (no global memory cap enforced anywhere in `router.go`/`config.go`).
4. As concurrent connection count grows, resident memory grows unbounded (`N × ~5MiB`), leading to OOM and process crash on the router host once the operator's connection/file-descriptor limits allow sufficient concurrency — no equivalent of `evmrpc`'s `MaxConcurrentRequestBytes` protects this router.

### Citations

**File:** cmd/frozen-rpc-router/router.go (L101-118)
```go
func newRouter(liveAddress string, frozenConfigs []frozenNodeConfig, client *http.Client, maxRequestBodySize int64, maxBlockReferenceDepth, batchRequestLimit int) (*router, error) {
	liveURL, err := parseEndpoint(liveAddress)
	if err != nil {
		return nil, fmt.Errorf("invalid live node: %w", err)
	}
	if client == nil {
		client = &http.Client{}
	}
	if maxRequestBodySize <= 0 {
		return nil, errors.New("maximum request body size must be positive")
	}
	if maxBlockReferenceDepth <= 0 {
		return nil, errors.New("maximum block reference depth must be positive")
	}
	if batchRequestLimit <= 0 {
		return nil, errors.New("batch request limit must be positive")
	}

```

**File:** cmd/frozen-rpc-router/router.go (L174-198)
```go
func (r *router) ServeHTTP(w http.ResponseWriter, request *http.Request) {
	if request.Method != http.MethodPost {
		r.liveProxy.ServeHTTP(w, request)
		return
	}

	request.Body = http.MaxBytesReader(w, request.Body, r.maxRequestBodySize)
	body, err := io.ReadAll(request.Body)
	if err != nil {
		var maxBytesErr *http.MaxBytesError
		if errors.As(err, &maxBytesErr) {
			http.Error(w, "request body too large", http.StatusRequestEntityTooLarge)
			return
		}
		http.Error(w, "failed to read request body", http.StatusBadRequest)
		return
	}

	trimmed := bytes.TrimSpace(body)
	if len(trimmed) > 0 && trimmed[0] == '[' {
		r.serveBatch(w, request, body)
		return
	}
	r.serveSingle(w, request, body)
}
```

**File:** cmd/frozen-rpc-router/router.go (L369-393)
```go
func (r *router) fetchBatchGroups(request *http.Request, groups []*batchGroup) {
	var wg sync.WaitGroup
	for _, group := range groups {
		wg.Add(1)
		go func(group *batchGroup) {
			defer wg.Done()
			payload := make([]json.RawMessage, 0, len(group.calls))
			for _, call := range group.calls {
				payload = append(payload, call.raw)
			}
			body, err := json.Marshal(payload)
			if err != nil {
				group.err = err
				return
			}
			responseBody, err := r.callUpstream(request, group.upstream, body)
			if err != nil {
				group.err = err
				return
			}
			group.responses, group.err = decodeBatchResponses(responseBody)
		}(group)
	}
	wg.Wait()
}
```

**File:** cmd/frozen-rpc-router/router.go (L583-626)
```go
func (r *router) proxy(w http.ResponseWriter, request *http.Request, target *upstream, body []byte) error {
	upstreamRequest, err := r.newUpstreamRequest(request, target, body)
	if err != nil {
		return err
	}
	response, err := r.client.Do(upstreamRequest) //nolint:gosec // upstream URLs come from operator configuration; request data only supplies the proxied path and query.
	if err != nil {
		return err
	}

	copyResponseHeaders(w.Header(), response.Header)
	w.Header().Set(rpcRouteHeader, target.routeName())
	w.WriteHeader(response.StatusCode)
	_, _ = io.Copy(w, response.Body)
	_ = response.Body.Close()
	return nil
}

func (u *upstream) routeName() string {
	if u.freezeHeight == 0 {
		return "live"
	}
	return fmt.Sprintf("frozen:%d", u.freezeHeight)
}

func (r *router) callUpstream(request *http.Request, target *upstream, body []byte) ([]byte, error) {
	upstreamRequest, err := r.newUpstreamRequest(request, target, body)
	if err != nil {
		return nil, err
	}
	response, err := r.client.Do(upstreamRequest) //nolint:gosec // upstream URLs come from operator configuration; request data only supplies the proxied path and query.
	if err != nil {
		return nil, err
	}
	responseBody, err := io.ReadAll(response.Body)
	_ = response.Body.Close()
	if err != nil {
		return nil, err
	}
	if response.StatusCode < http.StatusOK || response.StatusCode >= http.StatusMultipleChoices {
		return nil, fmt.Errorf("upstream returned HTTP %d", response.StatusCode)
	}
	return responseBody, nil
}
```

**File:** evmrpc/config/config.go (L288-300)
```go
	// MaxConcurrentRequestBytes bounds the total size, in bytes, of HTTP and
	// WebSocket JSON-RPC request bodies admitted for processing concurrently.
	// HTTP (:8545) and WebSocket (:8546) each get an independent budget, so peak
	// in-flight request bytes process-wide can reach 2× this value (e.g. 256 MiB
	// when set to the 128 MiB default). HTTP charges the budget incrementally as
	// body bytes are read, bounding what a slow/stalled upload can pin to about
	// one read batch rather than the full declared Content-Length; requests that
	// would exceed the budget mid-read are rejected (HTTP 429). WebSocket blocks
	// until budget frees or WSAdmissionTimeout elapses; on timeout the peer
	// receives JSON-RPC error -32005 and the connection is closed (active
	// subscriptions are dropped with the connection). Set to 0 to disable the
	// limit on either protocol.
	MaxConcurrentRequestBytes int64 `mapstructure:"max_concurrent_request_bytes"`
```

**File:** cmd/frozen-rpc-router/main.go (L26-46)
```go
func run() error {
	cfg, err := parseConfig(os.Args[1:], os.Stderr)
	if err != nil {
		return err
	}
	frozenNodes, err := parseFrozenNodes(cfg.frozenNodes)
	if err != nil {
		return err
	}
	router, err := newRouter(cfg.liveNode, frozenNodes, nil, cfg.maxRequestBodySize, cfg.maxBlockReferenceDepth, cfg.batchRequestLimit)
	if err != nil {
		return err
	}

	server := &http.Server{
		Addr:              cfg.listenAddress,
		Handler:           router,
		ReadHeaderTimeout: 10 * time.Second,
		WriteTimeout:      cfg.writeTimeout,
		IdleTimeout:       2 * time.Minute,
	}
```

**File:** cmd/frozen-rpc-router/config.go (L14-21)
```go
const (
	defaultListenAddress          = "127.0.0.1:8545"
	defaultMaxRequestBodySize     = int64(5 << 20)
	defaultMaxBlockReferenceDepth = 16
	defaultBatchRequestLimit      = 1000
	defaultWriteTimeout           = 30 * time.Second
	defaultShutdownTimeout        = 10 * time.Second
)
```

**File:** CHANGELOG.md (L92-92)
```markdown
* [#3989](https://github.com/sei-protocol/sei-chain/pull/3989) Add frozen RPC router and Docker integration cluster
```
