### Title
Unchecked type assertion on CosmWasm query result causes EVM node panic in pointer precompile - (File: precompiles/pointer/pointer.go)

### Summary
The `pointer` precompile's `AddCW20`, `AddCW721`, and `AddCW1155` handlers query an arbitrary, caller-supplied CosmWasm contract and then blindly type-assert two fields of the JSON response to `string` without the safe (`, ok`) form. Any pointer-eligible CW contract that omits `name`/`symbol` or returns them as a non-string JSON type causes an unrecovered Go runtime panic during EVM message execution, directly analogous to CVE-2022-3109's pattern of using an unchecked/unvalidated value that can be attacker-influenced and cause a crash.

### Finding Description
In `precompiles/pointer/pointer.go`, `AddCW20` calls the target contract with a `token_info` query and unmarshals the response into a generic `map[string]interface{}`, then does: [1](#0-0) 

```go
formattedRes := map[string]interface{}{}
if err := json.Unmarshal(res, &formattedRes); err != nil {
    return nil, 0, err
}
name := formattedRes["name"].(string)
symbol := formattedRes["symbol"].(string)
```

The same unchecked pattern appears in `AddCW721` and `AddCW1155`: [2](#0-1) [3](#0-2) 

`formattedRes["name"]` is a JSON-decoded `interface{}`. If the key is absent, `json.Unmarshal` leaves it as `nil`; if present but not a JSON string (number, bool, array, object, or explicit `null`), the decoded Go value is not a `string`. A single-value type assertion `.(string)` (without the two-value form) panics with `interface conversion: interface {} is <type>, not string` (or `nil` when the key is absent) instead of returning a checked error like the CW20 legacy path already does via `.(string)` too — every legacy version (`precompiles/pointer/legacy/v552/pointer.go` through `v67`) carries the identical bug.

This code path is reachable by any unprivileged EVM transaction sender or contract: `addCW20Pointer`/`addCW721Pointer`/`addCW1155Pointer` on the pointer precompile (`0x...100b`) accept an arbitrary `cwAddr` string, which is resolved via `sdk.AccAddressFromBech32` and then queried with `wasmdKeeper.QuerySmartSafe`. A user can deploy their own CosmWasm contract implementing the `token_info`/`contract_info` query handler to return a JSON object where `name` is, e.g., an integer or omitted, then call `addCW20Pointer` (or `addCW721Pointer`/`addCW1155Pointer`) against it from an EVM transaction.

I traced the call path up through the precompile execution wrapper `DynamicGasPrecompile.RunAndCalculateGas` in `precompiles/common/precompiles.go`, and confirmed the only panic recovery installed there is scoped narrowly to gas-meter panics inside `chargeDecodeGas`: [4](#0-3) 
The call into the executor's `Execute` method (which reaches `AddCW20`/`AddCW721`/`AddCW1155`) at line 206 has no surrounding `recover()`, so a panic from the unchecked type assertion propagates out of `RunAndCalculateGas` unrecovered at this layer: [5](#0-4) 

I was not able to fully verify, within the remaining tool budget, whether a higher layer (e.g., Cosmos SDK `BaseApp.runTx`, EVM ante handler, or the EVM message-execution wrapper) installs a general-purpose `recover()` that would convert this panic into an ordinary failed-transaction result rather than a node crash. A `grep_search` on `sei-cosmos/baseapp/baseapp.go` for `recover()`/`defer func` returned hits, but I ran out of iterations before reading and confirming their scope (e.g., whether they wrap the full `runMsgs`/precompile execution path or only ABCI-level goroutine recovery in `abci.go`). This is the key open question that determines final severity: if `runTx`'s panic recovery covers this call path (which is the normal case in Cosmos SDK, where `runTx` wraps message execution in a `recover()` that returns an error to the caller), the practical impact is limited to a single failed transaction (with gas consumed) rather than a node/RPC crash, which would fall outside the "Validate" impact bar this task requires (crash of default-configuration RPC nodes, chain split, or fund loss). If, however, this panic occurs during a code path executed by a background/async goroutine (e.g., an EVM tracer, a websocket/filter subscription replaying blocks, or gRPC/JSON-RPC query simulation code that does not go through `runTx`'s top-level recovery), it could crash that goroutine's host process.

### Impact Explanation
If the panic is not caught by `BaseApp.runTx`'s standard recovery (unconfirmed due to time constraints), any node executing or simulating this transaction — including the sequential re-execution during block replay, `eth_call`/`eth_estimateGas` simulation via query-only paths, or EVM tracing — would crash, since precompile execution happens inside the EVM interpreter loop and Go panics unwind through Go call frames independent of the EVM's own error/revert semantics unless explicitly caught. This is a plausible route to "crash of default-configuration RPC nodes" if the RPC's `eth_call`/`eth_estimateGas`/tracing code path does not itself wrap execution in a `recover()` distinct from `runTx`'s. If it is caught by standard SDK panic recovery, the practical impact is limited to a reverted transaction with no consensus-safety issue, which would not meet the required severity bar.

### Likelihood Explanation
Trivial to trigger: any address can deploy a CosmWasm contract whose `token_info`/`contract_info` query handler returns `{"name": 123, "symbol": "X", ...}` or omits `name`, then call `addCW20Pointer` (`0x000000000000000000000000000000000000100b`) from an EVM transaction pointing at that contract address. No special privileges, funds, or governance access are required — the same trust class as CVE-2022-3109 (any input reaching an unchecked value).

### Recommendation
Replace all six unchecked type assertions in `precompiles/pointer/pointer.go` (`AddCW20`, `AddCW721`, `AddCW1155`) and their legacy counterparts (`precompiles/pointer/legacy/v552` through `v67`) with the safe two-value form, returning an explicit error (e.g., "contract query response missing/invalid name or symbol field") instead of panicking, mirroring the existing `json.Unmarshal` error-handling style already used two lines above each assertion.

### Proof of Concept
1. Deploy a CosmWasm contract `C` whose query entry point responds to `{"token_info":{}}` with a JSON body such as `{"name": 42, "symbol": "X", "decimals": 6, "total_supply": "0"}` (numeric `name` instead of string), or simply omits the `name` key.
2. From any EVM account, call the pointer precompile at `0x000000000000000000000000000000000000100b` method `addCW20Pointer(cwAddr)` with `cwAddr` set to `C`'s bech32 address.
3. Execution reaches `precompiles/pointer/pointer.go:154` (`name := formattedRes["name"].(string)`), where the type assertion panics because the decoded value is not a `string`.
4. Observe whether the panic is caught by SDK-level transaction recovery (transaction fails) or propagates uncaught through a non-`runTx` execution path (e.g., simulation/tracing/query RPC), causing a node/process crash — this final determination requires further investigation of `sei-cosmos/baseapp/baseapp.go`'s panic-recovery scope, which I could not complete before running out of tool iterations.

### Citations

**File:** precompiles/pointer/pointer.go (L150-156)
```go
	formattedRes := map[string]interface{}{}
	if err := json.Unmarshal(res, &formattedRes); err != nil {
		return nil, 0, err
	}
	name := formattedRes["name"].(string)
	symbol := formattedRes["symbol"].(string)
	contractAddr, err := p.evmKeeper.UpsertERCCW20Pointer(ctx, evm, cwAddr, utils.ERCMetadata{Name: name, Symbol: symbol})
```

**File:** precompiles/pointer/pointer.go (L182-188)
```go
	formattedRes := map[string]interface{}{}
	if err := json.Unmarshal(res, &formattedRes); err != nil {
		return nil, 0, err
	}
	name := formattedRes["name"].(string)
	symbol := formattedRes["symbol"].(string)
	contractAddr, err := p.evmKeeper.UpsertERCCW721Pointer(ctx, evm, cwAddr, utils.ERCMetadata{Name: name, Symbol: symbol})
```

**File:** precompiles/pointer/pointer.go (L214-220)
```go
	formattedRes := map[string]interface{}{}
	if err := json.Unmarshal(res, &formattedRes); err != nil {
		return nil, 0, err
	}
	name := formattedRes["name"].(string)
	symbol := formattedRes["symbol"].(string)
	contractAddr, err := p.evmKeeper.UpsertERCCW1155Pointer(ctx, evm, cwAddr, utils.ERCMetadata{Name: name, Symbol: symbol})
```

**File:** precompiles/common/precompiles.go (L206-209)
```go
	ret, remainingGas, err = d.executor.Execute(ctx, method, caller, callingContract, args, value, readOnly, evm, suppliedGas, hooks)
	if err != nil {
		return ret, remainingGas, err
	}
```

**File:** precompiles/common/precompiles.go (L224-234)
```go
func (d DynamicGasPrecompile) chargeDecodeGas(ctx sdk.Context, method *abi.Method, input []byte) (err error) {
	defer func() {
		if r := recover(); r != nil {
			switch r.(type) {
			case sdk.ErrorOutOfGas, sdk.ErrorGasOverflow:
				err = fmt.Errorf("%v", r)
			default:
				panic(r)
			}
		}
	}()
```
