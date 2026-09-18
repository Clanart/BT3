Interesting finding: in `precompiles/pointer/pointer.go` (`AddCW20`) and its many legacy versions, after `QuerySmartSafe` returns a CosmWasm contract's `token_info` JSON, the code does an unchecked type assertion:

```go
formattedRes := map[string]interface{}{}
if err := json.Unmarshal(res, &formattedRes); err != nil {
    return nil, 0, err
}
name := formattedRes["name"].(string)
symbol := formattedRes["symbol"].(string)
``` [1](#0-0) 

If the target CW20 contract's `token_info` query response has `name` or `symbol` fields that are missing, `null`, or non-string JSON values, `formattedRes["name"]` returns `nil` (interface), and `nil.(string)` type assertion panics ("interface conversion: interface {} is nil, not string"), not a graceful error. This is directly reachable by any EVM caller invoking the `pointer` precompile's `addCW20Native`-style methods (`AddCW20`/`AddCW721`) against an attacker-deployed or attacker-controlled CW20/CW721 contract address, exactly analogous to the CVE's crash-on-malformed-input pattern (untrusted structured input from an "uploaded" artifact triggering an unchecked-type/null dereference deep in a parsing path).

However, before finalizing this as the analog, I need to verify whether precompile execution paths wrap this panic with `recover()` (as seen consistently elsewhere in this codebase, e.g., `x/evm/ante`, `giga` executor, precompile `Run`/`Execute` wrappers) — if so, it downgrades from a node-crash to a mere reverted transaction, which the rules explicitly disqualify ("no-impact analogs").This confirms the vulnerability is real and not defended against. `RunAndCalculateGas` in `precompiles/common/precompiles.go` (and the versioned copies under `precompiles/common/legacy/*`) has a `defer` that only converts an *error* return (`err != nil`) into `vm.ErrExecutionReverted` — it does **not** call `recover()`. There is no panic-recovery wrapper anywhere in the chain from `RunAndCalculateGas` → `d.executor.Execute` → `PrecompileExecutor.AddCW20`/`AddCW721`. This is corroborated by the test `TestDynamicGasPrecompileRepanicsNonGas`, whose comment/assert explicitly documents that "a non-gas panic must propagate rather than be masked" — i.e., a genuine Go panic inside a dynamic-gas precompile executor is **not swallowed** and will propagate up through the EVM call stack.

That confirms: an unchecked type assertion panic (`formattedRes["name"].(string)` on a `nil` interface) inside `AddCW20`/`AddCW721` in the `pointer` precompile is not caught by the precompile framework and will propagate.

### Title
Unrecovered panic in `pointer` precompile's `AddCW20`/`AddCW721` via untrusted CW20/CW721 `token_info`/`contract_info` query response — ([File: precompiles/pointer/pointer.go])

### Summary
The `pointer` precompile's `AddCW20` and `AddCW721` executors (and all versioned copies under `precompiles/pointer/legacy/v555` … `v640`) query an attacker-supplied CosmWasm contract address for `token_info`/`contract_info`, unmarshal the response into a generic `map[string]interface{}`, and then perform unchecked Go type assertions on `name`/`symbol`. This is directly analogous to CVE-2021-28300 (GPAC `MergeTrack()` NULL pointer dereference from malformed/attacker-controlled input): both bugs blindly assume a structured, attacker-influenced payload conforms to an expected shape, and dereference/cast a value that can be absent or of the wrong type, crashing the process instead of returning a graceful error.

### Finding Description
`AddCW20` (and `AddCW721`) call:
```go
res, err := p.wasmdKeeper.QuerySmart(ctx, cwAddress, []byte("{\"token_info\":{}}"))
...
formattedRes := map[string]interface{}{}
if err := json.Unmarshal(res, &formattedRes); err != nil { return nil, 0, err }
name := formattedRes["name"].(string)
symbol := formattedRes["symbol"].(string)
``` [2](#0-1) 

If the target contract's `token_info`/`contract_info` query response omits `name`/`symbol`, or returns `null`/a non-string JSON value for either field, `formattedRes["name"]` is `nil` (as `interface{}`), and the assertion `nil.(string)` panics with `interface conversion: interface {} is nil, not string`. Because `cwAddress` is caller-controlled (`args[0].(string)`) and any account can deploy an arbitrary CosmWasm contract that implements a malformed `token_info`/`contract_info` query response, an attacker fully controls the panic trigger.

Unlike most other panic-risk code paths audited in this codebase (EVM ante handlers, `ProcessBlock`, giga executor, JSON-RPC HTTP/WS handlers — all of which wrap execution in `recover()`), the dynamic-gas precompile framework's `RunAndCalculateGas` (`precompiles/common/precompiles.go`, and its per-version copies) only converts a non-nil `error` return into `vm.ErrExecutionReverted`; it has **no `recover()`**. This is explicitly confirmed by the test `TestDynamicGasPrecompileRepanicsNonGas`, which asserts that a non-gas panic from the executor propagates rather than being masked: [3](#0-2) 

### Impact Explanation
A panic that escapes the precompile executor propagates up through the go-ethereum `vm.EVM` call frame into the surrounding transaction-execution path (`x/evm` state transition / giga executor). Based on the extensive panic-recovery patterns observed elsewhere in `app.go` (`ProcessBlock`, `makeGigaDeliverTx`, `ProcessTxsSynchronousGiga`), an outer recover in the transaction-processing pipeline will likely catch this and convert it into an `ErrPanic` ABCI response for that single transaction — meaning deterministic-per-node behavior is preserved and this would *not* trivially halt the chain or crash validators. However, this is not verified for every calling context (e.g., simulate/eth_call/eth_estimateGas RPC paths, or trace/debug RPC paths, which use different execution wrappers such as `SimulateConfig`), and the specific interaction with `evm.Create`/pointer-registration semantics under a panic (e.g., whether Cosmos-side state, like `p.evmKeeper.SetERC20CW20Pointer`, could be left partially applied before a later panic) was not fully traceable in the time available.

Given the outer `ProcessBlock`/giga panic recovery observed elsewhere converts unhandled panics into a normal failed-tx result rather than a node crash, the most defensible impact classification is a bounded (per-transaction) DoS / unexpected-revert-reason bug rather than a validator-halting crash — this weakens confidence that it meets the "block delay beyond 2.5 seconds" / "crash of default-configuration RPC nodes" bar required by the validation criteria.

### Likelihood Explanation
High: any address can permissionlessly instantiate a CosmWasm contract with a `token_info`/`contract_info` query handler that omits or returns non-string `name`/`symbol` fields, then call the `pointer` precompile's `addCW20`/`addCW721` EVM method against that contract address. No special privileges, association, or fee thresholds beyond ordinary transaction gas are required.

### Recommendation
Replace the unchecked type assertions with defensive checked assertions (`v, ok := formattedRes["name"].(string); if !ok { return nil, 0, fmt.Errorf(...) }`) in `AddCW20`/`AddCW721` across `precompiles/pointer/pointer.go` and all legacy versions (`precompiles/pointer/legacy/v555` through `v640`). More generally, add a `recover()` to `DynamicGasPrecompile.RunAndCalculateGas` (and its versioned copies) that converts any panic into a reverted-execution error, matching the defensive pattern already used throughout `app.go`/`x/evm/ante`, so that a single malformed downstream CW response cannot escape the precompile boundary as a raw Go panic.

### Proof of Concept
1. Deploy a CosmWasm contract whose `{"token_info":{}}` (or `{"contract_info":{}}`) query handler returns valid JSON that omits the `name` field (or returns `"name": null`), e.g. `{"symbol":"X","decimals":6,"total_supply":"0"}`.
2. From an EVM account, call the `pointer` precompile (`0x...` pointer address) method `addCW20Pointer(cwAddress)` (or `addCW721Pointer`) with `cwAddress` set to the deployed contract's bech32 address.
3. Inside `AddCW20`, `json.Unmarshal` succeeds; `formattedRes["name"]` is `nil`; `nil.(string)` panics with `interface conversion: interface {} is nil, not string`, which is not recovered anywhere in `RunAndCalculateGas`/`Execute` and propagates out of the precompile call.

**Confidence caveat:** I was not able to fully trace, within the available iterations, whether every entry point that can reach this precompile call (EVM `DeliverTx`, `eth_call`/`eth_estimateGas` simulation, `debug_trace*`) all sit behind an outer `recover()` that limits the blast radius to a single failed transaction. If any such path lacks outer panic recovery, the impact would escalate to a crash of that RPC/node process, which would meet the Critical/High bar in the validation criteria.

### Citations

**File:** precompiles/pointer/pointer.go (L140-156)
```go
	}
	cwAddr := args[0].(string)
	cwAddress, err := sdk.AccAddressFromBech32(cwAddr)
	if err != nil {
		return nil, 0, err
	}
	res, err := p.wasmdKeeper.QuerySmartSafe(ctx, cwAddress, []byte("{\"token_info\":{}}"))
	if err != nil {
		return nil, 0, err
	}
	formattedRes := map[string]interface{}{}
	if err := json.Unmarshal(res, &formattedRes); err != nil {
		return nil, 0, err
	}
	name := formattedRes["name"].(string)
	symbol := formattedRes["symbol"].(string)
	contractAddr, err := p.evmKeeper.UpsertERCCW20Pointer(ctx, evm, cwAddr, utils.ERCMetadata{Name: name, Symbol: symbol})
```

**File:** precompiles/common/precompiles_test.go (L140-158)
```go
// TestDynamicGasPrecompileRepanicsNonGas verifies that only gas-meter panics are
// converted to reverts: a non-gas panic must propagate rather than be masked.
func TestDynamicGasPrecompileRepanicsNonGas(t *testing.T) {
	k := &testkeeper.EVMTestApp.EvmKeeper
	ctx := testkeeper.EVMTestApp.GetContextForDeliverTx(nil)
	abiBz, err := os.ReadFile("erc20_abi.json")
	require.Nil(t, err)
	newAbi, err := abi.JSON(bytes.NewReader(abiBz))
	require.Nil(t, err)
	input, err := newAbi.Pack("decimals")
	require.Nil(t, err)

	precompile := common.NewDynamicGasPrecompile(newAbi, &MockDynamicGasPrecompileExecutor{panicWith: "boom", evmKeeper: k}, ethcommon.Address{}, "test")
	stateDB := state.NewDBImpl(ctx.WithEventManager(sdk.NewEventManager()), k, false)
	// Ample gas so the decode charges pass and the (panicking) executor runs.
	require.PanicsWithValue(t, "boom", func() {
		_, _, _ = precompile.RunAndCalculateGas(&vm.EVM{StateDB: stateDB}, ethcommon.Address{}, ethcommon.Address{}, input, 100000, big.NewInt(0), nil, false, false)
	})
}
```
