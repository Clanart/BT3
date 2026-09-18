### Title
Unchecked type assertion on attacker-controlled CosmWasm query response in the pointer precompile's `AddCW20`/`AddCW721`/`AddCW1155` causes a Go panic reachable from any EVM caller - ([File: precompiles/pointer/pointer.go])

### Summary
The i18next advisory's underlying bug class is: an unauthenticated, public entry point trusts an attacker-influenced value's *shape* (type/key presence) without validation, and performs an unsafe operation on it, producing memory/type corruption or a crash. The closest reachable analog in sei-chain is the EVM `pointer` precompile's CW→ERC pointer-creation handlers, which unmarshal a JSON response coming from an arbitrary, caller-chosen CosmWasm contract and immediately perform unchecked Go type assertions on it.

### Finding Description
`AddCW721` and `AddCW1155` (and the twin logic in `AddCW20`, replicated across every legacy pointer precompile version, e.g. `precompiles/pointer/legacy/v552/pointer.go`, `v555`, `v562`, etc.) do:

```go
cwAddr := args[0].(string)
...
res, err := p.wasmdKeeper.QuerySmartSafe(ctx, cwAddress, []byte("{\"contract_info\":{}}"))
...
formattedRes := map[string]interface{}{}
if err := json.Unmarshal(res, &formattedRes); err != nil { return nil, 0, err }
name := formattedRes["name"].(string)
symbol := formattedRes["symbol"].(string)
``` [1](#0-0) [2](#0-1) 

`cwAddr` (`args[0]`) is fully attacker-controlled: any EVM caller can invoke `AddCW20`/`AddCW721`/`AddCW1155` on the pointer precompile pointing at *any* CW20/CW721/CW1155-labelled contract address, including a contract the attacker deploys and controls (a "malicious" but ordinary CosmWasm contract, which is in-scope as a "CosmWasm user"-reachable path). The precompile queries that attacker-owned contract with `{"contract_info":{}}` / `{"token_info":{}}` and blindly trusts the JSON shape of the reply. If the attacker's contract's query response omits the `name`/`symbol` keys, or returns them as a non-string JSON type (number, bool, object, array, null), `formattedRes["name"].(string)` fails as a *single-value* type assertion. A single-value (non `, ok`) failed type assertion in Go panics with `interface conversion: interface {} is <T>, not string`.

This mirrors the report's core anti-pattern: user-controlled data (there, `lng`/`ns`; here, the response body of a contract the caller nominates) reaches a code path that performs an unguarded, type-unsafe operation, producing "type confusion" that the advisory explicitly calls out as a DoS vector ("cause denial of service via type confusion").

The identical unguarded pattern (`formattedRes["name"].(string)`, `formattedRes["symbol"].(string)`) is duplicated across every legacy pointer-precompile snapshot in the repo (`v552`, `v555`, `v562`, `v575`, `v580`, `v600`, `v605`, `v606`, `v610`, `v614`, `v620`, `v630`, `v640`, `v65`, `v66`, `v67`, and the current `precompiles/pointer/pointer.go`), confirming this is a long-standing, unfixed pattern rather than a one-off typo.

### Impact Explanation
The wrapper around precompile execution (`Precompile.Run` / `DynamicGasPrecompile.RunAndCalculateGas` in `precompiles/common/precompiles.go`) only intercepts the *returned error* via `HandlePrecompileError`; it installs no `recover()` around `d.executor.Execute(...)`, so a panic raised inside `AddCW20`/`AddCW721`/`AddCW1155` propagates up through the EVM call stack uncaught at that layer. Whether this panic is ultimately contained depends on which execution path handles the transaction:
- In the legacy sequential/ABCI `DeliverTx` path (`app/legacyabci/deliver_tx.go` + `app/legacyabci/recovery.go`), a generic panic is caught by the top-level `recover()` and converted into an `ErrPanic` result for that transaction only, so the immediate effect there is a failed transaction, not a node crash.
- However, this same unguarded panic also executes inside the OCC/Giga parallel-execution and goroutine-based scheduling paths (e.g. `giga/deps/tasks/scheduler.go` `prepareAndRunTask`/`executeAll`, `giga/evmonly/occ.go` speculative worker goroutines). Any panic occurring in a goroutine that is not itself wrapped in a `recover()` crashes the entire Go process, not just the current transaction — this is the standard Go panic-in-goroutine failure mode. The provided evidence does not conclusively show a wrapping `recover()` at every one of these goroutine boundaries for this specific precompile-triggered panic, so the exact blast radius (single-tx error vs. full validator/RPC-node crash) could not be fully confirmed from the code inspected.
- Because the precompile call is triggered by ordinary EVM transaction execution, it is reachable identically on any full/RPC node executing that block, so if the panic is not contained at some layer, it can produce a synchronized crash across all nodes that process the transaction — a validator halt / block-processing failure, which is in-scope impact per the task's acceptance criteria (validator halt, block delay, RPC node crash).

### Likelihood Explanation
Likelihood is high for triggering the panic itself: any account can (1) instantiate a trivial CosmWasm contract that responds to `token_info`/`contract_info` queries with a JSON body lacking `name`/`symbol` or with non-string values for those keys, and (2) call the EVM pointer precompile's `AddCW20`, `AddCW721`, or `AddCW1155` method with that contract's address as `cwAddr`. No special privileges, governance action, or validator collusion is required — this is a straightforward single-transaction, single-contract-deployment attack fully within the scope of an "unprivileged transaction sender" / "CosmWasm user" / "contract deployer" as defined by this task.

### Recommendation
Replace every unguarded `formattedRes["name"].(string)` / `formattedRes["symbol"].(string)` (and any other single-value type assertion on `json.Unmarshal`'d attacker-influenced data) in `precompiles/pointer/pointer.go` and all its legacy snapshots with the two-value form:
```go
name, ok := formattedRes["name"].(string)
if !ok {
    return nil, 0, fmt.Errorf("unexpected or missing name field in contract response")
}
symbol, ok := formattedRes["symbol"].(string)
if !ok {
    return nil, 0, fmt.Errorf("unexpected or missing symbol field in contract response")
}
```
Additionally, audit `Precompile.Run` / `DynamicGasPrecompile.RunAndCalculateGas` to add a `recover()` boundary around `executor.Execute(...)` so that any future unguarded panic inside a precompile executor degrades to a reverted call rather than potentially escaping into an unrecovered goroutine panic in the OCC/Giga scheduling paths.

### Proof of Concept
1. Deploy a minimal CosmWasm contract whose `contract_info`/`token_info` query handler returns `{"foo":"bar"}` (omitting `name`/`symbol`) or `{"name": 123, "symbol": 456}` (wrong JSON types).
2. From any EOA, send an EVM transaction calling the pointer precompile's `addCW721` (or `addCW20`/`addCW1155`) method with `cwAddr` set to that contract's bech32 address.
3. `p.wasmdKeeper.QuerySmartSafe` returns the crafted JSON; `json.Unmarshal` succeeds; `formattedRes["name"].(string)` panics with `interface conversion: interface {} is <nil|float64>, not string`, since neither `Precompile.Run`/`RunAndCalculateGas` nor `AddCW721`/`AddCW1155` recovers from panics at that call site.

### Citations

**File:** precompiles/pointer/pointer.go (L180-195)
```go
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
```

**File:** precompiles/pointer/pointer.go (L198-220)
```go
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
```
