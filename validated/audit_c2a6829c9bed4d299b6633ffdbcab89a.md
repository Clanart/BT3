### Title
Unchecked type assertion on attacker-controlled CosmWasm query response causes panic in the `pointer` precompile's `AddCW20`/`AddCW721`/`AddCW1155` handlers - (File: precompiles/pointer/pointer.go)

### Summary
The Ollama report is a "missing return-value validation before dereference" bug class: attacker-controlled data is passed to a function whose failure mode (NULL/invalid) is never checked, and the unchecked value is then used, crashing the process. The closest reachable analog in sei-chain is in the `pointer` precompile's CW20/CW721/CW1155 pointer-creation methods, which blindly type-assert fields out of a JSON map built from a `QuerySmart`/`QuerySmartSafe` response to an attacker-chosen CosmWasm contract, without checking that the assertion succeeds.

### Finding Description
`AddCW20`, `AddCW721`, and `AddCW1155` in `precompiles/pointer/pointer.go` accept a bech32 CosmWasm contract address supplied directly by the EVM caller as an ABI argument: [1](#0-0) 

Each queries the target contract for `token_info` / `contract_info`, unmarshals the response into a generic `map[string]interface{}`, and then performs unchecked type assertions:
```
formattedRes := map[string]interface{}{}
json.Unmarshal(res, &formattedRes)
name := formattedRes["name"].(string)
symbol := formattedRes["symbol"].(string)
```
This pattern recurs in every version of the precompile (current and all `legacy/vXXX` copies), e.g.: [2](#0-1) [3](#0-2) 

There is no `ok` check on `formattedRes["name"].(string)`. `cwAddress` can point to any contract the caller deploys and controls (a "CW20/CW721-labeled" contract is not verified to implement the real interface before this call — the precompile only checks it responds to the fixed query message, not that the response shape matches). If the target contract's `token_info`/`contract_info` query response omits `name`/`symbol`, returns `null`, or returns a non-string value (e.g. a number), the Go runtime panics with an "interface conversion" error — the same root-cause shape as the CVE: attacker-controlled data is fed into a code path that assumes success/valid-type without validating it first.

### Impact Explanation
The direct consequence is a Go panic inside `PrecompileExecutor.Execute`, invoked from `DynamicGasPrecompile.RunAndCalculateGas` — `precompiles/common/precompiles.go` — which has no `recover()` around the executor call itself: [4](#0-3) 
Whether this manifests as a full node/RPC crash (matching the CVE's "crash the runner process") versus a merely-failed transaction depends on whether the invoking layer (baseapp's tx-execution panic recovery, or the EVM RPC's `eth_call`/`eth_estimateGas`/tracing panic-recovery wrappers seen throughout `evmrpc/*.go`) wraps this call path with `recover()`. I was not able to conclusively trace whether every call site (in particular `eth_call`/simulation contexts) sits behind a `recover()` before reaching this precompile executor, so I cannot confirm this reaches the "crash of default-configuration RPC nodes" bar required by the validation rules without further tracing of the EVM `Call`/`StaticCall`/`vm.RunPrecompiledContract` dispatch path and baseapp's `runTx` recovery middleware, which were outside what I could fully verify in the time available.

### Likelihood Explanation
Reaching the code is trivial and fully permissionless: any account can deploy a CosmWasm contract of their choosing and call the EVM `pointer` precompile's `addCW20Pointer`/`addCW721Pointer`/`addCW1155Pointer` with that contract's address — no privileged role required.

### Recommendation
Replace the unchecked type assertions with the two-value form and return an error (not a panic) when the response shape is unexpected, e.g.:
```go
name, ok := formattedRes["name"].(string)
if !ok {
    return nil, 0, fmt.Errorf("contract %s did not return a string name", cwAddr)
}
symbol, ok := formattedRes["symbol"].(string)
if !ok {
    return nil, 0, fmt.Errorf("contract %s did not return a string symbol", cwAddr)
}
```
Apply the same fix uniformly to all current and legacy (`legacy/vXXX`) copies of `AddCW20`, `AddCW721`, and `AddCW1155` in `precompiles/pointer/*`.

### Proof of Concept
1. Deploy a minimal CosmWasm contract whose `{"token_info":{}}` (or `{"contract_info":{}}`) query handler returns valid JSON where `name` (or `symbol`) is absent, `null`, or a non-string type (e.g. `{"name": null, "symbol": 123, "decimals": 6, "total_supply": "0"}`).
2. From any EVM account, call the `pointer` precompile (`0x000000000000000000000000000000000000100b`) method `addCW20Pointer(contractAddress)` with the deployed contract's bech32 address.
3. Execution reaches `formattedRes["name"].(string)` in `precompiles/pointer/pointer.go`, which panics because the interface value is `nil`/non-string instead of `string`.

I was unable to verify, within the scope of this analysis, whether this panic is guaranteed to be recovered by an outer handler in every reachable call path (tx execution vs. `eth_call`/gas estimation/tracing), which is necessary to confirm actual node/RPC-crash impact versus a merely-reverted transaction.

### Citations

**File:** precompiles/pointer/pointer.go (L134-163)
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
```

**File:** precompiles/pointer/legacy/v552/pointer.go (L212-221)
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

**File:** precompiles/pointer/legacy/v66/pointer.go (L148-157)
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
```

**File:** precompiles/common/precompiles.go (L156-217)
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

// chargeDecodeGas charges the (already-installed) gas meter for decoding the
```
