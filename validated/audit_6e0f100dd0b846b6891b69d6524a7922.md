Found a concrete unhandled-panic analog reachable from a normal EVM transaction.

### Title
Unrecovered type-assertion panic in the pointer precompile's CW20/CW721/CW1155 registration path halts transaction processing - ([File: precompiles/pointer/pointer.go])

### Summary
The Sei pointer precompile's `AddCW20`, `AddCW721`, and `AddCW1155` handlers query a CosmWasm contract for its `token_info`/`contract_info` and blindly type-assert the JSON response fields to `string` without checking the second `ok` return value. Any CosmWasm contract deployer can make a contract return a non-string (or missing) `name`/`symbol` field, causing a Go runtime panic when an unprivileged EVM caller invokes the pointer precompile against that contract. Unlike other precompile paths in this codebase (e.g. `precompiles/addr/addr.go`), the `Precompile.Run` wrapper contains no `recover()`, so nothing catches this panic at the precompile layer.

### Finding Description
`PrecompileExecutor.AddCW20` (and the CW721/CW1155 equivalents) does: [1](#0-0) 
```
res, err := p.wasmdKeeper.QuerySmartSafe(ctx, cwAddress, []byte("{\"token_info\":{}}"))
...
formattedRes := map[string]interface{}{}
if err := json.Unmarshal(res, &formattedRes); err != nil { ... }
name := formattedRes["name"].(string)
symbol := formattedRes["symbol"].(string)
```
`formattedRes["name"]` and `formattedRes["symbol"]` come straight from the queried CosmWasm contract's own `token_info`/`contract_info` query response — entirely attacker-controlled data — and are asserted to `string` without the `ok` check. If the contract returns `"name": 123` (a number), a nested object, or omits the field, the map lookup yields a non-string (or nil) `interface{}`, and the type assertion panics with `interface conversion: interface {} is <T>, not string`.

The generic precompile dispatcher `Precompile.Run` that wraps `Execute` has no panic recovery: [2](#0-1) 
This is unlike the `addr` precompile, which explicitly wraps its `Execute` with `defer recover()` to convert panics into reverts: [3](#0-2) 
Neither `PrecompileExecutor.AddCW20/AddCW721/AddCW1155` in `pointer.go`, nor its callers, install any such guard, so the panic propagates up through `vm.RunPrecompiledContract` and the EVM execution stack.

This is structurally the same bug class as the Mattermost boards issue: unvalidated externally-supplied structured content (a "link"/JSON payload) is deserialized and consumed without a defensive check, producing a crash instead of a graceful error.

### Impact Explanation
The panic surfaces during `msgServer.EVMTransaction` state transition. That function does have a top-level `recover()` in `x/evm/keeper/msg_server.go` (lines 80–89) — but it explicitly **re-panics** any recovered value (`panic(pe)`), rather than converting it to an error. That re-panic then propagates to `ProcessBlock`'s recover in `app/app.go` (lines 1764–1781), which converts it into an `error` returned from `ProcessBlock` — this is a deterministic, block-processing-level failure hit identically by every validator executing the same block, since the CW20/CW721/CW1155 contract and the calling pointer-registration tx are both committed on-chain and replayed by all nodes. Depending on how the caller of `ProcessBlock` treats a non-nil error return (typically fatal in Cosmos SDK ABCI `FinalizeBlock` flows), this can produce a chain-wide halt or repeated block-processing failure across the network rather than an isolated node crash — a "crash a channel" analog scaled to "crash the block-processing pipeline" for every validator, since the fault is deterministic and reproducible by any node that processes the block. Additionally, the same code path is reachable via `eth_call`/`eth_estimateGas` on any public RPC node (since it doesn't require the tx to be committed), where the RPC/tracing precompile-invocation stack similarly lacks its own recover for this precompile, risking a crash of default-configuration RPC nodes as well.

### Likelihood Explanation
Trivial to trigger: an unprivileged CosmWasm contract deployer stores and instantiates a minimal CW20/CW721/CW1155-labeled contract whose `token_info`/`contract_info` query handler returns `{"name": 1, "symbol": "X"}` (or omits `name`/`symbol`), then any account calls the pointer precompile's `addCW20`/`addCW721`/`addCW1155` method against that contract address via a normal EVM transaction or `eth_call`. No special privileges, races, or governance actions are required.

### Recommendation
In `precompiles/pointer/pointer.go` (and its `legacy/*` copies), replace the unchecked type assertions with checked ones, returning an error (not panicking) when the fields are missing or the wrong type:
```go
name, ok := formattedRes["name"].(string)
if !ok {
    return nil, 0, errors.New("token_info response 'name' is not a string")
}
symbol, ok := formattedRes["symbol"].(string)
if !ok {
    return nil, 0, errors.New("token_info response 'symbol' is not a string")
}
```
Apply the same fix to `AddCW721`/`AddCW1155` (and the analogous `contract_info` fields). As defense in depth, add a `recover()` guard in `Precompile.Run` / `DynamicGasPrecompile.RunAndCalculateGas` (mirroring the pattern already used in `precompiles/addr/addr.go`) so that any future unchecked-assertion-style bug in a precompile executor degrades to `execution reverted` instead of an unrecovered panic that can propagate to block processing.

### Proof of Concept
1. Deploy (via `MsgStoreCode`/`MsgInstantiateContract`) a CosmWasm contract labeled as CW20 whose query handler responds to `{"token_info":{}}` with `{"name": 12345, "symbol": "X", "decimals": 6, "total_supply": "0"}` (a numeric `name` instead of a string), or with the `name` key entirely absent.
2. From any EVM account, call the pointer precompile at the well-known pointer precompile address, method `addCW20(cw20Address)` (as exercised in `precompiles/pointer/pointer.go` `AddCW20`), passing the malicious contract's bech32 address as `cwAddr`.
3. Observe: `formattedRes["name"].(string)` panics with `interface conversion: interface {} is float64, not string` (or `interface {} is nil, not string` if the key is absent), because there is no `ok`-checked assertion and no `recover()` in `Precompile.Run`.
4. The panic propagates through `msgServer.EVMTransaction`'s `recover()`/re-`panic()` in `x/evm/keeper/msg_server.go:82-89` to `App.ProcessBlock`'s recover in `app/app.go:1764-1781`, turning block processing into a failure for every node that processes this transaction — reproducible deterministically by re-submitting the same tx/contract pair.

### Citations

**File:** precompiles/pointer/pointer.go (L134-157)
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
