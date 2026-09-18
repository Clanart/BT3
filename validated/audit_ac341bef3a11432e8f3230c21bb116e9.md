## Analysis

The Keycloak CVE class is: **raw, attacker/request-controlled string values become Prometheus metric label values with no bound on cardinality**, so an unprivileged caller can create unbounded numbers of unique metric time-series and exhaust node memory. The sei-chain codebase has a direct structural analog in the wasm keeper's query metrics.

## Title
Unbounded Prometheus metric cardinality via attacker-controlled `contract_address` label in wasm query telemetry - (File: sei-wasmd/x/wasm/keeper/metrics.go)

## Summary
`recordContractQuerySmartInvocation` and `recordContractQuerySmartGasUsed` unconditionally attach the raw, caller-supplied `contractAddress` string as a Prometheus label on the legacy `armon/go-metrics` telemetry sink for every `SmartContractState`/query-smart call, regardless of whether that address corresponds to a real, existing contract. Any public caller of the wasm query surface can drive unbounded metric-series creation by querying many distinct (including non-existent) contract addresses.

## Finding Description
`recordContractQuerySmartInvocation(contractAddress string)` calls `telemetry.IncrCounterWithLabels` with `contract_address` set directly from the caller-supplied query parameter, and `recordContractQuerySmartGasUsed` does the same via `telemetry.SetGaugeWithLabels`: [1](#0-0) [2](#0-1) 

The code's own comment on the OTel path acknowledges the exact bug class: including `contract_address` as a label creates "one series per distinct contract address queried," and that this is unsafe on a sink with no series expiration — which is precisely why the author *removed* the label from the new OTel histogram: [3](#0-2) 

However, the legacy `telemetry.IncrCounterWithLabels`/`SetGaugeWithLabels` calls immediately below still carry the unbounded `contract_address` label into the `armon/go-metrics` fanout sink, which is enabled by `telemetry.Enabled` and additionally exported to Prometheus once `PrometheusRetentionTime > 0`: [4](#0-3) 

Because `contractAddress` is taken from the query request itself (the `SmartContractState`/`RawContractState` query path in `sei-wasmd/x/wasm/keeper/keeper.go`, which the grep results confirm calls these `record*` functions), a caller does not need to reference a real, deployed contract — any syntactically valid bech32 address string reaches these label-emitting functions. This is the same root cause as the Keycloak advisory: user-supplied, effectively unbounded strings (there, error text containing a client ID; here, a contract address) become Prometheus label values with no allow-list or hashing/truncation, so an attacker can mint arbitrarily many unique label combinations.

## Impact Explanation
An attacker can repeatedly issue `wasm` smart/raw contract queries (a public, gasless gRPC/REST query, not even requiring a submitted transaction) against a large number of distinct, potentially non-existent contract addresses. Each call creates a new label combination in the legacy metrics sink's internal maps and, when `prometheus-retention-time` is configured (the sink retains series until expiration), in the exported Prometheus vector as well. Sustained high-rate querying with unique addresses can grow memory unboundedly until the process (any node exposing this query endpoint with telemetry enabled, including validators, since this label is emitted from the core keeper, not just an RPC-only component) is exhausted and crashes — satisfying the "crash of default-configuration RPC nodes" impact bar.

## Likelihood Explanation
The query path is public, unauthenticated, and gasless (it is a keeper-level query function, not a transaction), so the cost to the attacker of generating large volumes of distinct `contract_address` label values is minimal — essentially bandwidth to send many query RPCs. The severity is mitigated by two factors that reduce confidence to Medium: (1) the affected counters/gauges only take effect once telemetry is enabled and, for the Prometheus-exported path, only once `prometheus-retention-time` is set to a positive value — the exact default across all deployment configs was not fully verified in this pass; (2) the legacy `armon/go-metrics/prometheus` sink does have an `Expiration` sweep (`PrometheusOpts.Expiration` in `sei-cosmos/telemetry/metrics.go`), which caps how long stale series survive, unlike the unbounded-forever case cited in the OTel comment — this limits, but does not eliminate, the achievable peak cardinality during a sustained attack burst.

## Recommendation
Remove the `contract_address` label from the legacy `telemetry.IncrCounterWithLabels`/`SetGaugeWithLabels` calls in `recordContractQuerySmartInvocation` and `recordContractQuerySmartGasUsed`, exactly as was already done for the newer OTel histogram, or replace the raw address with a bounded/aggregated dimension (e.g., no label, or a coarse "known vs. unknown contract" boolean) so the metric cardinality cannot scale with attacker-chosen input.

## Proof of Concept
1. Enable telemetry with `prometheus-retention-time > 0` on a node exposing the wasm query gRPC/REST surface.
2. Repeatedly call `SmartContractState`/`RawContractState` (or the CLI/REST equivalent) with a large number of distinct, syntactically valid bech32 contract addresses (they need not correspond to deployed contracts, since the label is attached in `recordContractQuerySmartInvocation`/`recordContractQuerySmartGasUsed` regardless of query success).
3. Each call adds a new label set to `sei-wasmd/x/wasm/keeper/metrics.go:125-147`'s underlying `armon/go-metrics` counter/gauge; sustained high-rate distinct-address queries grow the sink's internal series map faster than the configured `Expiration` sweep can reclaim it, driving memory usage up until the process is exhausted.

### Citations

**File:** sei-wasmd/x/wasm/keeper/metrics.go (L125-134)
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
```

**File:** sei-wasmd/x/wasm/keeper/metrics.go (L136-147)
```go
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

**File:** sei-cosmos/telemetry/metrics.go (L74-140)
```go
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
}
```
