### Title
Unbounded `contract_address` label cardinality in legacy telemetry sink from CosmWasm `QuerySmart`/gas-used metrics enables Prometheus sink memory exhaustion - (File: sei-wasmd/x/wasm/keeper/metrics.go)

### Summary
`sei-wasmd/x/wasm/keeper/metrics.go` emits legacy `armon/go-metrics` telemetry with an attacker-controlled `contract_address` label on every CosmWasm smart-query invocation, feeding an in-process Prometheus sink that has no series expiration protection for this label. Any user who can submit smart queries against arbitrary (including non-existent) contract addresses can force unbounded metric-series growth, mirroring the exact bug class in GHSA-gcj9-jj38-hwmc (unbounded per-request-attribute counters/timers draining the metrics backend).

### Finding Description
`recordContractQuerySmartInvocation` and `recordContractQuerySmartGasUsed` call `telemetry.IncrCounterWithLabels` / `telemetry.SetGaugeWithLabels` with a `contract_address` label built directly from the caller-supplied query target: [1](#0-0) 

These wrap into the shared `sei-cosmos/telemetry` package, which forwards to `github.com/armon/go-metrics`, and — when `telemetry.prometheus-retention-time > 0` — also to a `metricsprom.PrometheusSink`: [2](#0-1) 

The shipped node configs enable telemetry and set a positive `prometheus-retention-time` by default (e.g. `5`, `60`, `7200` seconds across different config templates), meaning the Prometheus sink path is live out of the box on many deployments: [3](#0-2) [4](#0-3) 

The code's own comment on `recordContractQuerySmartGasUsed` explicitly acknowledges that "unlike the legacy Prometheus sink, the OTel SDK has no series expiration, so a per-contract label here would retain one series per distinct contract address queried for the process lifetime" — and deliberately drops the label from the OTel histogram for that reason, while still emitting it unconditionally via the legacy `telemetry.SetGaugeWithLabels`/`IncrCounterWithLabels` calls: [5](#0-4) 

This is the same bug class as the reported advisory: an attacker-controlled string (there, the HTTP route; here, the wasm contract address) is used directly as a metrics label, creating a new time series per distinct value with no cap, eventually consuming unbounded memory in the metrics backend.

### Impact Explanation
An unprivileged CosmWasm caller can invoke `QuerySmart` (reachable via CLI/gRPC/REST/CosmWasm query routing and via the EVM `wasmd` precompile's static-call query path) against an unlimited number of distinct bech32 contract addresses — including addresses that do not correspond to any deployed contract, since address strings are cheap to generate and the query only needs to reach the metrics recording point. Each distinct address creates a new permanent Prometheus series (counter + gauge) held in process memory for the life of the process (bounded only by `PrometheusRetentionTime` for *scrape* exposure, but the underlying `armon/go-metrics` label-set/series bookkeeping and the exported cardinality itself grow unboundedly with sustained traffic). Sustained abuse can exhaust node memory, degrade or crash the metrics/API endpoint, and in the worst case affect availability of the RPC/API node — a resource-exhaustion DoS against public-facing nodes with telemetry-backed Prometheus enabled (the shipped default in several node templates).

### Likelihood Explanation
High: no privileges, gas payment, or fee accounting is required beyond an ordinary gRPC/REST/CLI smart query call; the label value is entirely attacker-chosen and unvalidated against an actual deployed contract before the metric is recorded. The only requirement is that the operator runs with `telemetry.enabled = true` and `prometheus-retention-time > 0`, which is the default in the repository's own `docker/rpcnode/config/app.toml` and migration-generated `app.toml` templates.

### Recommendation
Remove the `contract_address` label from the legacy `telemetry.IncrCounterWithLabels`/`SetGaugeWithLabels` calls in `recordContractQuerySmartInvocation` and `recordContractQuerySmartGasUsed` (mirroring the reasoning already applied to the OTel histogram), or replace the raw address with a bounded/aggregated dimension (e.g., no label, or a hashed/truncated bucket with a small fixed cardinality), consistent with the Vapor patch's approach of collapsing unbounded route/identifier labels into a single bounded fallback value.

### Proof of Concept
1. Run a node with default telemetry settings (`telemetry.enabled = true`, `telemetry.prometheus-retention-time > 0`, e.g. the shipped `docker/rpcnode/config/app.toml`).
2. From an unprivileged client, repeatedly call the wasm `QuerySmart` query (via gRPC/REST/CLI, or via the EVM wasmd precompile's static query path) against a large number of distinct, arbitrary bech32 "contract" addresses.
3. Each call reaches `recordContractQuerySmartInvocation`/`recordContractQuerySmartGasUsed` in [1](#0-0) , creating a new `contract_address`-labeled series in the process's `armon/go-metrics` Prometheus sink.
4. Observe unbounded growth of distinct metric series in `/metrics` output and increasing node memory usage over time, with no cap or expiration on the label set.

### Citations

**File:** sei-wasmd/x/wasm/keeper/metrics.go (L125-147)
```go
func recordContractQuerySmartInvocation(contractAddress string) {
	// No OTel counter here: it would be redundant with wasm_contract_query_smart_duration's
	// count, which is recorded unconditionally on every QuerySmart call just like this one.
	// TODO(PLT-910): remove once wasm_contract_query_smart_duration verified
	telemetry.IncrCounterWithLabels(
		[]string{"wasm", "contract", "query-smart", "invocation"},
		1,
		[]metrics.Label{telemetry.NewLabel("contract_address", contractAddress)},
	)
}

func recordContractQuerySmartGasUsed(ctx context.Context, contractAddress string, gasUsed uint64) {
	// contract_address omitted on the OTel histogram: unlike the legacy Prometheus sink, the
	// OTel SDK has no series expiration, so a per-contract label here would retain one series
	// per distinct contract address queried for the process lifetime.
	wasmKeeperMetrics.contractQuerySmartGasUsed.Record(ctx, int64(gasUsed)) //nolint:gosec
	// TODO(PLT-910): remove once wasm_contract_query_smart_gas_used verified
	telemetry.SetGaugeWithLabels(
		[]string{"wasm", "contract", "query-smart", "gas-used"},
		float32(gasUsed),
		[]metrics.Label{telemetry.NewLabel("contract_address", contractAddress)},
	)
}
```

**File:** sei-cosmos/telemetry/metrics.go (L73-139)
```go
// New creates a new instance of Metrics
func New(cfg Config) (*Metrics, error) {
	if !cfg.Enabled {
		return nil, nil
	}

	if numGlobalLables := len(cfg.GlobalLabels); numGlobalLables > 0 {
		parsedGlobalLabels := make([]metrics.Label, numGlobalLables)
		for i, gl := range cfg.GlobalLabels {
			parsedGlobalLabels[i] = NewLabel(gl[0], gl[1])
		}

		globalLabels = parsedGlobalLabels
	}

	metricsConf := metrics.DefaultConfig(cfg.ServiceName)
	metricsConf.EnableHostname = cfg.EnableHostname
	metricsConf.EnableHostnameLabel = cfg.EnableHostnameLabel

	memSink := metrics.NewInmemSink(10*time.Second, time.Minute)
	metrics.DefaultInmemSignal(memSink)

	m := &Metrics{memSink: memSink}
	fanout := metrics.FanoutSink{memSink}

	if cfg.PrometheusRetentionTime > 0 {
		promSink, err := m.setupPrometheus(cfg)
		if err != nil {
			return nil, err
		}
		fanout = append(fanout, promSink)
	}

	if _, err := metrics.NewGlobal(metricsConf, fanout); err != nil {
		return nil, err
	}

	return m, nil
}

func (m *Metrics) setupPrometheus(cfg Config) (*metricsprom.PrometheusSink, error) {
	m.prometheusEnabled = true
	prometheusOpts := metricsprom.PrometheusOpts{
		Expiration: time.Duration(cfg.PrometheusRetentionTime) * time.Second,
		// Default definitions, this allows Prometheus to persist metrics between scrapes instead
		// not reporting them if they are not updated. Please use this only if needed as it
		// will mean the metrics are stored in memory.
		GaugeDefinitions: []metricsprom.GaugeDefinition{
			{
				Name: []string{"cosmos", "upgrade", "plan", "height"},
				Help: "Next upgrade height",
			},
		},
		CounterDefinitions: []metricsprom.CounterDefinition{
			{
				Name: []string{"sei_cosmos_validator_slashed"},
				Help: "Total number of validator was slashed",
			},
		},
	}

	promSink, err := metricsprom.NewPrometheusSinkFrom(prometheusOpts)
	if err != nil {
		return nil, err
	}

	return promSink, nil
```

**File:** docker/rpcnode/config/app.toml (L73-87)
```text
# an in-memory sink is also enabled by default. Operators may also enabled
# other sinks such as Prometheus.
enabled = true

# Enable prefixing gauge values with hostname.
enable-hostname = false

# Enable adding hostname to labels.
enable-hostname-label = false

# Enable adding service to labels.
enable-service-label = false

# PrometheusRetentionTime, when positive, enables a Prometheus metrics sink.
prometheus-retention-time = 7200
```

**File:** docs/migration/seiv2_config_migration.md (L746-761)
```markdown
# Enabled enables the application telemetry functionality. When enabled,
# an in-memory sink is also enabled by default. Operators may also enabled
# other sinks such as Prometheus.
enabled = true

# Enable prefixing gauge values with hostname.
enable-hostname = false

# Enable adding hostname to labels.
enable-hostname-label = false

# Enable adding service to labels.
enable-service-label = false

# PrometheusRetentionTime, when positive, enables a Prometheus metrics sink.
prometheus-retention-time = 60
```
