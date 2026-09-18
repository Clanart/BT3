Confirmed: the `pointer` precompile is registered as a `DynamicGasPrecompile` via `pcommon.NewDynamicGasPrecompile` in `precompiles/pointer/pointer.go:69`, whose `RunAndCalculateGas` (`precompiles/common/precompiles.go:156-217`) only recovers **gas-meter panics** inside `chargeDecodeGas`; any panic raised by `d.executor.Execute(...)` (line 208) propagates unrecovered up through the EVM call stack — unlike the `addr` precompile, which explicitly wraps `Execute` in a `recover()` that downgrades panics to `"execution reverted"` (`precompiles/addr/legacy/v66/addr.go:96-101`). The pointer precompile has no such guard.

### Title
Unhandled type-assertion panic in `AddCW20`/`AddCW721`/`AddCW1155` pointer precompile crashes node on malicious CW20/CW721/CW1155 metadata - (File: `precompiles/pointer/pointer.go`)

### Summary
`PrecompileExecutor.AddCW20` (and the equivalent `AddCW721`/`AddCW1155` handlers) call the target CosmWasm contract's `token_info`/similar query, JSON-unmarshal the response into `map[string]interface{}{}`, and then perform **unchecked Go type assertions** — `formattedRes["name"].(string)` and `formattedRes["symbol"].(string)` — on attacker-controlled data returned by an attacker-deployed CosmWasm contract.

### Finding Description
Any account can deploy a CosmWasm contract that implements a `token_info` query returning a `name` or `symbol` field that is not a JSON string (e.g. a number, bool, array, object, or omitted key so the map value is `nil`). Since `formattedRes["name"]` is typed `interface{}`, a `.(string)` assertion without the two-value `, ok` form panics with `interface conversion` when the underlying type does not match: [1](#0-0) 

This code path is reached via the public `addCW20Pointer` EVM method on the pointer precompile at `0x000000000000000000000000000000000000100b`, callable by any EVM transaction sender: [2](#0-1) 

The pointer precompile is registered as a `DynamicGasPrecompile`: [3](#0-2) 

`RunAndCalculateGas` only recovers panics from the gas-meter charge in `chargeDecodeGas` — any panic raised inside `d.executor.Execute(...)` (which calls into `AddCW20`) is *not* caught here: [4](#0-3) 

Contrast this with the `addr` precompile, which deliberately wraps its `Execute` dispatch in its own `recover()` to convert any panic (including gas-meter panics) into a plain EVM revert, precisely to avoid this class of bug: [5](#0-4) 

Whether an unrecovered panic here ultimately crashes the validator process depends on whether an outer layer (e.g. `msgServer.EVMTransaction`'s deferred recover in `x/evm/keeper/msg_server.go:80-89`, or `runTx`'s recovery middleware in `sei-cosmos/baseapp/baseapp.go:904-915`) catches it first. Both of those layers do have generic `recover()` handlers that convert panics into `ErrPanic` ABCI results rather than allowing the process to crash, so this bug is very likely reduced to a **DoS at the transaction level** (that tx and any dependent txs fail / are marked `ErrPanic`) rather than a full node crash — the same graceful-degradation pattern documented and tested elsewhere in the codebase (e.g. `x/evm/keeper/msg_server.go:80-89`, `TestGigaOCC_PanicRecovery` in `giga/tests/giga_test.go:2092-2113`). I was not able to fully verify whether every call path that can reach `AddCW20`/`AddCW721`/`AddCW1155` (including the Giga executor's synchronous path in `app/app.go`, which does have its own panic recovery, and the OCC scheduler paths) is guaranteed to have an intervening `recover()` in all execution modes, so a residual risk that some code path lacks a top-level recover (leading to an actual process crash / consensus halt) cannot be ruled out without deeper tracing of the Giga custom-precompile fail-fast path (`gigaprecompiles.AllCustomPrecompilesFailFast` in `app/app.go:1944`), since the giga executor explicitly documents that registered custom precompiles force sequential (non-OCC) execution and that panics are supposed to be caught per-tx.

### Impact Explanation
If the panic escapes all recovery layers on any single node (or on the specific execution path used by a public RPC's `eth_call`/`eth_estimateGas`/simulation surface, which may not share the same recovery wrapping as `DeliverTx`), it results in either: (a) a crash of that node's process (validator halt or degraded/crashed public RPC node), or (b) at minimum a guaranteed transaction-level failure (`ErrPanic`) for every `addCW20Pointer`/`addCW721Pointer`/`addCW1155Pointer` call against a maliciously crafted CW contract, which is a functional break of the pointer bridge for any legitimate CW20/CW721/CW1155 token whose contract does not return strictly well-typed name/symbol fields — this can be weaponized to permanently deny pointer creation for a contract (registration DoS) even if the top-level process does not crash.

### Likelihood Explanation
Trivially reachable: it only requires deploying one CosmWasm contract with a non-conforming `token_info` response and then calling the permissionless `addCW20Pointer` (or CW721/CW1155 equivalent) precompile method from any EVM account — no privileges, no funds beyond gas, and no special network conditions are required.

### Recommendation
Use the two-value type assertion form (`name, ok := formattedRes["name"].(string)`) and return a descriptive error instead of panicking, matching the defensive pattern used elsewhere in the precompile package (e.g., `chargeDecodeGas`'s `ok` check, or the `addr` precompile's blanket `recover()`). Apply the same fix to all `AddCW20`/`AddCW721`/`AddCW1155` implementations across every legacy version directory (`precompiles/pointer/legacy/v552`, `v555`, `v562`, `v580`, `v600`, `v606`, `v610`, `v614`, and the current `precompiles/pointer/pointer.go`), since the same unchecked assertion pattern is duplicated in each.

### Proof of Concept
1. Deploy a CosmWasm contract whose `token_info` query handler returns `{"name": 123, "symbol": "X", "decimals": 0, "total_supply": "0"}` (i.e., `name` is a JSON number, not a string).
2. From any EVM account, call `addCW20Pointer(cwContractAddress)` on the pointer precompile at `0x000000000000000000000000000000000000100b`.
3. Inside `AddCW20`, `json.Unmarshal` succeeds and populates `formattedRes["name"]` with a `float64` value; `formattedRes["name"].(string)` panics with `interface conversion: interface {} is float64, not string`.
4. Observe whether the panic is caught by an outer recovery layer (transaction fails with `ErrPanic`) or escapes uncaught on the specific execution path used, and confirm the concrete blast radius (tx-level failure vs. node crash) via node logs / stack traces.

### Citations

**File:** precompiles/pointer/pointer.go (L21-29)
```go
const (
	PrecompileName   = "pointer"
	AddNativePointer = "addNativePointer"
	AddCW20Pointer   = "addCW20Pointer"
	AddCW721Pointer  = "addCW721Pointer"
	AddCW1155Pointer = "addCW1155Pointer"
)

const PointerAddress = "0x000000000000000000000000000000000000100b"
```

**File:** precompiles/pointer/pointer.go (L47-69)
```go
func NewPrecompile(keepers putils.Keepers) (*pcommon.DynamicGasPrecompile, error) {
	newAbi := pcommon.MustGetABI(f, "abi.json")

	p := &PrecompileExecutor{
		evmKeeper:   keepers.EVMK(),
		bankKeeper:  keepers.BankK(),
		wasmdKeeper: keepers.WasmdVK(),
	}

	for name, m := range newAbi.Methods {
		switch name {
		case AddNativePointer:
			p.AddNativePointerID = m.ID
		case AddCW20Pointer:
			p.AddCW20PointerID = m.ID
		case AddCW721Pointer:
			p.AddCW721PointerID = m.ID
		case AddCW1155Pointer:
			p.AddCW1155PointerID = m.ID
		}
	}

	return pcommon.NewDynamicGasPrecompile(newAbi, p, common.HexToAddress(PointerAddress), PrecompileName), nil
```

**File:** precompiles/pointer/pointer.go (L134-148)
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
```

**File:** precompiles/common/precompiles.go (L205-234)
```go
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
```

**File:** precompiles/addr/legacy/v66/addr.go (L95-101)
```go
func (p PrecompileExecutor) Execute(ctx sdk.Context, method *abi.Method, _ common.Address, _ common.Address, args []interface{}, value *big.Int, readOnly bool, _ *vm.EVM, suppliedGas uint64, hooks *tracing.Hooks) (ret []byte, remainingGas uint64, err error) {
	// Needed to catch gas meter panics
	defer func() {
		if r := recover(); r != nil {
			err = fmt.Errorf("execution reverted: %v", r)
		}
	}()
```
