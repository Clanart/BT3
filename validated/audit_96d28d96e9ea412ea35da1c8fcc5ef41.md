Confirmed: `RunAndCalculateGas` in `precompiles/common/precompiles.go` (`DynamicGasPrecompile.RunAndCalculateGas`, line 156) has no top-level `recover()` guarding the call to `d.executor.Execute(...)` at line 206 — the only `recover()` in that path is scoped narrowly to `chargeDecodeGas` (lines 224-234) and re-panics anything that is not `sdk.ErrorOutOfGas`/`sdk.ErrorGasOverflow`. This is corroborated by the test comment in `precompiles/common/precompiles_test.go:140-142`: "only gas-meter panics are converted to reverts: a non-gas panic must propagate rather than be masked." So a runtime panic raised inside `PrecompileExecutor.Execute` (e.g., a failed type assertion) is not caught at the precompile-dispatch layer.

### Title
Unrecovered type-assertion panic in CW20/CW721 pointer precompile crashes/halts block processing on malformed CosmWasm contract responses - (File: precompiles/pointer/pointer.go)

### Summary
`AddCW20`/`AddCW721` in the pointer precompile (`precompiles/pointer/pointer.go:134-164`, and duplicated across `precompiles/pointer/legacy/v552`…`v67`) query an arbitrary attacker-supplied CosmWasm contract address and blindly assume the JSON response conforms to the CW20/CW721 shape, performing unchecked type assertions `formattedRes["name"].(string)` and `formattedRes["symbol"].(string)` [1](#0-0) . Any CosmWasm contract can be pointed at — nothing verifies it actually implements the `token_info`/`contract_info` query in the expected shape — so a contract that returns a JSON object without a `"name"`/`"symbol"` string field (e.g. missing field, null, or non-string value) triggers a Go runtime panic on the failed type assertion.

### Finding Description
This closely mirrors the CVE-2025-38252 bug class: a handler assumes the identified object is of a specific type/shape without verifying it, then dereferences data as if that assumption held, leading to a crash. Here, `AddCW20`/`AddCW721` accept any bech32 CosmWasm address supplied by the EVM caller, call `wasmdKeeper.QuerySmart`/`QuerySmartSafe`, and directly type-assert `"name"` and `"symbol"` fields as `string` without checking existence or type [2](#0-1) . A contract need not be an actual cw20/cw721 token — it only needs to respond successfully to the specific query message with JSON lacking the expected fields (or with those fields as non-string types), which is trivial for anyone deploying a custom CosmWasm contract.

Unlike other precompiles in the codebase (e.g. `precompiles/addr`, `precompiles/oracle`, `precompiles/solo`) which explicitly wrap `Execute` with `defer recover()` "to catch gas meter panics" (and in `solo`, any panic) [3](#0-2) , the pointer precompile's `Execute` has no such guard [4](#0-3) , and the shared dispatch layer `DynamicGasPrecompile.RunAndCalculateGas` only recovers gas-meter-specific panics inside the narrow `chargeDecodeGas` helper, explicitly re-panicking anything else [5](#0-4) . This design is intentional per the regression test comment: "a non-gas panic must propagate rather than be masked" [6](#0-5) .

### Impact Explanation
A panic that propagates out of `d.executor.Execute` at `precompiles/common/precompiles.go:206` is not recovered by any code in this call path. Whether this crashes/halts a node depends on whether outer EVM/state-transition or ante-handler layers install their own top-level `recover()` — this could not be conclusively confirmed within the scope of the search performed (that boundary lives in `x/evm` message handling / geth's EVM interpreter loop, which was not fully traced here). If no outer recovery exists at that layer, every validator processing the same block containing the malicious transaction would panic deterministically and identically, which for a Tendermint/CometBFT-based chain typically manifests as a consensus-halting node crash (all validators fail identically, rather than diverging state) — matching the High severity bar. If an outer recovery does exist, the practical impact is a deterministic EVM-level revert (similar to the CVE's low-severity outcome), not a crash.

### Likelihood Explanation
Trivial to trigger: any unprivileged account can deploy a minimal CosmWasm contract that responds successfully to `{"token_info":{}}` or `{"contract_info":{}}` with JSON missing the `name`/`symbol` keys (or with non-string values), then call `addCW20Pointer`/`addCW721Pointer` on the pointer precompile at `0x000000000000000000000000000000000000100b` with that contract's address. This requires no privileges beyond submitting an EVM transaction and deploying/controlling a CosmWasm contract, both of which are explicitly in-scope reachable primitives.

### Recommendation
In `AddCW20`/`AddCW721` (`precompiles/pointer/pointer.go` and all `precompiles/pointer/legacy/*` copies), replace the unchecked type assertions with safe, checked extraction (`value, ok := formattedRes["name"].(string); if !ok { return nil, 0, fmt.Errorf(...) }`) for both `name` and `symbol`, returning a normal error instead of panicking. Additionally, consider adding a top-level `defer recover()` in the pointer precompile's `Execute` (consistent with `precompiles/solo`) as defense-in-depth against any other unchecked type assertions/panics reachable from attacker-controlled CW query responses.

### Proof of Concept
1. Deploy a CosmWasm contract whose query handler responds to `{"token_info":{}}` with `{}` (or `{"name": 123, "symbol": null}` — non-string/missing fields) and returns success.
2. From an EVM account, call `addCW20Pointer(cwContractAddress)` on the pointer precompile at `0x000000000000000000000000000000000000100b`.
3. `p.wasmdKeeper.QuerySmart` succeeds, `json.Unmarshal` succeeds, but `formattedRes["name"].(string)` panics with `interface conversion: interface {} is nil, not string` (or similar) at `precompiles/pointer/pointer.go:154-155`.
4. This panic propagates unrecovered through `DynamicGasPrecompile.RunAndCalculateGas` (`precompiles/common/precompiles.go:206`), whose only panic-guard is scoped to gas-meter errors in `chargeDecodeGas`.

### Citations

**File:** precompiles/pointer/pointer.go (L72-93)
```go
func (p PrecompileExecutor) Execute(ctx sdk.Context, method *ethabi.Method, caller common.Address, callingContract common.Address, args []interface{}, value *big.Int, readOnly bool, evm *vm.EVM, suppliedGas uint64, hooks *tracing.Hooks) (ret []byte, remainingGas uint64, err error) {
	if readOnly {
		return nil, 0, errors.New("cannot call pointer precompile from staticcall")
	}
	if ctx.EVMPrecompileCalledFromDelegateCall() {
		return nil, 0, errors.New("cannot delegatecall pointer")
	}

	switch method.Name {
	case AddNativePointer:
		return p.AddNative(ctx, method, caller, args, value, evm, hooks)
	case AddCW20Pointer:
		return p.AddCW20(ctx, method, caller, args, value, evm, hooks)
	case AddCW721Pointer:
		return p.AddCW721(ctx, method, caller, args, value, evm, hooks)
	case AddCW1155Pointer:
		return p.AddCW1155(ctx, method, caller, args, value, evm, hooks)
	default:
		err = fmt.Errorf("unknown method %s", method.Name)
	}
	return
}
```

**File:** precompiles/pointer/pointer.go (L141-156)
```go
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

**File:** precompiles/solo/legacy/v614/solo.go (L90-99)
```go
func (p PrecompileExecutor) Execute(ctx sdk.Context, method *abi.Method, caller common.Address, callingContract common.Address, args []interface{}, value *big.Int, readOnly bool, evm *vm.EVM, suppliedGas uint64, _ *tracing.Hooks) (ret []byte, remainingGas uint64, err error) {
	// Needed to catch gas meter panics
	defer func() {
		if r := recover(); r != nil {
			err = fmt.Errorf("execution reverted: %v", r)
			ret = nil
			remainingGas = 0
			return
		}
	}()
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

**File:** precompiles/common/precompiles_test.go (L140-142)
```go
// TestDynamicGasPrecompileRepanicsNonGas verifies that only gas-meter panics are
// converted to reverts: a non-gas panic must propagate rather than be masked.
func TestDynamicGasPrecompileRepanicsNonGas(t *testing.T) {
```
