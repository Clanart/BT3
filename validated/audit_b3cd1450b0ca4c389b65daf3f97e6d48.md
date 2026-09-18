Confirmed: `precompiles/pointer/pointer.go`'s `Execute` method has no `recover()` wrapper, unlike sibling precompiles (`addr`, `oracle`, `slashing`, `solo`) that explicitly guard against gas-meter and other panics with `defer func() { if r := recover(); ... }()`. Both `AddCW20`, `AddCW721`, and `AddCW1155` do unchecked type assertions `formattedRes["name"].(string)` / `formattedRes["symbol"].(string)` on a `map[string]interface{}` populated straight from `json.Unmarshal` of an attacker-controlled CW contract's query response. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) 

I need to be direct about the residual uncertainty: whether an unguarded panic in a precompile actually crashes the validator process, or is instead recovered somewhere further up the call stack (e.g., in the go-ethereum EVM interpreter's opcode-call recovery, or in the Cosmos SDK's baseapp `runTx`), could not be fully confirmed from what I could inspect. The only recover I could locate in `precompiles/common/precompiles.go`'s `Run`/`RunAndCalculateGas` wrappers only converts an `err` value into `vm.ErrExecutionReverted` — it does **not** call `recover()`, so it does not catch panics. [5](#0-4) [6](#0-5) 

If no upstream layer recovers this panic (which I could not fully verify given index limits on `baseapp`/go-ethereum interpreter code), a single transaction calling `addCW20Pointer`/`addCW721Pointer`/`addCW1155Pointer` against a contract that returns non-string `name`/`symbol` JSON fields could crash transaction processing on every validator that includes the tx, which would be a chain halt (well beyond the 2.5-second threshold) rather than a mere per-request DoS — this maps to the CVE-2021-1095 bug class ("dereferencing an untrusted pointer/value from an external caller causes denial of service in the privileged execution layer") but reachable through the pointer precompile's untyped JSON handling rather than a literal kernel pointer.

### Title
Unrecovered panic in Pointer precompile from untrusted CW20/CW721/CW1155 query response type assertion - (File: precompiles/pointer/pointer.go)

### Summary
The `pointer` precompile's `AddCW20`, `AddCW721`, and `AddCW1155` handlers call `QuerySmartSafe` against an attacker-supplied CW contract address and blindly type-assert `formattedRes["name"].(string)` and `formattedRes["symbol"].(string)` on the resulting `map[string]interface{}`, with no `ok` check and no `recover()` wrapper in `Execute` (unlike the `addr`, `oracle`, `slashing`, and `solo` precompiles which all defend against panics).

### Finding Description
`Execute` in `precompiles/pointer/pointer.go` dispatches to `AddCW20`/`AddCW721`/`AddCW1155` without any panic recovery. [1](#0-0)  Each handler queries an attacker-chosen CW contract (`token_info`/`contract_info`), unmarshals the JSON reply into `map[string]interface{}`, and performs unchecked type assertions on the `name`/`symbol` fields. [7](#0-6)  A malicious contract can return `name`/`symbol` as a non-string JSON value (number, bool, object, array, or omit the field entirely, yielding `nil`), which causes `formattedRes["name"].(string)` to panic with an "interface conversion" runtime error. All other in-tree precompiles that decode arbitrary/queried data explicitly wrap `Execute` in `defer func(){ recover() }` to convert such panics into a returned `err`. [8](#0-7)  The pointer precompile omits this safeguard entirely.

### Impact Explanation
If this panic is not caught anywhere further up the call stack (a point I could not fully verify given index coverage limits on the go-ethereum interpreter/baseapp code paths embedded in this repo), any unprivileged account can trigger it by deploying a trivial CW20/CW721/CW1155-like contract that returns malformed `name`/`symbol` fields and then calling `addCW20Pointer`/`addCW721Pointer`/`addCW1155Pointer` from an EVM transaction. Since this executes during ordinary transaction processing (not a query-only path), a panic here would propagate through block execution on every validator processing the same transaction, potentially producing a chain-wide halt — a far more severe outcome than the CVE's single-machine DoS.

### Likelihood Explanation
Likelihood is high for reaching the code path: no privilege is required, deploying a CW20/CW721/CW1155-shaped contract with a crafted `token_info`/`contract_info` handler and calling the pointer precompile is straightforward on any public devnet/mainnet. What remains uncertain (and lowers confidence) is whether an outer layer (e.g. the Cosmos SDK `baseapp.runTx` panic recovery, or the go-ethereum EVM's own defer/recover around precompiled-contract calls) already converts this panic into a graceful tx failure — if so, this would only be a per-transaction revert, not a node crash, and would fall outside the required "crash of default-configuration RPC nodes" / "validator halt" impact bar. I could not fully confirm this due to code not returned by the index.

### Recommendation
Add explicit validation for the decoded query response before use — check `ok` on both type assertions (`name, ok := formattedRes["name"].(string)`) and return a normal `error` for malformed responses, mirroring the pattern already used in `precompiles/addr/addr.go`, `precompiles/oracle/oracle.go`, and `precompiles/solo/solo.go`. Additionally, wrap `PrecompileExecutor.Execute` in `precompiles/pointer/pointer.go` (and its `legacy/*` variants) with the same `defer func() { if r := recover(); r != nil { err = fmt.Errorf(...) } }()` pattern used by sibling precompiles, so any future unguarded panic degrades to a reverted transaction instead of a node crash.

### Proof of Concept
1. Deploy a CosmWasm contract whose `{"token_info":{}}` (or `{"contract_info":{}}`) query handler returns `{"name": 12345, "symbol": true}` (or omits `name`/`symbol` altogether) instead of strings.
2. From any EVM account, call the pointer precompile at `0x000000000000000000000000000000000000100b` (`PointerAddress`) [9](#0-8)  with `addCW20Pointer(<bech32 of the malicious contract>)`.
3. `AddCW20` unmarshals the response and executes `formattedRes["name"].(string)`, which panics because the underlying value is not a string. [10](#0-9) 
4. Because `Execute` has no `recover()`, the panic propagates out of the precompile call during normal transaction execution instead of being converted to a reverted-tx `error`.

### Citations

**File:** precompiles/pointer/pointer.go (L29-29)
```go
const PointerAddress = "0x000000000000000000000000000000000000100b"
```

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

**File:** precompiles/common/precompiles.go (L64-90)
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
