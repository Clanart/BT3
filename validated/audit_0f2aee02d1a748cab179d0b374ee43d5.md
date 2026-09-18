Based on the code I found, there is a concrete analog: the CW20/CW721/CW1155 branches of the **pointer precompile** perform unchecked type assertions on JSON fields returned by an **attacker-controlled CosmWasm contract**, mirroring the reported bug class ("unvalidated input causing invalid data to be processed/stored").

### Title
Unchecked type assertion on attacker-controlled CW20/CW721/CW1155 query response in pointer precompile causes panic - (File: `precompiles/pointer/pointer.go`)

### Summary
`AddCW20`, `AddCW721`, and `AddCW1155` in the pointer precompile query a CosmWasm contract chosen by the caller (`cwAddr`, taken directly from unvalidated EVM calldata) and then blindly cast the returned `name`/`symbol` JSON fields to `string` without checking that the keys exist or hold the expected type.

### Finding Description
`AddCW20` resolves an arbitrary bech32 address supplied by the caller, queries it with `{"token_info":{}}`, unmarshals the JSON reply into a generic `map[string]interface{}`, and then does: [1](#0-0) 
Any account can deploy a CosmWasm contract that responds to `token_info`/`contract_info` queries with a JSON object that omits `name`/`symbol`, or returns them as a non-string type (number, bool, null, nested object). `formattedRes["name"].(string)` is an unchecked type assertion (not the two-value `v, ok := formattedRes["name"].(string)` form), so a missing key yields `nil.(string)` and a wrong-typed value yields a mismatched type assertion — both panic in Go. The same unchecked pattern repeats in `AddCW721`: [2](#0-1) 
and in `AddCW1155`: [3](#0-2) 
This is directly reachable by any unprivileged EVM caller: `cwAddr` is fully attacker-supplied, and the only prerequisite is deploying a trivial CosmWasm contract that answers the query with a malformed shape. Unlike `AddNative`, which derives `name`/`symbol` from `bank` module metadata with defensive default handling, these three functions never validate the shape of the externally-supplied JSON before using it.

### Impact Explanation
A panic thrown while processing an EVM transaction that reaches this code path occurs during transaction/block execution. If this panic is not recovered by an outer layer, it can crash the node processing the block (validator or RPC node), leading to a validator halt or repeated crash-and-restart cycle for any node that re-executes the same block/tx, i.e., a potential chain halt or block-processing delay well beyond normal bounds. I was not able to conclusively verify, within the available index, whether an outer recover() (e.g., in the go-ethereum EVM interpreter or in Sei's precompile dispatch wrapper) catches such panics before they propagate to node-crashing effect — this is a genuine gap in my verification and would need to be confirmed by tracing the full call path from `vm.EVM.Call` down through `Precompile.Run`/`RunAndCalculateGas` into `PrecompileExecutor.Execute`.

### Likelihood Explanation
Likelihood is high for triggering the panic itself: deploying a CosmWasm contract with a crafted `token_info`/`contract_info` response and calling the pointer precompile against it requires no special privilege, only a CosmWasm contract deployment and one EVM transaction. Likelihood of this actually causing a node crash (versus a caught/reverted error) depends on unverified panic-recovery behavior in the surrounding EVM/precompile dispatch code.

### Recommendation
Replace all unchecked type assertions in `AddCW20`, `AddCW721`, and `AddCW1155` with the two-value form and explicit validation, e.g.:
```go
nameVal, ok := formattedRes["name"].(string)
if !ok {
    return nil, 0, fmt.Errorf("invalid or missing 'name' field in contract query response")
}
symbolVal, ok := formattedRes["symbol"].(string)
if !ok {
    return nil, 0, fmt.Errorf("invalid or missing 'symbol' field in contract query response")
}
```
Additionally, confirm (and if absent, add) a top-level `recover()` around precompile execution so that malformed/malicious contract responses can never propagate into an unrecovered panic during block processing.

### Proof of Concept
1. Deploy a CosmWasm contract whose `token_info` query handler returns `{"decimals": 0}` (omitting `name`/`symbol`) or `{"name": 123, "symbol": 456}`.
2. From any EVM account, call the pointer precompile's `addCW20Pointer(cwAddr)` with the deployed contract's bech32 address.
3. Execution reaches `precompiles/pointer/pointer.go:154-155`, where `formattedRes["name"].(string)` panics due to the missing/mistyped key, propagating a runtime panic during transaction execution instead of a clean error revert. [4](#0-3)

### Citations

**File:** precompiles/pointer/pointer.go (L134-164)
```go
func (p PrecompileExecutor) AddCW20(ctx sdk.Context, method *ethabi.Method, caller common.Address, args []interface{}, value *big.Int, evm *vm.EVM, hooks *tracing.Hooks) (ret []byte, remainingGas uint64, err error) {
	if err := pcommon.ValidateNonPayable(value); err != nil {
		return nil, 0, err
	}
	if err := pcommon.ValidateArgsLength(args, 1); err != nil {
		return nil, 0, err
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
	if err != nil {
		return nil, 0, err
	}

	ret, err = method.Outputs.Pack(contractAddr)
	remainingGas = pcommon.GetRemainingGas(ctx, p.evmKeeper)
	return
}
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
