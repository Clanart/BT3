Confirmed: `RunAndCalculateGas` in `precompiles/common/precompiles.go` has **no top-level `recover()`** around the call to `d.executor.Execute(...)` [1](#0-0) . The only `recover()` in that function is scoped narrowly to `chargeDecodeGas` for gas-meter panics, and re-panics anything else [2](#0-1) . This means a Go panic raised inside a pointer precompile's `Execute` implementation is **not caught and converted to a revert** — it propagates up through the EVM call stack.

### Title
Unrecovered type-assertion panic in the `pointer` precompile's CW20/CW721/CW1155 pointer-creation handlers allows any transaction sender to crash validator/RPC nodes - ([File: precompiles/pointer/pointer.go])

### Summary
The `pointer` precompile's `AddCW20`, `AddCW721`, and `AddCW1155` handlers query an attacker-chosen CosmWasm contract, JSON-unmarshal the response into a `map[string]interface{}`, and then perform unchecked Go type assertions `formattedRes["name"].(string)` / `formattedRes["symbol"].(string)` [3](#0-2) . Because the queried CosmWasm contract is fully attacker-controlled (any user can deploy/instantiate a contract that returns arbitrary JSON for `token_info`/`contract_info`), a response missing the `name`/`symbol` field, or returning a non-string value for it, makes `formattedRes["name"]` be `nil` or a non-string type, causing the type assertion to panic with `interface conversion: interface {} is nil, not string`.

### Finding Description
This is directly analogous to the PowerDNS Recursor bug class: a crafted response from an entity the node "forwards" a query to (there, an upstream DNS server; here, a CosmWasm contract queried via `wasmdKeeper.QuerySmartSafe`) is trusted and parsed without validation, and the malformed shape triggers a crash instead of a graceful error.

The current `precompiles/pointer/pointer.go` (`AddCW20` at line 134, `AddCW721` at line 166, `AddCW1155` at line 198) lacks the `defer func() { recover() ... }` guard that the corresponding `wasmd` precompile execute/query handlers, and even older legacy versions of the pointer precompile (`v552`, `v555`), use to convert panics into ordinary errors [4](#0-3) . The dispatch path `DynamicGasPrecompile.RunAndCalculateGas` → `PrecompileExecutor.Execute` → `AddCW721`/`AddCW1155`/`AddCW20` has no recover wrapping the executor call [1](#0-0) , so the panic is not intercepted at the precompile-framework level either.

### Impact Explanation
An unrecovered panic inside EVM execution (which normally runs inside the ABCI transaction-processing / `eth_call` path) will propagate past the precompile boundary. Depending on how far up the call stack a recover exists (e.g., in the ABCI `DeliverTx`/`CheckTx` handler or `eth_call` RPC handler), this can either abort processing of the single transaction or, if no outer recover exists at that layer, crash the node process handling the block or JSON-RPC request — a denial of service reachable by any address that can submit a transaction or call the public JSON-RPC (`eth_call`) with a crafted, self-deployed CosmWasm contract as the pointer target.

### Likelihood Explanation
High reachability: any unprivileged EVM transaction sender or JSON-RPC caller can invoke `addCW721Pointer`/`addCW1155Pointer`/`addCW20Pointer` on the pointer precompile, pointing at a CosmWasm contract they control that returns a `contract_info`/`token_info` JSON response omitting `name`/`symbol` fields (fully within the attacker's control since they author the contract). No special privileges, governance, or validator collusion are required.

### Recommendation
Add the same `defer func() { if r := recover(); r != nil { ... } }()` guard used by `wasmd`'s `execute`/`query` handlers (and by the legacy `v552`/`v555` pointer precompile) to `AddCW20`, `AddCW721`, and `AddCW1155` in `precompiles/pointer/pointer.go`, and replace the raw type assertions with checked forms (`name, ok := formattedRes["name"].(string)`) that return a normal error instead of panicking on malformed/missing fields.

### Proof of Concept
1. Deploy a CosmWasm contract whose `contract_info` (for CW721/1155) or `token_info` (for CW20) query handler returns `{}` or a JSON object without a `name`/`symbol` key.
2. From any EVM account, call `pointer.addCW721Pointer(cwAddr)` (or `addCW1155Pointer`/`addCW20Pointer`) targeting that contract's Sei address.
3. `p.wasmdKeeper.QuerySmartSafe` returns the crafted JSON; `json.Unmarshal` succeeds into `map[string]interface{}{}`; `formattedRes["name"].(string)` panics because the key is absent (`nil`, not `string`) [5](#0-4) .
4. The panic is unrecovered at the precompile-framework layer [1](#0-0) , propagating up the call stack.

### Citations

**File:** precompiles/common/precompiles.go (L206-214)
```go
	ret, remainingGas, err = d.executor.Execute(ctx, method, caller, callingContract, args, value, readOnly, evm, suppliedGas, hooks)
	if err != nil {
		return ret, remainingGas, err
	}
	events := ctx.EventManager().Events()
	if len(events) > 0 {
		em.EmitEvents(ctx.EventManager().Events())
	}
	return ret, remainingGas, err
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

**File:** precompiles/pointer/pointer.go (L146-155)
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

**File:** precompiles/pointer/pointer.go (L182-187)
```go
	formattedRes := map[string]interface{}{}
	if err := json.Unmarshal(res, &formattedRes); err != nil {
		return nil, 0, err
	}
	name := formattedRes["name"].(string)
	symbol := formattedRes["symbol"].(string)
```

**File:** precompiles/wasmd/legacy/v600/wasmd.go (L332-340)
```go
func (p PrecompileExecutor) execute(ctx sdk.Context, method *abi.Method, caller common.Address, callingContract common.Address, args []interface{}, value *big.Int, readOnly bool, hooks *tracing.Hooks, evm *vm.EVM) (ret []byte, remainingGas uint64, rerr error) {
	defer func() {
		if err := recover(); err != nil {
			ret = nil
			remainingGas = 0
			rerr = fmt.Errorf("%s", err)
			return
		}
	}()
```
