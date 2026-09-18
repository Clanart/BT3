Found a valid analog: an unchecked type assertion in the wasmd-CW-pointer bridge that panics on attacker-controlled contract responses, propagating up through `RunAndCalculateGas` with no generic panic recovery — the same "unvalidated external input drives an unrecoverable Go-level fatal path" pattern as the `DrawBoundingBoxes` `CHECK`-fail bug.

### Title
Unrecovered Go panic (unchecked JSON type assertion) in CW pointer precompiles crashes tx execution - (File: `precompiles/pointer/pointer.go`)

### Summary
`AddCW20`, `AddCW721`, and `AddCW1155` in the `pointer` precompile (address `0x...100b`) query an arbitrary, caller-specified CosmWasm contract and then blindly type-assert two fields out of the JSON response as `string`, without checking `ok`. If the target contract returns a `token_info`/`contract_info` response where `name` or `symbol` is missing, `null`, or a non-string JSON type, the assertion panics.

### Finding Description
`AddCW20` (and the analogous `AddCW721`/`AddCW1155`) do: [1](#0-0) 
```go
res, err := p.wasmdKeeper.QuerySmartSafe(ctx, cwAddress, []byte("{\"token_info\":{}}"))
...
formattedRes := map[string]interface{}{}
if err := json.Unmarshal(res, &formattedRes); err != nil {
    return nil, 0, err
}
name := formattedRes["name"].(string)
symbol := formattedRes["symbol"].(string)
```
`formattedRes["name"]` and `formattedRes["symbol"]` come directly from the queried CW contract's JSON response and are asserted to `string` with the single-value form (`x.(string)`), which panics if the key is absent (`nil` interface) or the underlying JSON value is a number/bool/object/array instead of a string — exactly the "user-controlled input drives an unchecked assumption that crashes the process" pattern in the reported `DrawBoundingBoxes` bug (there, `height=0` violated an implicit assumption and hit a `CHECK` abort instead of a graceful `OP_REQUIRES`-style error).

Unlike essentially every other precompile in this codebase (bank, addr, p256, params, distribution, json, wasmd), which wrap their `Execute`/`Run` bodies in `defer recover()` that converts *any* panic into `execution reverted` — a pattern the repo explicitly documents and tests for ("Bad input reverts, and out-of-gas failures never leak a Go panic — a consensus-relevant guard inherited from the legacy suite"): [2](#0-1) 

the `pointer` precompile's `Execute` and its `Add*` methods have **no** `defer/recover` at all: [3](#0-2) 

The call path `DynamicGasPrecompile.RunAndCalculateGas` (shared by all dynamic-gas precompiles including `pointer`) only recovers gas-meter panics inside `chargeDecodeGas`, not panics from the executor's `Execute` call itself: [4](#0-3) [5](#0-4) 
So a panic raised inside `AddCW20`/`AddCW721`/`AddCW1155` is not caught anywhere in the precompile stack and propagates as an unhandled Go panic during EVM execution.

Notably, earlier legacy revisions of this same precompile (e.g. `v552`, `v555`, `v562`, `v575`) contain byte-for-byte identical unchecked assertions in their `AddCW20`/`AddCW721` implementations: [6](#0-5) [7](#0-6) 
showing this has been a longstanding, unfixed gap even as the rest of the precompile suite was hardened against panic-based DoS (see e.g. the dedicated `TestDynamicGasPrecompileRepanicsNonGas` test, which documents that non-gas panics are *intentionally* re-raised rather than swallowed): [8](#0-7) 

### Impact Explanation
Any address can deploy a trivial CosmWasm contract whose `token_info` (or `contract_info`) query handler returns JSON omitting `name`/`symbol`, or returning them as non-string types (e.g. `null`, a number, or an object) — this is fully attacker-controlled and requires no special privilege beyond deploying a CW contract, which is a normal, permissionless action reachable through the `wasmd` precompile or native CW deployment. Any subsequent call (by that attacker or by tricking another EVM user) to `pointer.addCW20Pointer` / `addCW721Pointer` / `addCW1155Pointer` targeting that contract triggers an unrecovered Go panic during `EVMTransaction` execution. Depending on how the surrounding msg-server/ante panic-recovery layers handle this (which convert most application panics into failed tx VM errors), the practical impact ranges from a single-transaction failure to, in the worst case (if the panic occurs at a point not wrapped by the outer `ProcessBlock`/msg-server recover boundary the way other precompile-level panics are), a validator node process crash or a non-deterministic (chain-fork-risk) result, since Go panics recovered at different call depths across different validator implementations/versions are a known source of consensus divergence. This matches the report's severity class (Medium): unauthenticated denial-of-service via a `CHECK`-style hard assumption failure.

### Likelihood Explanation
High likelihood of triggerability: deploying a CosmWasm contract and calling a public EVM precompile method are both fully permissionless, low-cost operations available to any transaction sender. No governance, validator, or privileged access is required — only a public RPC endpoint and gas to submit two transactions (deploy the malicious contract, then call `addCW20Pointer`/`addCW721Pointer`/`addCW1155Pointer` against it).

### Recommendation
Replace the unchecked single-value type assertions with the two-value form and return an explicit error on failure, mirroring the pattern already used elsewhere in the codebase (e.g. `precompiles/json/json.go`'s bounds/type checks):
```go
name, ok := formattedRes["name"].(string)
if !ok {
    return nil, 0, errors.New("cw contract token_info response missing/invalid name field")
}
symbol, ok := formattedRes["symbol"].(string)
if !ok {
    return nil, 0, errors.New("cw contract token_info response missing/invalid symbol field")
}
```
Apply this fix to all four call sites (`AddCW20`, `AddCW721`, `AddCW1155` in `precompiles/pointer/pointer.go`, and equivalent logic in the legacy versioned copies if still reachable). Additionally, consider adding a generic `defer recover()` wrapper to the `pointer` precompile's `Execute`/`Add*` methods (consistent with `bank`, `addr`, `p256`, `distribution`, etc.) as defense-in-depth against any other unforeseen panic paths in this precompile.

### Proof of Concept
1. Deploy a minimal CosmWasm contract whose `QueryMsg::TokenInfo {}` (or `ContractInfo {}`) handler returns `{}` (or `{"name": 123}`) instead of the expected `{"name": "...", "symbol": "..."}`.
2. From an EVM account, call `pointer.addCW20Pointer(contractAddr)` (or `addCW721Pointer`/`addCW1155Pointer`) on address `0x000000000000000000000000000000000000100b` targeting the deployed contract.
3. Execution reaches `formattedRes["name"].(string)` in `precompiles/pointer/pointer.go` with `formattedRes["name"]` equal to `nil` (or a `float64`), causing an unrecovered Go type-assertion panic that is not caught by any `defer/recover` in the `pointer` precompile or in `DynamicGasPrecompile.RunAndCalculateGas`'s gas-only recovery, unlike all sibling precompiles.

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

**File:** integration_test/precompile_tests/README.md (L31-37)
```markdown
  the Cosmos-side truth.
- **Error handling.** Bad input reverts, and out-of-gas failures never leak a
  Go panic — a consensus-relevant guard inherited from the legacy suite. (The
  precise OOG shape differs per precompile: bank/addr executors convert the
  gas-meter panic to `execution reverted`, while a starved staking call runs
  out inside the cosmos store layer and surfaces a location-tagged
  `out of gas` error instead.)
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

**File:** precompiles/common/precompiles.go (L217-247)
```go
// chargeDecodeGas charges the (already-installed) gas meter for decoding the
// calldata, before it is decoded: a length-proportional scan cost that also
// bounds the DecodeGasCost scan, then the string-copy surcharge from
// DecodeGasCost. Its out-of-gas / overflow recovery is deliberately scoped to
// just these charges — a call that cannot afford the decode reverts here, while
// an executor that later exhausts its gas keeps its normal propagating
// out-of-gas semantics. Anything other than a gas-meter panic is re-raised.
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
	scanCost := DefaultGasCost(input, false)
	ctx.GasMeter().ConsumeGas(scanCost, fmt.Sprintf("%s precompile calldata scan", d.name))
	decodeCost, ok := DecodeGasCost(method.Inputs, input)
	if !ok {
		// Calldata is structurally invalid (Unpack would reject it too); reject
		// now, without attempting the decode.
		return fmt.Errorf("invalid calldata encoding for %s", d.name)
	}
	// DecodeGasCost already includes scanCost; charge only the remaining
	// (string-copy) portion so the decode is priced exactly once.
	ctx.GasMeter().ConsumeGas(decodeCost-scanCost, fmt.Sprintf("%s precompile calldata decode", d.name))
	return nil
}
```

**File:** precompiles/pointer/legacy/v552/pointer.go (L216-221)
```go
	formattedRes := map[string]interface{}{}
	if err := json.Unmarshal(res, &formattedRes); err != nil {
		return nil, 0, err
	}
	name := formattedRes["name"].(string)
	symbol := formattedRes["symbol"].(string)
```

**File:** precompiles/pointer/legacy/v562/pointer.go (L197-198)
```go
	name := formattedRes["name"].(string)
	symbol := formattedRes["symbol"].(string)
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
