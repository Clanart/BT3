Confirmed: `RunAndCalculateGas` in `precompiles/common/precompiles.go` has no top-level `recover()` around `d.executor.Execute(...)` — only `chargeDecodeGas` has a scoped recover (and it re-panics anything other than an out-of-gas error). So a Go runtime panic raised inside `AddCW20`/`AddCW721`/`AddCW1155` propagates up uncaught through the precompile dispatch path.

### Title
Unchecked type assertion on attacker-controlled CosmWasm query response causes EVM precompile panic / node crash - (File: precompiles/pointer/pointer.go)

### Summary
The `pointer` precompile's `AddCW20`, `AddCW721`, and `AddCW1155` handlers query an arbitrary, caller-specified CosmWasm contract for `token_info`/`contract_info`, then blindly type-assert the returned JSON `name`/`symbol` fields as Go strings without validating their presence or type, mirroring the CVE-2024-47076 pattern of using unsanitized attributes from an untrusted responder to build downstream artifacts.

### Finding Description
`AddCW20` (and the analogous `AddCW721`/`AddCW1155`) resolves `cwAddr := args[0].(string)` from caller-supplied EVM calldata, queries that contract via `p.wasmdKeeper.QuerySmartSafe(ctx, cwAddress, []byte("{\"token_info\":{}}"))`, unmarshals the raw JSON response into a `map[string]interface{}`, and then does: [1](#0-0) 
```go
formattedRes := map[string]interface{}{}
if err := json.Unmarshal(res, &formattedRes); err != nil {
    return nil, 0, err
}
name := formattedRes["name"].(string)
symbol := formattedRes["symbol"].(string)
```
This is an unchecked (single-value) Go type assertion. Since `cwAddr` is fully attacker-controlled (any address the caller passes, including a contract the caller itself deployed via the wasmd precompile or a native `MsgInstantiateContract`), an attacker can deploy a CosmWasm contract whose `token_info`/`contract_info` query handler returns a `name` or `symbol` field that is `null`, a number, an object, or omits the field entirely. `formattedRes["name"]` is then either `nil` (interface holding no concrete type) or a non-`string` type (e.g. `float64`), and `.(string)` panics at runtime with "interface conversion: interface {} is nil, not string" or similar.

Unlike the CVE's data-flow (IPP server response consumed unsanitized by a PPD generator), here the untrusted responder is a CosmWasm contract the *same transaction sender* controls, and the consumer is the Sei EVM precompile dispatch path. The same unchecked-assertion pattern exists across every legacy pointer precompile version (v552, v555, v562, v580, v605, v606, v614, v620, v630, v640, v65, v66, v67) confirming it is a longstanding, unfixed pattern rather than a one-off.

The dispatch path that invokes this code has no panic recovery: `DynamicGasPrecompile.RunAndCalculateGas` calls `d.executor.Execute(...)` directly with no `defer recover()` around it — the only `recover()` in that file is scoped narrowly to `chargeDecodeGas` and explicitly re-panics anything that isn't a gas-related error: [2](#0-1) [3](#0-2) 

A Go panic that escapes the goroutine executing block/transaction processing (i.e., not caught by any deferred `recover()` in the call stack up to the ABCI/tendermint boundary) will crash the node process.

### Impact Explanation
If the panic propagates uncaught to the top of the goroutine handling transaction execution, it crashes the node. Every full node executing this transaction (validators included, since this runs during deterministic state transition, not just RPC-only nodes) would panic identically, causing a chain-wide halt rather than an isolated RPC crash — this is more severe than a single-node DoS. This satisfies the "crash of default-configuration RPC nodes" / "validator halt" acceptance bar, contingent on no outer `recover()` existing in the ABCI message-handling stack (Cosmos SDK's `baseapp` typically does wrap `runTx` in a recover that converts panics into failed transactions, which would downgrade impact to "transaction always fails / gas griefing" rather than a full crash — this uncertainty could not be fully resolved without tracing baseapp's msg-service router recover behavior, which was outside the indexed/available context).

### Likelihood Explanation
High reachability: any transaction sender can (1) instantiate a trivial CosmWasm contract with a crafted `token_info`/`contract_info` query response, then (2) call `addCW20Pointer`/`addCW721Pointer`/`addCW1155Pointer` on the pointer precompile (`0x000000000000000000000000000000000000100b`) pointing at that contract. No special privilege, association, or funds are required beyond normal contract deployment and one EVM transaction.

### Recommendation
Replace unchecked type assertions with the checked two-value form (`name, ok := formattedRes["name"].(string); if !ok { return nil, 0, fmt.Errorf(...) }`) in `AddCW20`, `AddCW721`, and `AddCW1155` (and their legacy-version counterparts), returning a normal precompile error instead of panicking. Additionally, wrap `DynamicGasPrecompile.RunAndCalculateGas`'s call to `d.executor.Execute` in a deferred `recover()` that converts any panic into `vm.ErrExecutionReverted`, consistent with defense-in-depth already present elsewhere in the codebase (e.g., the `execute`/`query` methods in `precompiles/wasmd/wasmd.go` wrap their bodies in `recover()`).

### Proof of Concept
1. Attacker deploys a CosmWasm contract `C` (via `wasmd` precompile `instantiate` or a native `MsgInstantiateContract`) whose `QueryMsg::TokenInfo {}` / `QueryMsg::ContractInfo {}` handler returns `{"name": null, "symbol": null, ...}` (or simply omits `name`/`symbol`, or returns them as integers).
2. Attacker sends an EVM transaction calling `addCW20Pointer(C)` (or `addCW721Pointer`/`addCW1155Pointer`) on precompile address `0x...100b`.
3. Inside `AddCW20`, `p.wasmdKeeper.QuerySmartSafe` succeeds and returns the crafted JSON; `json.Unmarshal` succeeds; `formattedRes["name"].(string)` panics because the value is not a string. [4](#0-3) 
4. Because no `recover()` exists at the `RunAndCalculateGas` dispatch layer, the panic propagates out of the precompile call during transaction execution.

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

**File:** precompiles/common/precompiles.go (L224-234)
```go
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
```
