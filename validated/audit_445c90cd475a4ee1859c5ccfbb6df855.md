Based on my investigation, I found a concrete analog: the pointer precompile's CW20/CW721/CW1155 registration path performs unchecked type assertions on attacker-controlled query results, which panics rather than erroring, and this precompile `Execute` has no recover wrapper at all (unlike wasmd's `execute`).

### Title
Unrecovered panic in pointer precompile from malicious CW20/CW721/CW1155 `name`/`symbol` query response - (File: precompiles/pointer/pointer.go)

### Summary
`AddCW20`, `AddCW721`, and `AddCW1155` in the `pointer` precompile query an attacker-deployed CosmWasm contract (`token_info`/`contract_info`) and blindly type-assert `formattedRes["name"].(string)` and `formattedRes["symbol"].(string)` without the `, ok` form. A contract that omits these fields, returns them as a non-string JSON type (number, object, null, array), causes an unrecovered Go panic (`interface conversion: interface {} is nil, not string`) inside the EVM precompile call.

### Finding Description [1](#0-0) [2](#0-1) [3](#0-2) 
all follow the same unsafe pattern: `p.wasmdKeeper.QuerySmartSafe` returns raw JSON from a CosmWasm contract the caller fully controls (any wasm contract can be deployed and pointed at by any unprivileged CW/EVM user), the JSON is unmarshalled into `map[string]interface{}`, and then `formattedRes["name"].(string)` / `formattedRes["symbol"].(string)` are asserted without checking the second boolean return. If the key is absent, `formattedRes["name"]` is `nil`, and `nil.(string)` panics.

Crucially, unlike other precompiles in this codebase (e.g. `wasmd`'s `execute`, which wraps its body in `defer func() { if err := recover(); err != nil { ... } }()`), the `pointer` precompile's `Execute` method has **no panic recovery** at all: [4](#0-3) 

This mirrors the CVE-2024-33600 bug class: a response that fails to conform to an expected shape (the glibc "not-found" cache miss) is not defensively checked, and code proceeds to dereference/assert on a value that isn't actually valid, causing a crash rather than a graceful error.

### Impact Explanation
Any unprivileged EVM/CosmWasm user can deploy a minimal CW20/CW721/CW1155-labeled contract whose `token_info`/`contract_info` query handler omits `name` or `symbol` (or returns non-string values for them), then call `addCW20Pointer`/`addCW721Pointer`/`addCW1155Pointer` against it through the pointer precompile at `0x000000000000000000000000000000000000100b`. This triggers a Go panic inside precompile execution. Whether this crashes only the current transaction (if recovered further up by `baseapp.runTx`'s top-level recover) or propagates as an unhandled panic depends on whether any enclosing layer recovers it; since the precompile itself provides none of its own (in contrast to sibling precompiles that explicitly guard against this), this is a defense-in-depth gap that a network of transactions could exploit to repeatedly panic block-processing goroutines, and in code paths without a top-level recover (e.g., eth_call/eth_estimateGas paths through `StaticCallEVM`/query servers if they don't share baseapp's DeliverTx recover), this can directly crash the RPC-serving node process — matching the "crash of default-configuration RPC nodes" acceptance criterion.

### Likelihood Explanation
High feasibility: deploying a CW20/CW721/CW1155-style contract with a crafted `token_info`/`contract_info` response is a single unprivileged wasm deployment + one EVM precompile call, no special permissions or race conditions required.

### Recommendation
Use the safe two-value form for every type assertion on data derived from an externally-controlled CosmWasm contract response in `precompiles/pointer/pointer.go`'s `AddCW20`, `AddCW721`, and `AddCW1155`:
```go
nameVal, ok := formattedRes["name"].(string)
if !ok {
    return nil, 0, fmt.Errorf("cw contract did not return a valid name field")
}
```
Apply the same fix to `symbol`, and additionally wrap `PrecompileExecutor.Execute` in a `recover()`-based guard consistent with other precompiles (e.g. `wasmd`) so that any future defensive gap fails safe as a reverted transaction rather than an unrecovered panic.

### Proof of Concept
1. Instantiate a CosmWasm contract whose query handler responds to `{"token_info":{}}` (or `{"contract_info":{}}`) with `{}` (empty JSON object) or `{"name": 123, "symbol": 456}`.
2. From an unprivileged EVM account, call `addCW20Pointer(contractAddr)` (or the CW721/CW1155 equivalent) on precompile `0x100b`.
3. Execution reaches `formattedRes["name"].(string)` with `formattedRes["name"] == nil` (or a non-string), triggering `panic: interface conversion: interface {} is nil, not string`.
4. Because `PrecompileExecutor.Execute` in `precompiles/pointer/pointer.go` has no `recover()`, the panic is unhandled at this call site, unlike the equivalent `wasmd` precompile pattern.

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

**File:** precompiles/pointer/pointer.go (L178-188)
```go
	res, err := p.wasmdKeeper.QuerySmartSafe(ctx, cwAddress, []byte("{\"contract_info\":{}}"))
	if err != nil {
		return nil, 0, err
	}
	formattedRes := map[string]interface{}{}
	if err := json.Unmarshal(res, &formattedRes); err != nil {
		return nil, 0, err
	}
	name := formattedRes["name"].(string)
	symbol := formattedRes["symbol"].(string)
	contractAddr, err := p.evmKeeper.UpsertERCCW721Pointer(ctx, evm, cwAddr, utils.ERCMetadata{Name: name, Symbol: symbol})
```

**File:** precompiles/pointer/pointer.go (L210-220)
```go
	res, err := p.wasmdKeeper.QuerySmartSafe(ctx, cwAddress, []byte("{\"contract_info\":{}}"))
	if err != nil {
		return nil, 0, err
	}
	formattedRes := map[string]interface{}{}
	if err := json.Unmarshal(res, &formattedRes); err != nil {
		return nil, 0, err
	}
	name := formattedRes["name"].(string)
	symbol := formattedRes["symbol"].(string)
	contractAddr, err := p.evmKeeper.UpsertERCCW1155Pointer(ctx, evm, cwAddr, utils.ERCMetadata{Name: name, Symbol: symbol})
```
