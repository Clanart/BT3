Based on the analysis, `HandlePrecompileError` and the `defer` blocks in `RunAndCalculateGas`/`Run` only handle the `err` return value — they do **not** call `recover()`. A Go panic (such as a failed type assertion `formattedRes["name"].(string)`) inside `Execute`/`AddCW20`/`AddCW721` is therefore not caught at this layer, and unlike the confirmed `TestDynamicGasPrecompileRepanicsNonGas` test shows, non-gas panics are expected to propagate up out of the precompile [1](#0-0) , [2](#0-1) .

### Title
Pointer-creation precompiles panic on malformed CW20/CW721 `token_info`/`contract_info` responses due to unchecked type assertions - ([File: precompiles/pointer/pointer.go])

### Summary
`AddCW20`/`AddCW721` (and their many legacy versions) query an arbitrary, caller-supplied CosmWasm contract address for `token_info`/`contract_info` and then extract `name`/`symbol` fields with unchecked Go type assertions (`formattedRes["name"].(string)`). Any account can deploy a CW20/CW721-like contract whose query response contains a non-string `name`/`symbol` field (e.g., a JSON number, object, or omitted field), then call the `addCW20Pointer`/`addCW721Pointer` precompile method pointing at it, triggering a runtime panic during EVM transaction execution.

### Finding Description
`p.wasmdKeeper.QuerySmart(ctx, cwAddress, []byte("{\"token_info\":{}}"))` returns the raw JSON of any contract answering that query — the contract's code and response format are entirely attacker-controlled, since anyone can `MsgInstantiateContract` a CW20-shaped contract with custom `query` logic. The precompile then does:
```go
formattedRes := map[string]interface{}{}
json.Unmarshal(res, &formattedRes)
name := formattedRes["name"].(string)
symbol := formattedRes["symbol"].(string)
``` [3](#0-2) , [4](#0-3) 

If the contract's `token_info`/`contract_info` response has `name` missing (`nil` value → assertion `nil.(string)` panics) or a non-string type (e.g. `"name": 123`), this direct (non-`ok`-form) type assertion panics with `interface conversion: interface {} is float64, not string` (or nil-conversion panic). This is present identically across essentially every pointer-precompile version in the repo (`v552`, `v555`, `v562`, `v575`, `v580`, `v600`, `v605`, `v606`, `v610`, `v614`, `v620`, `v630`, `v640`, `v65`, `v66`, `v67`, current `pointer.go`) [5](#0-4) .

Crucially, the calling framework (`Run` / `RunAndCalculateGas` in `precompiles/common/precompiles.go`) only wraps the returned `error` value in a `defer`; it does not `recover()` from panics [2](#0-1) . A dedicated test, `TestDynamicGasPrecompileRepanicsNonGas`, explicitly documents and asserts that non-gas panics from an executor are **not** masked and must propagate rather than be converted to a revert [6](#0-5) . This means a panic raised inside `AddCW20`/`AddCW721` propagates out of the precompile call, out of the EVM interpreter, and up through the ante/deliver-tx pipeline for the transaction, mirroring the NEAR report's root cause: user-controlled data used to construct/derive state without validating its shape, leading to an unhandled panic in core transaction-processing logic that any unprivileged caller can trigger.

This closely mirrors the reported bug class: unvalidated externally-influenced data (there, an account ID string; here, a query-response JSON field) is consumed by a code path (there, `Promise::new().create_account()`; here, a Go type assertion during contract-pointer creation) without validation, and the resulting panic disrupts the "main functionality" (there, vote conclusion; here, EVM transaction processing / pointer creation) for every subsequent attempt.

### Impact Explanation
A panic escaping into `baseapp`'s deliver-tx path during a transaction is generally caught by the outer consensus-level panic-recovery machinery (`sei-cosmos/baseapp/recovery.go`) per-transaction, so a single malicious pointer-creation call would likely fail that one transaction rather than permanently crash the node — the critical distinction from the NEAR case (which had no baseapp-level recovery at all and *no way to remove the stuck value*, permanently freezing future votes). Whether this specific pointer-creation panic causes a permanent DoS (stuck state preventing any future pointer creation for that pointee, analogous to the NEAR bug) depends on whether `evmKeeper.SetERC20CW20Pointer`/`SetERC721CW721Pointer` state is partially written before the panic point and never rolled back consistently across all execution paths — this could not be fully confirmed from the available code paths in this session.

### Likelihood Explanation
Very high: any account can permissionlessly instantiate a CW20/CW721-shaped contract with a `token_info`/`contract_info` query handler returning a non-string `name`/`symbol`, then call `addCW20Pointer`/`addCW721Pointer` via a normal EVM transaction — no privilege or special network position required.

### Recommendation
Replace all unchecked type assertions in `AddCW20`/`AddCW721` (and equivalent CW1155/native pointer paths) across all pointer-precompile versions with the two-value `value, ok := formattedRes["name"].(string)` form, returning a proper `error` (not panicking) when `ok` is false or when required fields are missing, consistent with how the rest of the module already returns `err` on malformed input.

### Proof of Concept
1. Deploy a CosmWasm contract via `MsgInstantiateContract` whose `query` entry point responds to `{"token_info":{}}` with `{"name": 12345, "symbol": "X", "decimals": 0, "total_supply": "0"}` (a JSON number instead of a string for `name`).
2. From an EVM account, call the `pointer` precompile's `addCW20Pointer(contractAddr)` (selector `AddCW20PointerID`) targeting that contract's Sei address.
3. `AddCW20` unmarshals the query response into `map[string]interface{}`; `formattedRes["name"]` is `float64(12345)`; the assertion `formattedRes["name"].(string)` panics with `interface conversion: interface {} is float64, not string`, which is not recovered by `Run`/`RunAndCalculateGas`, propagating the panic out of contract execution for that transaction. [7](#0-6)

### Citations

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

**File:** precompiles/common/precompiles.go (L64-99)
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

func HandlePrecompileError(err error, evm *vm.EVM, operation string) {
	if err != nil {
		if sdb := state.GetDBImpl(evm.StateDB); sdb != nil {
			sdb.SetPrecompileError(err)
		}
		metrics.IncrementErrorMetrics(operation, err)
	}
}
```

**File:** precompiles/pointer/legacy/v562/pointer.go (L196-249)
```go
	}
	name := formattedRes["name"].(string)
	symbol := formattedRes["symbol"].(string)
	constructorArguments := []interface{}{
		cwAddr, name, symbol,
	}

	packedArgs, err := cw20.GetParsedABI().Pack("", constructorArguments...)
	if err != nil {
		panic(err)
	}
	bin := append(cw20.GetBin(), packedArgs...)
	if value == nil {
		value = utils.Big0
	}
	ret, contractAddr, remainingGas, err := evm.Create(caller, bin, suppliedGas, uint256.MustFromBig(value))
	if err != nil {
		return
	}
	err = p.evmKeeper.SetERC20CW20Pointer(ctx, cwAddr, contractAddr)
	if err != nil {
		return
	}

	ctx.EventManager().EmitEvent(sdk.NewEvent(
		types.EventTypePointerRegistered, sdk.NewAttribute(types.AttributeKeyPointerType, "cw20"),
		sdk.NewAttribute(types.AttributeKeyPointerAddress, contractAddr.Hex()), sdk.NewAttribute(types.AttributeKeyPointee, cwAddr),
		sdk.NewAttribute(types.AttributeKeyPointerVersion, fmt.Sprintf("%d", cw20.CurrentVersion(ctx)))))
	ret, err = method.Outputs.Pack(contractAddr)
	return
}

func (p PrecompileExecutor) AddCW721(ctx sdk.Context, method *ethabi.Method, caller common.Address, args []interface{}, value *big.Int, evm *vm.EVM, suppliedGas uint64, hooks *tracing.Hooks) (ret []byte, remainingGas uint64, err error) {
	if err := pcommon.ValidateNonPayable(value); err != nil {
		return nil, 0, err
	}
	if err := pcommon.ValidateArgsLength(args, 1); err != nil {
		return nil, 0, err
	}
	cwAddr := args[0].(string)
	existingAddr, existingVersion, exists := p.evmKeeper.GetERC721CW721Pointer(ctx, cwAddr)
	if exists && existingVersion >= 4 {
		return nil, 0, fmt.Errorf("pointer at %s with version %d exists when trying to set pointer for version %d", existingAddr.Hex(), existingVersion, cw721.CurrentVersion)
	}
	cwAddress, err := sdk.AccAddressFromBech32(cwAddr)
	if err != nil {
		return nil, 0, err
	}
	res, err := p.wasmdKeeper.QuerySmart(ctx, cwAddress, []byte("{\"contract_info\":{}}"))
	if err != nil {
		return nil, 0, err
	}
	formattedRes := map[string]interface{}{}
	if err := json.Unmarshal(res, &formattedRes); err != nil {
```

**File:** precompiles/pointer/legacy/v575/pointer.go (L140-149)
```go
	res, err := p.wasmdKeeper.QuerySmart(ctx, cwAddress, []byte("{\"token_info\":{}}"))
	if err != nil {
		return nil, 0, err
	}
	formattedRes := map[string]interface{}{}
	if err := json.Unmarshal(res, &formattedRes); err != nil {
		return nil, 0, err
	}
	name := formattedRes["name"].(string)
	symbol := formattedRes["symbol"].(string)
```

**File:** precompiles/pointer/pointer.go (L132-197)
```go
}

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

```
