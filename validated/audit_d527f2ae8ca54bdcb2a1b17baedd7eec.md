Confirmed: the pointer precompile's `AddCW20`/`AddCW721`/`AddCW1155` handlers perform unchecked type assertions on attacker-controlled JSON, and the outer `DynamicGasPrecompile.RunAndCalculateGas` wrapper only recovers `HandlePrecompileError` for non-panic `err` values — the panic itself is not recovered inside these specific handlers, and propagates to `EVMTransaction`'s deferred `recover()` in `x/evm/keeper/msg_server.go`, which re-panics (`panic(pe)`) after logging, crashing/halting the node's block execution rather than reverting the transaction. This matches the CVE-2018-20429 bug class (unchecked nil/type assumption on external, untrusted data causing a crash) but reachable via a public precompile call instead of a library parser.

### Title
Unrecovered nil-interface type-assertion panic in the pointer precompile's `AddCW20`/`AddCW721`/`AddCW1155` handlers causes an unhandled EVM transaction panic - (File: `precompiles/pointer/pointer.go`)

### Summary
The `pointer` precompile (address `0x...100b`) lets any EVM caller register a CW20/CW721/CW1155 pointer contract by querying the target CosmWasm contract's `token_info`/`contract_info` and unmarshalling the JSON response into a `map[string]interface{}`, then directly type-asserting `formattedRes["name"].(string)` and `formattedRes["symbol"].(string)` without the safe two-value form. [1](#0-0) [2](#0-1) [3](#0-2) 

### Finding Description
If the queried CW20/CW721/CW1155 contract's `token_info`/`contract_info` query response JSON is missing the `name`/`symbol` keys, or returns them as a non-string type (e.g. `null`, a number, or omitted entirely), `formattedRes["name"]` evaluates to a `nil` interface value. A single-value type assertion `nil.(string)` panics with `interface conversion: interface {} is nil, not string`, analogous to a null-pointer dereference triggered by attacker-controlled untrusted data (the libming `getName` NULL-deref class). Since the caller deploys and controls the CW20/CW721/CW1155 contract being pointed at, they fully control the JSON shape returned by its query handler, making this trivially triggerable. Unlike other precompiles in this repo (e.g. `precompiles/addr/addr.go` and `precompiles/solo/solo.go`, both of which wrap `Execute` in a `defer recover()` specifically "to catch gas meter panics"), the current `pointer.PrecompileExecutor.Execute` has no such `recover()`. [4](#0-3) [5](#0-4) 

The generic `DynamicGasPrecompile.RunAndCalculateGas` wrapper (used to invoke `pointer.PrecompileExecutor.Execute`) only converts a returned `err` into `vm.ErrExecutionReverted` inside its `defer`; it does not itself `recover()` a Go panic, so a panic from `Execute` propagates up through the EVM interpreter and `applyEVMMessage`. [6](#0-5) 

That panic is ultimately caught by the top-level `defer recover()` in `msgServer.EVMTransaction`, but that handler explicitly re-`panic`s any non-OCC-read-estimate recovered value after logging it as an "EVM PANIC", rather than converting it into a failed/reverted transaction. [7](#0-6) 

### Impact Explanation
A crafted CW20/CW721/CW1155 contract deployed by any unprivileged user, when pointed at via `addCW20Pointer`/`addCW721Pointer`/`addCW1155Pointer` on the `pointer` precompile, produces an unrecovered Go panic that re-propagates out of `EVMTransaction`. This is consistent with a crash of the node processing the block/transaction (a validator halt / block-processing panic), which the scoring rules classify as a valid analog (validator halt / block delay) rather than a simple revert.

### Likelihood Explanation
High likelihood: any account can permissionlessly instantiate a CW20/CW721/CW1155 contract whose `token_info`/`contract_info` query returns JSON without `name`/`symbol` fields (or with non-string values), then call the public pointer precompile method against it from a plain EVM transaction. No special privileges, governance, or validator cooperation are required — this is reachable purely through public transaction submission.

### Recommendation
Use the safe comma-ok form for these type assertions (`name, ok := formattedRes["name"].(string); if !ok { return nil, 0, fmt.Errorf(...) }`) in `AddCW20`, `AddCW721`, and `AddCW1155` in `precompiles/pointer/pointer.go` (and the equivalent legacy versions under `precompiles/pointer/legacy/*`), and/or add a `defer recover()` guard around `PrecompileExecutor.Execute` consistent with the pattern already used in `precompiles/addr/addr.go` and `precompiles/solo/solo.go`, converting any panic into an `execution reverted` error instead of letting it propagate to the transaction-level panic handler.

### Proof of Concept
1. Deploy (via the wasmd/CosmWasm path) a minimal CW20-like contract whose `token_info` smart query handler returns `{}` (or `{"decimals": 6}` without `name`/`symbol` keys, or `{"name": null, "symbol": null}`).
2. As any EVM account, call `addCW20Pointer(cwAddr)` on the pointer precompile (`0x000000000000000000000000000000000000100b`) with `cwAddr` set to the deployed contract's bech32 address.
3. Inside `AddCW20`, `p.wasmdKeeper.QuerySmartSafe` succeeds and returns the crafted JSON; `json.Unmarshal` succeeds; `formattedRes["name"].(string)` panics because the map value is `nil`, not a string.
4. The panic is not recovered in `pointer.PrecompileExecutor.Execute` or in `DynamicGasPrecompile.RunAndCalculateGas`, and propagates to `msgServer.EVMTransaction`'s deferred recover, which re-panics after logging `"EVM PANIC"`. [8](#0-7) [9](#0-8)

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

**File:** precompiles/common/legacy/v67/precompiles.go (L158-217)
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
	ctxer := state.GetDBImpl(evm.StateDB)
	if ctxer == nil {
		return nil, 0, errors.New("cannot get context from EVM")
	}
	// Resolve the target method from the 4-byte selector only. The argument
	// payload is intentionally NOT decoded yet: ABI decoding of attacker-
	// controlled calldata can cost far more than len(input) (a single string can
	// be referenced by many array/tuple slots), so it must be paid for out of the
	// gas the caller supplied. The static-precompile path charges RequiredGas in
	// vm.RunPrecompiledContract before running; that step is skipped for
	// dynamic-gas precompiles, so we apply the equivalent charge here.
	methodID, err := ExtractMethodID(input)
	if err != nil {
		return nil, 0, err
	}
	method, err := d.MethodById(methodID)
	if err != nil {
		return nil, 0, err
	}
	operation = method.Name

	ctx := ctxer.Ctx()
	// Install the gas meter derived from the supplied EVM gas, then charge for
	// decoding the calldata BEFORE decoding it. A call that cannot afford the
	// decode is rejected here, before the parse/allocation work is performed.
	// chargeDecodeGas scopes the out-of-gas recovery to just these charges, so an
	// executor that later exhausts its gas keeps its normal (propagating)
	// out-of-gas semantics.
	gasLimit := d.executor.EVMKeeper().GetCosmosGasLimitFromEVMGas(ctx.WithGasMeter(sdk.NewInfiniteGasMeterWithMultiplier(ctx)), suppliedGas)
	ctx = ctx.WithGasMeter(sdk.NewGasMeterWithMultiplier(ctx, gasLimit))
	if err = d.chargeDecodeGas(ctx, method, input); err != nil {
		return nil, 0, err
	}

	args, err := method.Inputs.Unpack(input[4:])
	if err != nil {
		return nil, 0, err
	}
	em := ctx.EventManager()
	ctx = ctx.WithEventManager(sdk.NewEventManager())
	ctx = ctx.WithEVMPrecompileCalledFromDelegateCall(isFromDelegateCall)
	ret, remainingGas, err = d.executor.Execute(ctx, method, caller, callingContract, args, value, readOnly, evm, suppliedGas, hooks)
	if err != nil {
		return ret, remainingGas, err
	}
	events := ctx.EventManager().Events()
	if len(events) > 0 {
		em.EmitEvents(ctx.EventManager().Events())
	}
	return ret, remainingGas, err
}
```

**File:** x/evm/keeper/msg_server.go (L80-89)
```go
	defer func() {
		defer stateDB.Cleanup()
		if pe := recover(); pe != nil {
			if !strings.Contains(fmt.Sprintf("%s", pe), occtypes.ErrReadEstimate.Error()) {
				debug.PrintStack()
				logger.Error("EVM PANIC", "err", pe)
				evmKeeperMetrics.panics.Add(goCtx, 1)
			}
			panic(pe)
		}
```
