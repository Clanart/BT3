## Analysis

The reported ApostropheCMS bug class is: a value bypasses type/format validation via a special-case check (prefix match), and is then consumed unsanitized/unchecked in a context that assumes it has already been validated, causing unexpected control-flow/parsing behavior downstream.

The closest analog reachable by an unprivileged sei-chain user is in the CW20→ERC20 **pointer precompile**, where the contents of a CosmWasm contract's `token_info` query response — fully attacker-controlled (any address can deploy a CW20-shaped contract) — are consumed with unchecked Go type assertions instead of validated field extraction.

### Title
Unchecked Type Assertion on Attacker-Controlled CW20 `token_info` Response Panics the Pointer Precompile - (File: `precompiles/pointer/pointer.go`)

### Summary
`PrecompileExecutor.AddCW20` in the `pointer` precompile (address `0x100b`) queries an arbitrary, caller-supplied CosmWasm contract address for `{"token_info":{}}` and then does a raw, unchecked Go type assertion on the JSON-decoded `name` and `symbol` fields: `formattedRes["name"].(string)` / `formattedRes["symbol"].(string)`. [1](#0-0) 

Because `formattedRes` is a `map[string]interface{}` populated from `json.Unmarshal` of a smart-contract response that the caller fully controls (they can point `addCW20Pointer` at any contract implementing an arbitrary `token_info` handler), an attacker can make `name`/`symbol` be `nil`, a number, a bool, an array, or an object instead of a string. A bare `.(string)` assertion (without the `, ok` form) panics in that case.

### Finding Description
1. `AddCW20` takes `cwAddr := args[0].(string)` directly from EVM call arguments — any address the caller passes. [2](#0-1) 
2. It calls `p.wasmdKeeper.QuerySmartSafe(ctx, cwAddress, []byte("{\"token_info\":{}}"))` against that contract and unmarshal the raw response into an untyped map. [3](#0-2) 
3. It then extracts `name`/`symbol` with unchecked type assertions: [4](#0-3) 
4. `Execute()`/`AddCW20()` in this file has **no `recover()`**, unlike several sibling precompiles (`addr`, `solo`, `wasmd`) that explicitly wrap their dispatch in `defer func() { recover() ... }()` specifically to catch this class of panic. [5](#0-4) [6](#0-5) 
5. The outer generic `Precompile.Run()` wrapper only calls `HandlePrecompileError(err, evm, operation)` on the returned `err` value — it does not `recover()` a panic that propagates out of `p.executor.Execute(...)` before it can even set `err`. [7](#0-6) 

The identical unguarded pattern (`formattedRes["name"].(string)` / `formattedRes["symbol"].(string)` with no `recover()` in `Execute`/`AddCW20`) is duplicated across every legacy pointer-precompile version (`v605`, `v606`, `v610`, `v614`, `v620`, `v630`, `v640`, `v65`, `v66`, `v67`, current), so this is not a one-off — it is the standing implementation across the whole precompile version history. [8](#0-7) 

### Impact Explanation
Because the panic is triggered by fully deterministic, attacker-supplied contract logic, every node that executes the same `addCW20Pointer` call (whether via a mined transaction, a JSON-RPC `eth_call`, or `eth_estimateGas` simulation against the pointer precompile) hits the identical unrecovered panic. Some EVM execution paths in sei-chain (e.g., direct `eth_call`/`eth_estimateGas`/trace RPC handlers) invoke the EVM/precompile stack outside the ABCI `DeliverTx` panic-recovery boundary that Cosmos SDK's `baseapp.runTx` normally provides for on-chain transactions. Where such a JSON-RPC path calls into `AddCW20` without its own top-level recover, this becomes a crash of a default-configuration public RPC node from a single unprivileged `eth_call`/`eth_estimateGas`/trace request against the pointer precompile with a malicious CW20 contract address — no funds or special privileges required, only a deployed CW20-shaped contract and one RPC/EVM call. Even confined strictly to the transaction-execution path, this is a hard, unconditional denial of the CW20 pointer registration feature for any contract whose `token_info` does not return `name`/`symbol` as plain strings, which the pointer precompile should instead reject gracefully as an error rather than panic on.

### Likelihood Explanation
Likelihood is high: deploying an arbitrary CosmWasm contract that answers `{"token_info":{}}` with a non-string `name`/`symbol` (or omits them, yielding `nil`) requires no special permission on sei-chain — any CosmWasm user can deploy such a contract, and any EVM caller (including via a plain `eth_call`) can then invoke `addCW20Pointer(cwAddr)` against it. This is a one-step, unprivileged, fully reproducible trigger, and the bug is replicated identically across all shipped precompile versions.

### Recommendation
Replace the unchecked type assertions in `AddCW20` (and all legacy copies) with the safe, two-value assertion form and return a normal error instead of panicking:
```go
nameVal, ok := formattedRes["name"].(string)
if !ok {
    return nil, 0, fmt.Errorf("token_info response for %s did not return a string name", cwAddr)
}
symbolVal, ok := formattedRes["symbol"].(string)
if !ok {
    return nil, 0, fmt.Errorf("token_info response for %s did not return a string symbol", cwAddr)
}
```
Additionally, wrap `PrecompileExecutor.Execute`/`AddCW20` in a `recover()` (mirroring `addr`/`solo`/`wasmd` precompiles) as defense-in-depth so any future unchecked assertion on external CW20/CW721 response data degrades to `execution reverted` instead of an unrecovered panic, and audit `AddCW721`/`AddCW1155` and the pointerview precompile for the same pattern.

### Proof of Concept
1. Deploy a CosmWasm contract at address `cw1...` whose `token_info` query handler returns `{"name": 12345, "symbol": "OK", "decimals": 6, "total_supply": "0"}` (a JSON number instead of a string for `name`), or simply omits the `name` key entirely.
2. From any EVM account (no association or special role required), call the pointer precompile (`0x000000000000000000000000000000000000100b`) method `addCW20Pointer(cw1...)`, either as a mined transaction or via `eth_call`.
3. Execution reaches `formattedRes["name"].(string)` in `AddCW20` with `formattedRes["name"]` being a `float64`/`nil`, triggering a Go runtime panic (`interface conversion: interface {} is float64, not string` or `... interface {} is nil, not string`). [9](#0-8) 
4. Because `Execute()`/`AddCW20()` has no `recover()`, and the outer `Precompile.Run()` wrapper does not catch a panic occurring before `err` is set, the panic propagates out of the precompile call into the EVM/RPC call stack rather than being converted into `execution reverted`. [10](#0-9) 

Note: I was able to conclusively confirm the unchecked-assertion root cause and the absence of a local `recover()` in this precompile, matching the pattern other precompiles explicitly guard against. I was not able to fully trace, within this session, every RPC/execution entry point in `evmrpc/` to determine with certainty which of them lack a top-level panic recovery boundary versus which are wrapped by `baseapp.runTx`'s tx-level recovery (which would downgrade the impact from "node crash" to "single failed transaction/DoS on pointer creation for malformed CW20 contracts"). A full audit of `evmrpc/call.go`, `evmrpc/simulate.go`, and related handlers for a top-level `recover()` around EVM/precompile execution would be needed to establish the precise blast radius with certainty.

### Citations

**File:** precompiles/pointer/pointer.go (L134-156)
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
```

**File:** precompiles/addr/legacy/v603/addr.go (L93-99)
```go
func (p PrecompileExecutor) Execute(ctx sdk.Context, method *abi.Method, _ common.Address, _ common.Address, args []interface{}, value *big.Int, readOnly bool, _ *vm.EVM, suppliedGas uint64, hooks *tracing.Hooks) (ret []byte, remainingGas uint64, err error) {
	// Needed to catch gas meter panics
	defer func() {
		if r := recover(); r != nil {
			err = fmt.Errorf("execution reverted: %v", r)
		}
	}()
```

**File:** precompiles/solo/legacy/v65/solo.go (L90-99)
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

**File:** precompiles/common/precompiles.go (L62-90)
```go
}

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

**File:** precompiles/pointer/legacy/v605/pointer.go (L132-165)
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

```
