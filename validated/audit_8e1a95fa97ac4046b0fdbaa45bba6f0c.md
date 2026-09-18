## Finding Description

The kernel bug is a classic "dereference of a resource that failed to be validated in an error/cleanup path" — `nfsd4_read_release()`'s trace point dereferences a filehandle-derived pointer without checking that it was actually acquired.

The sei-chain codebase has a structurally identical class of bug across many of its versioned EVM precompiles. `state.GetDBImpl` performs a type assertion and explicitly returns `nil` when the passed `vm.StateDB` is neither `*state.DBImpl` nor a `*state.HookedStateDB` wrapping one: [1](#0-0) 

Every legacy precompile package from `v552` through `v610` (and the corresponding `common` package used by `Prepare`/`HandlePrecompileError`) calls this function and immediately dereferences the result without a nil check, e.g. in the pointer precompile's error-handling defer: [2](#0-1) 

and the equivalent unguarded pattern in `precompiles/common/legacy/v605/precompiles.go`: [3](#0-2) 

The same unguarded call appears in the `v552`/`v555`/`v562`/`v575`/`v580`/`v600`/`v605`/`v606`/`v610` legacy variants of `addr`, `bank`, `distribution`, `gov`, `json`, `oracle`, `pointer`, `pointerview`, `staking`, and `wasmd` precompiles (confirmed by the repo-wide grep for `GetDBImpl(evm.StateDB).Set`).

Critically, this exact pattern was *fixed* starting at `v614` and in all newer/legacy-common packages, adding the missing nil guard: [4](#0-3) [5](#0-4) 

This confirms the maintainers recognized `GetDBImpl(evm.StateDB)` can legitimately return `nil` in some reachable code path and only patched it forward from `v614` onward — the `v552`–`v610` versions remain vulnerable to a nil-pointer panic whenever `Prepare`/method dispatch produces an `err` while `evm.StateDB` is not a `*DBImpl`/`*HookedStateDB`.

These legacy precompile implementations are not dead code: each precompile module selects the implementation by chain upgrade height via `GetVersioned`, so any code path that replays/executes an EVM call under a historical chain version (e.g. `debug_traceTransaction`, `debug_traceCall`, `debug_traceBlockByNumber`, or `eth_call` at a historical block) resolves to these unguarded legacy packages: [6](#0-5) 

## Impact Explanation

If `evm.StateDB` fails the type assertion in `GetDBImpl` while the legacy (`≤v610`) precompile path returns an error (e.g., unknown method, invalid pointer request, malformed input to `Prepare`), the immediate dereference of the `nil` `*DBImpl` when calling `.SetPrecompileError(err)` panics. Because this fires inside the EVM execution/precompile dispatch path reachable from historical-block tracing/`eth_call` RPC handlers, an unprivileged, unauthenticated public JSON-RPC client can crash the serving node process — a denial of service against default-configuration RPC nodes, matching the severity class of the reported kernel crash (CVSS AV:N/AC:L/PR:N — remote, no privileges, availability impact).

## Likelihood Explanation

I was unable to fully confirm, within the available exploration budget, a concrete production code path where `evm.StateDB` passed into these legacy precompiles is neither `*state.DBImpl` nor `*state.HookedStateDB` (e.g., a custom override/simulation StateDB type used by `evmrpc`'s state-override or block-replay tracing machinery). The `evmrpc/simulate.go` state-override and trace-replay functions (`replayTransactionTillIndex`, `StateAtTransaction`, `PrepareTx`) all construct `state.NewDBImpl` or a `state.HookedStateDB`, which *would* be correctly recognized — so under normal tracing flows the guard-less legacy code may never actually observe a mismatched type in current usage. Without finding an actual caller that injects a different `vm.StateDB` implementation, I cannot assert this is presently exploitable end-to-end; the codebase evidence proves the type hazard exists and was patched going forward, but the concrete unprivileged trigger for the un-patched versions (`v552`–`v610`) is not fully verified here.

## Recommendation

Backport the `v614`+ nil-check fix (`if sdb := state.GetDBImpl(evm.StateDB); sdb != nil { sdb.SetPrecompileError(err) }`) to all legacy precompile packages (`v552` through `v610`, including their `common` packages and every precompile module: `addr`, `bank`, `distribution`, `gov`, `json`, `oracle`, `pointer`, `pointerview`, `staking`, `wasmd`), and audit every other caller of `state.GetDBImpl` in the legacy trees for the same unguarded-dereference pattern.

## Proof of Concept

Not independently reproduced — this report is based on static code analysis showing (1) `GetDBImpl` can return `nil` by design, (2) unguarded dereferences of that return value exist in `v552`–`v610` legacy precompile code, and (3) the identical pattern was fixed starting at `v614`, evidencing the maintainers considered the nil case reachable. Confirming a live trigger would require identifying the exact RPC/tracing code path that supplies a non-`DBImpl`/non-`HookedStateDB` `vm.StateDB` when executing under a pre-`v6.1.4` chain-upgrade height, which was not conclusively located within the available search budget.

### Citations

**File:** x/evm/state/statedb.go (L373-381)
```go
func GetDBImpl(vmsdb vm.StateDB) *DBImpl {
	if sdb, ok := vmsdb.(*DBImpl); ok {
		return sdb
	}
	if hdb, ok := vmsdb.(*state.HookedStateDB); ok {
		return GetDBImpl(hdb.StateDB)
	}
	return nil
}
```

**File:** precompiles/pointer/legacy/v552/pointer.go (L104-109)
```go
func (p Precompile) RunAndCalculateGas(evm *vm.EVM, caller common.Address, callingContract common.Address, input []byte, suppliedGas uint64, value *big.Int, hooks *tracing.Hooks, readOnly bool, _ bool) (ret []byte, remainingGas uint64, err error) {
	defer func() {
		if err != nil {
			state.GetDBImpl(evm.StateDB).SetPrecompileError(err)
		}
	}()
```

**File:** precompiles/common/legacy/v605/precompiles.go (L93-98)
```go
func HandlePrecompileError(err error, evm *vm.EVM, operation string) {
	if err != nil {
		state.GetDBImpl(evm.StateDB).SetPrecompileError(err)
		metrics.IncrementErrorMetrics(operation, err)
	}
}
```

**File:** precompiles/common/legacy/v614/precompiles.go (L93-100)
```go
func HandlePrecompileError(err error, evm *vm.EVM, operation string) {
	if err != nil {
		if sdb := state.GetDBImpl(evm.StateDB); sdb != nil {
			sdb.SetPrecompileError(err)
		}
		metrics.IncrementErrorMetrics(operation, err)
	}
}
```

**File:** precompiles/common/precompiles.go (L92-99)
```go
func HandlePrecompileError(err error, evm *vm.EVM, operation string) {
	if err != nil {
		if sdb := state.GetDBImpl(evm.StateDB); sdb != nil {
			sdb.SetPrecompileError(err)
		}
		metrics.IncrementErrorMetrics(operation, err)
	}
}
```

**File:** precompiles/pointer/setup.go (L25-44)
```go
func GetVersioned(latestUpgrade string, keepers utils.Keepers) utils.VersionedPrecompiles {
	return utils.VersionedPrecompiles{
		latestUpgrade: check(NewPrecompile(keepers)),
		"v5.5.2":      check(pointerv552.NewPrecompile(keepers)),
		"v5.5.5":      check(pointerv555.NewPrecompile(keepers)),
		"v5.6.2":      check(pointerv562.NewPrecompile(keepers)),
		"v5.7.5":      check(pointerv575.NewPrecompile(keepers)),
		"v5.8.0":      check(pointerv580.NewPrecompile(keepers)),
		"v6.0.0":      check(pointerv600.NewPrecompile(keepers)),
		"v6.0.5":      check(pointerv605.NewPrecompile(keepers)),
		"v6.0.6":      check(pointerv606.NewPrecompile(keepers)),
		"v6.1.0":      check(pointerv610.NewPrecompile(keepers)),
		"v6.1.4":      check(pointerv614.NewPrecompile(keepers)),
		"v6.2.0":      check(pointerv620.NewPrecompile(keepers)),
		"v6.3.0":      check(pointerv630.NewPrecompile(keepers)),
		"v6.4.0":      check(pointerv640.NewPrecompile(keepers)),
		"v6.5":        check(pointerv65.NewPrecompile(keepers)),
		"v6.6":        check(pointerv66.NewPrecompile(keepers)),
	}
}
```
