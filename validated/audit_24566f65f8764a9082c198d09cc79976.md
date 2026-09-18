### Title
Unchecked `GetDBImpl` Return Value Leads to Nil-Pointer Dereference in Legacy Precompile Error Handling - ([File: precompiles/common/legacy/v610/precompiles.go])

### Summary
Multiple legacy-versioned precompile packages call `state.GetDBImpl(evm.StateDB).SetPrecompileError(err)` without checking whether `GetDBImpl` returned `nil`, exactly mirroring the CVE-2022-0907 bug class ("Unchecked Return Value to NULL Pointer Dereference"). Newer precompile code paths were later hardened with an explicit nil check, confirming that `GetDBImpl` can legitimately return `nil`.

### Finding Description
`Precompile.Prepare` explicitly handles the case where the StateDB cannot be adapted to the Sei context implementer: [1](#0-0) 
returning the error `"cannot get context from EVM"` when `ctxer := state.GetDBImpl(evm.StateDB)` is `nil`.

However, the `HandlePrecompileError` helper — invoked as a deferred error handler in `RunAndCalculateGas`/`Execute`/`Run` across these precompiles — re-invokes `state.GetDBImpl(evm.StateDB)` and unconditionally dereferences the result: [2](#0-1) 

If `evm.StateDB` is in a state where `GetDBImpl` returns `nil` (the same condition `Prepare` itself detects and reports as `"cannot get context from EVM"`), calling `.SetPrecompileError(err)` on that nil value dereferences a nil pointer and panics.

This exact pattern — unguarded direct dereference of `state.GetDBImpl(evm.StateDB)` — is repeated in numerous legacy precompile files: `precompiles/addr/legacy/v555/addr.go`, `precompiles/bank/legacy/{v552,v555}/bank.go`, `precompiles/common/legacy/{v562,v575,v580,v600,v605,v606,v610}/precompiles.go`, `precompiles/distribution/legacy/{v552,v555}/distribution.go`, `precompiles/gov/legacy/v555/gov.go`, `precompiles/json/legacy/{v552,v555}/json.go`, `precompiles/oracle/legacy/v555/oracle.go`, `precompiles/pointer/legacy/{v552,v555}/pointer.go`, `precompiles/pointerview/legacy/v555/pointerview.go`, `precompiles/staking/legacy/v555/staking.go`, and `precompiles/wasmd/legacy/{v552,v555}/wasmd.go`.

The bug was fixed later — `precompiles/common/legacy/v620/precompiles.go` and the top-level `precompiles/common/precompiles.go` add an explicit nil guard: [3](#0-2) 
This confirms `GetDBImpl` can return `nil` in production and that the unguarded call in the older, still-shipped legacy code is an unchecked-return-value bug of the same class as ALPINE-CVE-2022-0907.

### Impact Explanation
A nil-pointer dereference inside a precompile's error-handling defer triggers a Go runtime panic during EVM transaction execution. Depending on how far up the call stack the panic is recovered, this can crash the node process handling the transaction (default-configuration EVM/RPC node) or, if unrecovered during block processing/replay of historical blocks that still route through these legacy precompile versions (selected via `ctx.ClosestUpgradeName()`), can halt block production or full-node sync — a validator/RPC halt condition.

### Likelihood Explanation
The versioned precompile selection mechanism (seen via `semver.Compare(ctx.ClosestUpgradeName(), ...)` patterns in `x/evm/keeper/pointer.go`) dispatches to these legacy implementations for historical chain heights. Any node performing a full sync/replay through the affected upgrade ranges executes this vulnerable code path. I was not able to fully verify, within the available tooling, the exact runtime condition that causes `GetDBImpl` to return `nil` for a given `evm.StateDB`, nor whether a panic here is caught by an outer recover in the EVM ante/message-handling pipeline before it can halt a node — this would need further investigation in a live/Devin session with full source access.

### Recommendation
Backport the nil-guard fix already present in `precompiles/common/legacy/v620/precompiles.go` and `precompiles/common/precompiles.go` to all older legacy `HandlePrecompileError`/inline `SetPrecompileError` call sites listed above, i.e., replace:
```go
state.GetDBImpl(evm.StateDB).SetPrecompileError(err)
```
with:
```go
if sdb := state.GetDBImpl(evm.StateDB); sdb != nil {
    sdb.SetPrecompileError(err)
}
```

### Proof of Concept
Conceptually: submit an EVM transaction that calls any precompile whose legacy version's `Prepare` returns the `"cannot get context from EVM"` error (i.e., a condition where `state.GetDBImpl(evm.StateDB)` is `nil`). The deferred `HandlePrecompileError`/inline handler in that same call re-invokes `GetDBImpl`, gets `nil` again, and dereferences it, panicking the node executing that transaction. I could not construct or verify an end-to-end runnable exploit within this ask-only investigation because I don't have the tooling to execute code or trace the exact conditions under which `GetDBImpl` returns `nil` at runtime; a Devin session with full repo/build access would be needed to confirm exploitability and blast radius (single-tx panic vs. consensus-affecting halt).

### Citations

**File:** precompiles/common/legacy/v610/precompiles.go (L93-98)
```go
func HandlePrecompileError(err error, evm *vm.EVM, operation string) {
	if err != nil {
		state.GetDBImpl(evm.StateDB).SetPrecompileError(err)
		metrics.IncrementErrorMetrics(operation, err)
	}
}
```

**File:** precompiles/common/legacy/v610/precompiles.go (L100-104)
```go
func (p Precompile) Prepare(evm *vm.EVM, input []byte) (sdk.Context, *abi.Method, []interface{}, error) {
	ctxer := state.GetDBImpl(evm.StateDB)
	if ctxer == nil {
		return sdk.Context{}, nil, nil, errors.New("cannot get context from EVM")
	}
```

**File:** precompiles/common/legacy/v620/precompiles.go (L93-99)
```go
func HandlePrecompileError(err error, evm *vm.EVM, operation string) {
	if err != nil {
		if sdb := state.GetDBImpl(evm.StateDB); sdb != nil {
			sdb.SetPrecompileError(err)
		}
		metrics.IncrementErrorMetrics(operation, err)
	}
```
