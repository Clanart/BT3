## Title
Unbounded numeric-string parsing in legacy JSON precompile `extractAsUint256` enables CPU-exhaustion DoS via `debug_traceCall`/`debug_traceBlock*` on public EVM RPC nodes - (File: `precompiles/json/legacy/v562/json.go`, `precompiles/json/legacy/v605/json.go`, `precompiles/json/legacy/v606/json.go`, `precompiles/json/legacy/v614/json.go`, `precompiles/json/legacy/v630/json.go`, `precompiles/json/legacy/v640/json.go`)

### Summary
Several historical versions of the `json` Cosmos precompile's `ExtractAsUint256` method convert an attacker-controlled JSON string value into a `*big.Int` via `new(big.Int).SetString(strValue, 10)` with **no upper bound on the length of `strValue`**, mirroring the exact root cause of CVE-2010-4535 (unbounded numeric-string parsing causing resource exhaustion). The current/latest precompile code already fixes this by rejecting strings longer than 100 characters, but the unfixed legacy implementations remain shipped and selectable, and are reachable by any public JSON-RPC client through the `debug_traceCall`, `debug_traceBlockByNumber`, and `debug_traceBlockByHash` endpoints, which explicitly re-select the precompile version that was active at the historical block height being traced.

### Finding Description
`ExtractAsUint256` in the legacy precompile packages performs: [1](#0-0) 

There is no `len(strValue) > 100` guard before calling `SetString`, unlike the patched implementation: [2](#0-1) 

The same missing check exists in `precompiles/json/legacy/v562/json.go`, `v605/json.go`, `v606/json.go`, `v614/json.go`, and `v630/json.go` (all identical `ExtractAsUint256` bodies), while `v5.5.2`, `v5.5.5`, `v6.5`, `v6.6` and the current head add the 100-byte bound.

These versioned implementations are wired up in `precompiles/json/setup.go`: [3](#0-2) 

Precompile selection is height-dependent only when the context is in tracing mode. `CustomPrecompiles` returns the pinned "latest" set for ordinary block execution, but for tracing it looks up the version that was active at the traced height via `GetCustomPrecompilesVersions`: [4](#0-3) 

The public `debug_traceCall`/`debug_traceBlockByNumber`/`debug_traceBlockByHash` handlers explicitly enable this tracing context (`WithIsTracing(true)`) and resolve to a caller-specified historical block height/hash before dispatch: [5](#0-4) [6](#0-5) 

A `maxBlockLookback` guard exists, but it is a node-configurable window (and can be disabled with `-1`, as exercised in tests) rather than a hard restriction to only the fixed post-patch code path: [7](#0-6) 

The project's own test explicitly demonstrates that tracing a call/tx against a historical height dispatches to the JSON precompile version active at that height (i.e., an unfixed legacy version if the height predates the length-check upgrade): [8](#0-7) 

An attacker can call `debug_traceCall` with `blockNrOrHash` pointing at a block height corresponding to one of the unpatched precompile versions (`v5.6.2`, `v6.0.5`, `v6.0.6`, `v6.1.4`, `v6.3.0`, `v6.4.0`) and craft a JSON-RPC `Input` that calls the JSON precompile's `extractAsUint256` method with a JSON document containing an extremely long numeric string for the target key. This drives `new(big.Int).SetString(strValue, 10)` on an attacker-chosen, effectively unbounded-length decimal string — a decimal-string-to-bigint conversion that is quadratic in string length — consuming substantial CPU per RPC call, exactly analogous to the base36-timestamp DoS in the Django advisory.

### Impact Explanation
Because `debug_traceCall`/`debug_traceBlockByNumber`/`debug_traceBlockByHash` are simulation-style, off-chain RPC calls (not committed transactions), an attacker does not need to pay the precompile's per-byte gas cost as an on-chain fee — they only need a node willing to service the trace request. Repeated calls with megabyte-scale numeric strings can consume significant CPU on the RPC node servicing debug/trace requests, degrading or crashing the RPC service for legitimate users (crash of default-configuration RPC nodes / DoS), which matches the impact bar for this analog.

### Likelihood Explanation
Exploitability requires: (1) a public node exposing the `debug` namespace with trace endpoints, (2) `maxBlockLookback` configuration permitting historical/older heights to be traced, and (3) selecting a height that maps to one of the unpatched precompile versions (`v5.6.2` through `v6.4.0`). Chains that have progressed past `v6.5`/current head still retain historical block ranges corresponding to the vulnerable versions, and tracing arbitrary historical calls/blocks is a documented, tested feature (`evmrpc/tests/tracers_test.go`), making this reachable by any unprivileged RPC client without needing validator or node-operator privileges.

### Recommendation
Backport the `len(strValue) > 100` (or similar bound) check present in `precompiles/json/json.go`'s `ExtractAsUint256` into all legacy versions still reachable through historical tracing (`v562`, `v605`, `v606`, `v614`, `v630`, `v640`, and any other affected legacy package), so that tracing/replay of historical transactions cannot re-trigger unbounded `big.Int.SetString` parsing. Additionally, consider capping the maximum size of `TransactionArgs.Input`/call data accepted by `debug_traceCall` independent of precompile-specific gas accounting, since simulated trace calls do not incur the same economic cost as committed transactions.

### Proof of Concept
1. Identify (or run) a sei-chain node whose chain history includes a block height range in which the JSON precompile version was one of the unpatched ones (e.g. `v6.0.5`/`v6.0.6`), and whose RPC config exposes `debug_traceCall` with a permissive `maxBlockLookback`.
2. Construct a JSON-RPC `debug_traceCall` request:
   - `to`: the JSON precompile address `0x0000000000000000000000000000000000001003`
   - `blockNrOrHash`: a historical block number known to fall in the unpatched-version range
   - `data`: ABI-encoded call to `extractAsUint256(bytes,string)` where the `bytes` argument is a JSON object like `{"k":"999...9"}` with a multi-megabyte digit string for `k`, and the `string` argument is `"k"`.
3. Send this request repeatedly to the target node's RPC endpoint.
4. Observe elevated CPU usage / increased latency / RPC service degradation on the node processing the trace request, while a correspondingly-sized request against the current/head precompile (with the `len(strValue) > 100` check) is rejected immediately.

### Citations

**File:** precompiles/json/legacy/v640/json.go (L171-182)
```go
	// Assuming result is your byte slice
	// Convert byte slice to string and trim quotation marks
	strValue := strings.Trim(string(result), "\"")

	// Convert the string to big.Int
	value, success := new(big.Int).SetString(strValue, 10)
	if !success {
		return nil, fmt.Errorf("failed to convert %s to big.Int", strValue)
	}

	return value, nil
}
```

**File:** precompiles/json/json.go (L186-199)
```go
	// Convert byte slice to string and trim quotation marks
	strValue := strings.Trim(string(result), "\"")

	if len(strValue) > 100 {
		return nil, fmt.Errorf("value string too long: got %d, max 100", len(strValue))
	}

	// Convert the string to big.Int
	value, success := new(big.Int).SetString(strValue, 10)
	if !success {
		return nil, fmt.Errorf("failed to convert %s to big.Int", strValue)
	}

	return value, nil
```

**File:** precompiles/json/setup.go (L23-40)
```go
func GetVersioned(latestUpgrade string, keepers utils.Keepers) utils.VersionedPrecompiles {
	return utils.VersionedPrecompiles{
		latestUpgrade: check(NewPrecompile(keepers)),
		"v5.5.2":      check(jsonv552.NewPrecompile(keepers)),
		"v5.5.5":      check(jsonv555.NewPrecompile(keepers)),
		"v5.6.2":      check(jsonv562.NewPrecompile(keepers)),
		"v6.0.3":      check(jsonv603.NewPrecompile(keepers)),
		"v6.0.5":      check(jsonv605.NewPrecompile(keepers)),
		"v6.0.6":      check(jsonv606.NewPrecompile(keepers)),
		"v6.1.0":      check(jsonv610.NewPrecompile(keepers)),
		"v6.1.4":      check(jsonv614.NewPrecompile(keepers)),
		"v6.2.0":      check(jsonv620.NewPrecompile(keepers)),
		"v6.3.0":      check(jsonv630.NewPrecompile(keepers)),
		"v6.4.0":      check(jsonv640.NewPrecompile(keepers)),
		"v6.5":        check(jsonv65.NewPrecompile(keepers)),
		"v6.6":        check(jsonv66.NewPrecompile(keepers)),
	}
}
```

**File:** x/evm/keeper/keeper.go (L173-210)
```go
func (k *Keeper) CustomPrecompiles(ctx sdk.Context) map[common.Address]vm.PrecompiledContract {
	if !ctx.IsTracing() {
		return k.latestCustomPrecompiles
	}
	versions := k.GetCustomPrecompilesVersions(ctx)
	cp := make(map[common.Address]vm.PrecompiledContract, len(k.customPrecompiles))
	for addr, versioned := range k.customPrecompiles {
		cp[addr] = versioned[versions[addr]]
	}
	return cp
}

func (k *Keeper) GetCustomPrecompilesVersions(ctx sdk.Context) map[common.Address]string {
	height := ctx.BlockHeight()
	cp := make(map[common.Address]string, len(k.customPrecompiles))
	for addr, versioned := range k.customPrecompiles {
		mostRecentUpgradeHeight := int64(0)
		noForkHistory := true
		for upgrade := range versioned {
			upgradeHeight := k.upgradeKeeper.GetDoneHeight(ctx, upgrade)
			if upgradeHeight != 0 {
				noForkHistory = false
			}
			if height < upgradeHeight {
				// requested height hasn't seen this upgrade version yet.
				continue
			}
			if upgradeHeight > mostRecentUpgradeHeight {
				mostRecentUpgradeHeight = upgradeHeight
				cp[addr] = upgrade
			}
		}
		if noForkHistory {
			cp[addr] = k.latestUpgrade
		}
	}
	return cp
}
```

**File:** evmrpc/trace_profile.go (L143-149)
```go
func (api *DebugAPI) newProfileTracingBackend() *Backend {
	tracingBackend := *api.backend
	tracingBackend.ctxProvider = func(height int64) sdk.Context {
		return api.ctxProvider(height).WithIsTracing(true)
	}
	return &tracingBackend
}
```

**File:** evmrpc/tracers.go (L167-181)
```go
func (api *DebugAPI) guardHistoricalDebugTraceHeight(ctx context.Context, endpoint string, blockHeight int64) error {
	latest := api.ctxProvider(LatestCtxHeight).BlockHeight()
	if !isHistoricalDebugTraceBlock(blockHeight, latest, api.maxBlockLookback) {
		return nil
	}
	recordHistoricalDebugTraceAttempt(ctx, endpoint, string(api.connectionType))
	return fmt.Errorf("block number %d is beyond max lookback of %d", blockHeight, api.maxBlockLookback)
}

func isHistoricalDebugTraceBlock(blockHeight, latestHeight, maxBlockLookback int64) bool {
	if maxBlockLookback < 0 || blockHeight < 0 || latestHeight < blockHeight {
		return false
	}
	return blockHeight < latestHeight-maxBlockLookback
}
```

**File:** evmrpc/tracers.go (L559-589)
```go
func (api *DebugAPI) TraceCall(ctx context.Context, args export.TransactionArgs, blockNrOrHash rpc.BlockNumberOrHash, config *tracers.TraceCallConfig) (result interface{}, returnErr error) {
	startTime := time.Now()
	defer func() {
		recordMetricsWithError(ctx, "debug_traceCall", api.connectionType, startTime, returnErr, recover())
	}()

	if config == nil {
		config = &tracers.TraceCallConfig{}
	}
	if returnErr = api.validateTraceTracer(&config.TraceConfig); returnErr != nil {
		return nil, returnErr
	}

	ctx, done, err := api.prepareTraceContext(ctx)
	if err != nil {
		return nil, err
	}
	defer done()

	if returnErr = api.guardHistoricalDebugTraceByNumberOrHash(ctx, "debug_traceCall", blockNrOrHash); returnErr != nil {
		return nil, returnErr
	}

	if returnErr = validateStateOverrides(config.StateOverrides, api.backend.MaxStateOverrideAccounts(), api.backend.MaxStateOverrideSlots()); returnErr != nil {
		return nil, returnErr
	}
	api.clampDefaultStructLogLimit(&config.TraceConfig)
	result, returnErr = api.tracersAPI.TraceCall(ctx, args, blockNrOrHash, config)
	result, returnErr = resultUnlessExpired(ctx, result, returnErr)
	return
}
```

**File:** evmrpc/tests/tracers_test.go (L55-84)
```go
func TestTraceHistoricalPrecompiles(t *testing.T) {
	from := getAddrWithMnemonic(mnemonic1)
	txData := jsonExtractAsBytesFromArray(0).(*ethtypes.DynamicFeeTx)
	SetupTestServer(t, [][][]byte{{}, {}, {}}, mnemonicInitializer(mnemonic1), mockUpgrade("v5.5.2", 1), mockUpgrade(app.LatestUpgrade, 3)).Run(
		func(port int) {
			args := export.TransactionArgs{
				From:     &from,
				To:       txData.To,
				Gas:      (*hexutil.Uint64)(&txData.Gas),
				GasPrice: (*hexutil.Big)(txData.GasFeeCap),
				Nonce:    (*hexutil.Uint64)(&txData.Nonce),
				Input:    (*hexutil.Bytes)(&txData.Data),
				ChainID:  (*hexutil.Big)(txData.ChainID),
			}
			bz, err := json.Marshal(args)
			require.Nil(t, err)
			// error when traced on a block prior to v6.0.5
			res := sendRequestWithNamespace("debug", port, "traceCall", bz, "0x2", map[string]interface{}{
				"timeout": "60s", "tracer": "flatCallTracer",
			})
			errMsg := res["result"].([]interface{})[0].(map[string]interface{})["error"].(string)
			require.Contains(t, errMsg, "no method with id")
			// no error when traced on a block post v6.0.5
			res = sendRequestWithNamespace("debug", port, "traceCall", bz, "0x3", map[string]interface{}{
				"timeout": "60s", "tracer": "flatCallTracer",
			})
			resultMap := res["result"].([]interface{})[0].(map[string]interface{})
			require.NotContains(t, resultMap, "error")
		},
	)
```
