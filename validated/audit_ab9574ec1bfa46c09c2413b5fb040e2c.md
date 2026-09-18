Based on the investigation, I found a concrete analog to CVE-2017-12153's bug class (missing validation of expected fields leading to a crash via unchecked type coercion) in the `pointer` precompile.

### Title
Unchecked type assertions on external CW20/CW721/CW1155 query response fields in the pointer precompile cause a panic with no recover, unlike all sibling precompiles - (File: precompiles/pointer/pointer.go)

### Summary
`AddCW20`, `AddCW721`, and `AddCW1155` in the pointer precompile decode a `token_info`/`contract_info` JSON response from an arbitrary, caller-supplied CosmWasm contract and immediately perform unchecked Go type assertions (`formattedRes["name"].(string)`) on map values that are attacker-controlled, without validating the required keys exist or have the expected type. Unlike essentially every other precompile in the codebase, `pointer.PrecompileExecutor.Execute` has no `defer recover()` guard around this logic.

### Finding Description
`AddCW20` calls `p.wasmdKeeper.QuerySmartSafe(ctx, cwAddress, []byte("{\"token_info\":{}}"))`, unmarshals the raw response into `map[string]interface{}{}`, then does: [1](#0-0) 
If the target contract's `token_info` response omits the `"name"` or `"symbol"` keys, or returns them as a non-string JSON type (number, bool, object, null), the map lookup yields `nil`/wrong-type `interface{}`, and the unchecked assertion `.(string)` panics with `interface conversion: interface is nil, not string` (or a type mismatch). The identical pattern exists in `AddCW721` and `AddCW1155`: [2](#0-1) [3](#0-2) 

Critically, `Execute` for this precompile has no panic-recovery defer: [4](#0-3) 

This is inconsistent with the established pattern used by essentially every other precompile in the repo (`addr`, `evidence`, `mint`, `params`, `solo`, `wasmd` `instantiate`), all of which wrap `Execute`/handler logic in `defer func() { if r := recover(); r != nil { err = ... } }()` specifically to catch panics such as bad type assertions or gas-meter overruns: [5](#0-4) [6](#0-5) 

The generic precompile `Run` wrapper (`precompiles/common/legacy/v630/precompiles.go`) only handles the returned `error`, it does not itself recover from panics raised inside `p.executor.Execute`: [7](#0-6) 

This mirrors the CVE-2017-12153 bug class exactly: a handler trusts that a required attribute/field is present and of the expected shape without validating it first, and an unprivileged caller (here, the deployer of the CW20/CW721/CW1155 contract used as the pointer target, combined with anyone invoking `addCW20Pointer`/`addCW721Pointer`/`addCW1155Pointer`) can supply a response lacking that field, triggering a NULL/invalid-type dereference panic instead of a NULL pointer dereference in kernel memory.

### Impact Explanation
An unrecovered Go panic crashes the entire process unless caught by a `recover()` somewhere in the same goroutine's call stack. I could not fully verify, given the available context, whether the goroutine handling `eth_call`/`eth_estimateGas` JSON-RPC requests (a public, unauthenticated RPC surface reachable without submitting a transaction) has an outer-level `recover()` that would catch this panic before it crashes the node process; `x/evm/keeper/msg_server.go` does contain a `recover()`, but that path is specific to processing `MsgEVMTransaction` during block execution, not necessarily to the `eth_call`/query path used by public RPC clients. If the RPC/query path lacks an enclosing recover, a single crafted read-only `eth_call` to `addCW20Pointer`/`addCW721Pointer`/`addCW1155Pointer` against an attacker-deployed CW20/CW721/CW1155 contract that returns a malformed `token_info`/`contract_info` response would crash the RPC node — a validator or full node included. If block-processing does catch the panic via `msg_server.go`'s recover, the impact is reduced to a failed transaction rather than a node crash, but the missing validation itself remains a defect exploitable by any pointer user.

### Likelihood Explanation
High: no privilege is required beyond deploying an ordinary CosmWasm contract (any address can do this) and calling `addCW20Pointer`, `addCW721Pointer`, or `addCW1155Pointer` on it — a straightforward, one-transaction (or one `eth_call`) attack with no special preconditions.

### Recommendation
Add the same `defer func() { if r := recover(); r != nil { err = ... } }()` guard used by other precompiles to `pointer.PrecompileExecutor.Execute` (and its legacy versions), and additionally use safe, checked type assertions (`v, ok := formattedRes["name"].(string); if !ok { return nil, 0, fmt.Errorf(...) }`) for `name`/`symbol` in `AddCW20`, `AddCW721`, and `AddCW1155` so malformed external contract responses produce a normal error instead of a panic.

### Proof of Concept
1. Deploy a CosmWasm contract whose `token_info` (or `contract_info`) query handler returns JSON such as `{"name": 12345, "symbol": null}` (or omits `"name"`/`"symbol"` entirely).
2. Call the `pointer` precompile's `addCW20Pointer(contractAddr)` (or `addCW721Pointer`/`addCW1155Pointer`) via an EVM transaction or `eth_call` targeting that contract address.
3. In `AddCW20`, `formattedRes["name"].(string)` panics on the non-string/absent value: [8](#0-7) 
4. Because `Execute` has no `recover()`, the panic propagates up the call stack; depending on whether the invoking RPC/query path has its own recovery, this can crash the node process handling the request.

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

**File:** precompiles/pointer/pointer.go (L150-155)
```go
	formattedRes := map[string]interface{}{}
	if err := json.Unmarshal(res, &formattedRes); err != nil {
		return nil, 0, err
	}
	name := formattedRes["name"].(string)
	symbol := formattedRes["symbol"].(string)
```

**File:** precompiles/pointer/pointer.go (L186-188)
```go
	name := formattedRes["name"].(string)
	symbol := formattedRes["symbol"].(string)
	contractAddr, err := p.evmKeeper.UpsertERCCW721Pointer(ctx, evm, cwAddr, utils.ERCMetadata{Name: name, Symbol: symbol})
```

**File:** precompiles/pointer/pointer.go (L218-220)
```go
	name := formattedRes["name"].(string)
	symbol := formattedRes["symbol"].(string)
	contractAddr, err := p.evmKeeper.UpsertERCCW1155Pointer(ctx, evm, cwAddr, utils.ERCMetadata{Name: name, Symbol: symbol})
```

**File:** precompiles/addr/addr.go (L93-99)
```go
func (p PrecompileExecutor) Execute(ctx sdk.Context, method *abi.Method, _ common.Address, _ common.Address, args []interface{}, value *big.Int, readOnly bool, _ *vm.EVM, suppliedGas uint64, hooks *tracing.Hooks) (ret []byte, remainingGas uint64, err error) {
	// Needed to catch gas meter panics
	defer func() {
		if r := recover(); r != nil {
			err = fmt.Errorf("execution reverted: %v", r)
		}
	}()
```

**File:** precompiles/solo/solo.go (L90-99)
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

**File:** precompiles/common/legacy/v630/precompiles.go (L64-90)
```go
func (p Precompile) Run(evm *vm.EVM, caller common.Address, callingContract common.Address, input []byte, value *big.Int, readOnly bool, isFromDelegateCall bool, hooks *tracing.Hooks) (bz []byte, err error) {
	operation := fmt.Sprintf("%s_unknown", p.name)
	defer func() {
		HandlePrecompileError(err, evm, operation)
		if err != nil {
			err = vm.ErrExecutionReverted
		}
	}()
	ctx, method, args, err := p.Prepare(evm, input)
	if err != nil {
		return nil, err
	}

	operation = method.Name
	em := ctx.EventManager()
	ctx = ctx.WithEventManager(sdk.NewEventManager())
	ctx = ctx.WithEVMPrecompileCalledFromDelegateCall(isFromDelegateCall)
	bz, err = p.executor.Execute(ctx, method, caller, callingContract, args, value, readOnly, evm, hooks)
	if err != nil {
		return bz, err
	}
	events := ctx.EventManager().Events()
	if len(events) > 0 {
		em.EmitEvents(ctx.EventManager().Events())
	}
	return bz, err
}
```
