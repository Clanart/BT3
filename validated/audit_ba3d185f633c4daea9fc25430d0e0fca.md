### Title
Unchecked type assertions on CW20/CW721/CW1155 query responses cause a panic in the pointer precompile ([File: precompiles/pointer/pointer.go])

### Summary
The `addCW20Pointer`, `addCW721Pointer`, and `addCW1155Pointer` methods of the `pointer` precompile (address `0x100b`) unmarshal a JSON response returned by an arbitrary, caller-specified CosmWasm contract and then blindly type-assert two of its fields to `string` with no `ok`-check and no panic recovery around the call path, mirroring the CVE's root cause of "missing data block not handled" causing a dissector crash.

### Finding Description
`PrecompileExecutor.AddCW20`, `AddCW721`, and `AddCW1155` in [1](#0-0)  and [2](#0-1)  query an attacker-controlled CosmWasm contract via `QuerySmartSafe` with `{"token_info":{}}` / `{"contract_info":{}}`, unmarshal the JSON reply into `map[string]interface{}`, and then do:
```go
name := formattedRes["name"].(string)
symbol := formattedRes["symbol"].(string)
```
If the queried contract's response omits the `name`/`symbol` keys (missing data block, `nil` interface) or returns them as a non-string JSON type (e.g. a number or object), this is an unchecked type assertion that panics at runtime — `interface conversion: interface {} is nil, not string` (or `... is float64, not string`) — exactly analogous to the Wireshark ISAKMP dissector crashing on a missing decryption data block that it failed to validate before using.

Unlike sibling precompiles in this codebase, this call path has no panic recovery: `PrecompileExecutor.Execute` in [3](#0-2)  contains no `recover()`, and the wrapper that invokes it, `DynamicGasPrecompile.RunAndCalculateGas`, also has no `recover()` around `d.executor.Execute(...)` [4](#0-3) . This is in contrast to other precompiles such as `addr` and `solo`, whose `Execute` methods explicitly wrap the call body in `defer func(){ if r := recover(); ... }` to convert panics into a reverted call [5](#0-4) .

Because any Sei/EVM address can deploy an arbitrary CW20/CW721/CW1155-labelled contract and then call `addCW20Pointer`/`addCW721Pointer`/`addCW1155Pointer` pointing at it, the panic is fully attacker-triggerable and reachable from a single submitted EVM transaction or `eth_call`/`eth_estimateGas` RPC request that targets precompile `0x100b`.

### Impact Explanation
An unrecovered Go panic from this call path will propagate up through `vm.EVM.Call`. For transactions executed through `DeliverTx`/`CheckTx`, cosmos-sdk's `BaseApp.runTx` has an outer `recover()` that will typically catch this and turn it into a failed transaction rather than a node crash — reducing impact to a reliably-failing transaction (denial of a specific pointer-registration feature, plus wasted gas by the caller). However, if this panic is triggered via a JSON-RPC path that evaluates the EVM directly for simulation (`eth_call`/`eth_estimateGas`) without going through the same baseapp recover wrapper, an unrecovered panic in a Go RPC handler goroutine terminates the entire process, which would constitute a crash of a default-configuration public RPC node. I was not able to fully trace whether Sei's `eth_call`/`eth_estimateGas` handler installs an equivalent top-level recover before invoking the EVM, so the severity ceiling (chain-wide validator halt vs. isolated RPC-node crash vs. simple failed-tx DoS) is uncertain without further investigation of that specific code path.

### Likelihood Explanation
Likelihood is high for the "failed transaction" case: any account can deploy a trivial CosmWasm contract whose `token_info`/`contract_info` query omits `name`/`symbol` or returns them with the wrong JSON type, then call `addCW20Pointer`/`addCW721Pointer`/`addCW1155Pointer` against it with no special privileges. Likelihood of the more severe RPC-crash outcome depends on the (unverified) presence/absence of panic recovery in the `eth_call` execution path.

### Recommendation
Replace the unchecked type assertions with safe, checked extraction (`v, ok := formattedRes["name"].(string); if !ok { return nil, 0, fmt.Errorf(...) }`) for both `name` and `symbol` in `AddCW20`, `AddCW721`, and `AddCW1155`. Additionally, add a `recover()`-based panic guard in `PrecompileExecutor.Execute` (or in `DynamicGasPrecompile.RunAndCalculateGas`) consistent with the pattern already used in `precompiles/addr` and `precompiles/solo`, so that any future unhandled panic in a precompile executor is converted into `vm.ErrExecutionReverted` instead of propagating.

### Proof of Concept
1. Deploy a CosmWasm contract that responds to `{"token_info":{}}` with JSON that omits `name`/`symbol`, e.g. `{"decimals":6}` (or with `name` present but as a number).
2. From any EOA, send (or `eth_call`) a transaction to precompile address `0x000000000000000000000000000000000000100b` calling `addCW20Pointer(cw20_address)` with `cw20_address` set to the contract from step 1.
3. Execution reaches [6](#0-5) , where `formattedRes["name"].(string)` panics because the key is absent (nil interface) or of the wrong type, with no recovery in the call chain up to `DynamicGasPrecompile.RunAndCalculateGas`.

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

**File:** precompiles/pointer/pointer.go (L166-228)
```go
func (p PrecompileExecutor) AddCW721(ctx sdk.Context, method *ethabi.Method, caller common.Address, args []interface{}, value *big.Int, evm *vm.EVM, hooks *tracing.Hooks) (ret []byte, remainingGas uint64, err error) {
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
	if err != nil {
		return nil, 0, err
	}

	ret, err = method.Outputs.Pack(contractAddr)
	remainingGas = pcommon.GetRemainingGas(ctx, p.evmKeeper)
	return
}

func (p PrecompileExecutor) AddCW1155(ctx sdk.Context, method *ethabi.Method, caller common.Address, args []interface{}, value *big.Int, evm *vm.EVM, hooks *tracing.Hooks) (ret []byte, remainingGas uint64, err error) {
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
	if err != nil {
		return nil, 0, err
	}

	ret, err = method.Outputs.Pack(contractAddr)
	remainingGas = pcommon.GetRemainingGas(ctx, p.evmKeeper)
	return
}
```

**File:** precompiles/common/precompiles.go (L156-215)
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

**File:** precompiles/solo/legacy/v640/solo.go (L90-99)
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
