Confirmed: this dynamic-gas precompile framework does **not** wrap `Execute` in a `recover()` — the test `TestDynamicGasPrecompileRepanicsNonGas` explicitly documents that "a non-gas panic must propagate rather than be masked." This means an unrecovered Go panic inside a precompile executor propagates all the way up through the EVM call stack.

### Title
Unrecovered type-assertion panic in the CW20/CW721/CW1155 pointer precompile crashes block execution (Denial of Service) - (File: precompiles/pointer/pointer.go)

### Summary
The `pointer` precompile's `AddCW20`, `AddCW721`, and `AddCW1155` handlers unmarshal an arbitrary CosmWasm contract's smart-query response into a `map[string]interface{}` and then perform unchecked Go type assertions on the `"name"` and `"symbol"` fields, without validating that the target contract actually implements the expected interface.

### Finding Description
Any unprivileged EVM caller can invoke `addCW20Pointer(cwAddr)`, `addCW721Pointer(cwAddr)`, or `addCW1155Pointer(cwAddr)` on the pointer precompile (`0x000000000000000000000000000000000000100b`) with an attacker-controlled `cwAddr`. The handler queries the target CosmWasm contract (`token_info` for CW20, `contract_info` for CW721/CW1155) and blindly does: [1](#0-0) 

If the target is any CosmWasm contract (deployable by any user) that either does not implement `token_info`/`contract_info` in the exact expected shape, or returns a JSON response where `"name"`/`"symbol"` are absent, `null`, or a non-string type (e.g. a number, object, or array), `formattedRes["name"].(string)` performs an unchecked type assertion on an `interface{}`. In Go, a bare (non-`, ok`) type assertion on a mismatched or `nil` type **panics**.

This exact same unguarded pattern is repeated in `AddCW721` and `AddCW1155`: [2](#0-1) [3](#0-2) 

Critically, the dynamic-gas precompile dispatcher `RunAndCalculateGas` does **not** recover from generic panics — it only masks specific known conditions and intentionally lets other panics propagate, as verified by the test asserting this exact behavior: [4](#0-3) [5](#0-4) 

Since any CosmWasm user can deploy a minimal contract that responds to `{"token_info":{}}` or `{"contract_info":{}}` with a JSON body lacking string `name`/`symbol` fields (e.g. `{}` or `{"name": 123}`), an attacker can trivially trigger this panic by calling the pointer precompile against their own malicious contract from an ordinary EVM transaction.

### Impact Explanation
An unrecovered Go panic escaping the EVM execution path is not a normal EVM revert — it unwinds the Go call stack outside the sandboxed `RunPrecompiledContract`/`RunAndCalculateGas` machinery that is supposed to catch it. Because the test suite documents that non-gas panics are deliberately allowed to propagate, this panic will propagate up through `ApplyMessage`/`DeliverTx` and can crash the node process handling the transaction (in `DeliverTx`/block processing this would abort block execution on all validators that include the transaction, since it is a deterministic panic reproducible by every validator processing the same tx). This satisfies the "crash of default-configuration RPC/consensus nodes" and "block delay/validator halt" impact classes, since a panic during block application halts that validator's block processing for the offending block.

### Likelihood Explanation
High. Triggering this requires only:
1. Deploying an arbitrary CosmWasm contract (permissionless on Sei) that responds to the relevant query with a body lacking a string `name`/`symbol` field.
2. Calling `addCW20Pointer`/`addCW721Pointer`/`addCW1155Pointer` on the pointer precompile with that contract's address via a normal EVM transaction.

No special privileges, governance, or validator access are required — this is reachable by any public RPC client submitting a standard transaction.

### Recommendation
Replace the unchecked type assertions with the comma-ok idiom and return a proper error instead of panicking, e.g.:
```go
name, ok := formattedRes["name"].(string)
if !ok {
    return nil, 0, fmt.Errorf("cw contract %s did not return a valid name field", cwAddr)
}
symbol, ok := formattedRes["symbol"].(string)
if !ok {
    return nil, 0, fmt.Errorf("cw contract %s did not return a valid symbol field", cwAddr)
}
```
Apply this to `AddCW20`, `AddCW721`, and `AddCW1155` in `precompiles/pointer/pointer.go` (and any legacy versioned copies still reachable via the current dispatcher). Additionally, consider adding a top-level `recover()` in `RunAndCalculateGas` for genuinely unexpected panics from executors (distinct from the intentional gas-panic passthrough) so that malformed external contract responses cannot escalate to a node crash.

### Proof of Concept
1. Deploy a CosmWasm contract `Evil` whose `token_info` query handler returns `{}` (no `name`/`symbol` keys) or `{"name": 1, "symbol": 2}` (non-string types).
2. From any EVM account, call the pointer precompile at `0x000000000000000000000000000000000000100b` with `addCW20Pointer(EvilContractSeiAddress)`.
3. Execution reaches `formattedRes["name"].(string)` in `AddCW20` (precompiles/pointer/pointer.go:154), which panics because the map value is either absent (nil interface) or not a `string`.
4. Because `RunAndCalculateGas` does not recover generic panics (confirmed by `TestDynamicGasPrecompileRepanicsNonGas`), the panic propagates out of the precompile call and up through EVM/ante-handler execution, aborting processing of the block containing this transaction on every validator that processes it.

### Citations

**File:** precompiles/pointer/pointer.go (L146-156)
```go
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

**File:** precompiles/common/precompiles.go (L156-164)
```go
func (d DynamicGasPrecompile) RunAndCalculateGas(evm *vm.EVM, caller common.Address, callingContract common.Address, input []byte, suppliedGas uint64, value *big.Int, hooks *tracing.Hooks, readOnly bool, isFromDelegateCall bool) (ret []byte, remainingGas uint64, err error) {
	operation := fmt.Sprintf("%s_unknown", d.name)
	defer func() {
		HandlePrecompileError(err, evm, operation)
		if err != nil {
			fmt.Printf("precompile %s encountered error: %v\n", d.name, err)
			err = vm.ErrExecutionReverted
		}
	}()
```
