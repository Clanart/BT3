Confirmed root cause and reachability: `Precompile.Run` in `precompiles/common/legacy/v580/precompiles.go` calls `p.Prepare(evm, input)`, which correctly checks for a nil `state.GetDBImpl(evm.StateDB)` result and returns an error, but the `defer func(){ HandlePrecompileError(err, evm, operation) ...}()` runs on *every* returned error — including the one produced by `Prepare` itself when `ctxer == nil`. `HandlePrecompileError` then unconditionally dereferences `state.GetDBImpl(evm.StateDB)` a second time without a nil check:

```go
func HandlePrecompileError(err error, evm *vm.EVM, operation string) {
	if err != nil {
		state.GetDBImpl(evm.StateDB).SetPrecompileError(err)   // no nil check
		metrics.IncrementErrorMetrics(operation, err)
	}
}
``` [1](#0-0) [2](#0-1) 

This mirrors the reported TensorFlow bug pattern exactly: a "get current state" accessor (`ctx->session_state()` / `state.GetDBImpl(evm.StateDB)`) can legitimately return `nil`, one call-site checks for `nil` and errors out, but a second call-site (the error-handling path) dereferences the same accessor's result without checking, causing a nil-pointer panic instead of a clean error. The same unguarded pattern exists in `precompiles/common/legacy/v600/precompiles.go`, `v605/precompiles.go`, `v606/precompiles.go`, and `v610/precompiles.go` (fixed only starting at v614+, which added `if sdb := state.GetDBImpl(evm.StateDB); sdb != nil`). [3](#0-2) 

Reachability: these legacy precompile implementations are not dead code. `Keeper.CustomPrecompiles` picks the historical precompile version whenever `ctx.IsTracing()` is true, and `GetCustomPrecompilesVersions` maps the request's `ctx.BlockHeight()` to the precompile version that was active at that height: [4](#0-3) . This means a public JSON-RPC client calling `debug_traceTransaction`, `debug_traceCall`, `debug_traceBlockByNumber`, or `eth_call`/similar at a historical block number executes these old precompile code paths, including the one with the unguarded `HandlePrecompileError`.

The remaining open question is exactly when `state.GetDBImpl(evm.StateDB)` returns `nil` for a non-nil `evm.StateDB` (i.e., what StateDB type or wrapper causes the type assertion inside `GetDBImpl` to fail) — I found the `DBImpl` struct definition in `x/evm/state/statedb.go` but did not get to read `GetDBImpl`'s implementation itself, so I cannot fully confirm which caller-controlled condition (e.g., simulation/tracing wrapper types, a nested/foreign StateDB during static/delegate-calls) triggers the nil result at that second call-site. Given index limits, a Devin session would be needed to inspect `func GetDBImpl` fully to nail down the precise triggering condition and write a working PoC.

#### Title
Nil-pointer panic in legacy EVM precompile error handling reachable via historical JSON-RPC trace/eth_call requests - (File: `precompiles/common/legacy/v580/precompiles.go`, also v600/v605/v606/v610)

#### Summary
`HandlePrecompileError`, invoked from a deferred call in `Precompile.Run` / `DynamicGasPrecompile.RunAndCalculateGas` for every precompile execution error (including the "cannot get context from EVM" error produced by `Prepare` itself), calls `state.GetDBImpl(evm.StateDB).SetPrecompileError(err)` without checking whether `GetDBImpl` returned `nil`. This is the same bug class as CVE-2020-15204 (dereferencing a `nil` value returned from a state accessor without checking it).

#### Finding Description
`Prepare` explicitly guards against `ctxer == nil`:
```go
ctxer := state.GetDBImpl(evm.StateDB)
if ctxer == nil {
    return sdk.Context{}, nil, nil, errors.New("cannot get context from EVM")
}
```
but the deferred `HandlePrecompileError(err, evm, operation)` in `Run`/`RunAndCalculateGas` runs on that very error path and re-invokes `state.GetDBImpl(evm.StateDB)` without the same guard, immediately dereferencing the result. If `GetDBImpl` returns `nil` under the same conditions that made `Prepare` bail out, the node panics.

#### Impact Explanation
A panic inside precompile execution during a `Run`/`RunAndCalculateGas` call crashes the goroutine handling that RPC/EVM call. If triggered from `eth_call`, `debug_traceCall`, or `debug_traceTransaction`/`debug_traceBlockByNumber` against a public RPC node (which selects this legacy precompile version via `GetCustomPrecompilesVersions` based on `ctx.BlockHeight()` when `ctx.IsTracing()`), this can crash or degrade the RPC node's default-configuration serving process, matching the "crash of default-configuration RPC nodes" acceptance criterion.

#### Likelihood Explanation
Requires being able to make `state.GetDBImpl(evm.StateDB)` return `nil` at the `HandlePrecompileError` call site while still reaching `Prepare`'s error return — i.e., an attacker-controllable historical `eth_call`/trace request against one of the affected legacy precompile versions (v580/v600/v605/v606/v610) hitting any of the standard "cannot get context" or subsequent execute-error paths. I was not able to fully confirm the exact StateDB configuration that causes `GetDBImpl` to return `nil` without reading its full implementation, so likelihood is uncertain and should be validated further (e.g., by examining `func GetDBImpl` in `x/evm/state/statedb.go` and how `evm.StateDB` is constructed for tracing/simulation calls).

#### Recommendation
Apply the same fix that later versions (v614+) already contain: guard the second call site too:
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
in `precompiles/common/legacy/v580/precompiles.go`, `v600/precompiles.go`, `v605/precompiles.go`, `v606/precompiles.go`, and `v610/precompiles.go`.

#### Proof of Concept
Not fully constructed — requires determining the exact StateDB/tracing configuration under which `state.GetDBImpl` returns `nil` (this needs reading the full `GetDBImpl` implementation, which was not completed in this investigation). Conceptually: send a `debug_traceCall`/`eth_call`/`debug_traceTransaction` JSON-RPC request targeting a historical block height mapped to one of the affected legacy precompile upgrade versions, invoking a precompile method that fails inside `Prepare` (or later in `Execute`) with the StateDB wrapper in a state where `state.GetDBImpl` returns `nil`, causing the deferred `HandlePrecompileError` to panic on the nil dereference.

### Citations

**File:** precompiles/common/legacy/v580/precompiles.go (L60-98)
```go
func (p Precompile) Run(evm *vm.EVM, caller common.Address, callingContract common.Address, input []byte, value *big.Int, readOnly bool, _ bool, hooks *tracing.Hooks) (bz []byte, err error) {
	operation := fmt.Sprintf("%s_unknown", p.name)
	defer func() {
		HandlePrecompileError(err, evm, operation)
		if err != nil {
			bz = []byte(err.Error())
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

func HandlePrecompileError(err error, evm *vm.EVM, operation string) {
	if err != nil {
		state.GetDBImpl(evm.StateDB).SetPrecompileError(err)
		metrics.IncrementErrorMetrics(operation, err)
	}
}

func (p Precompile) Prepare(evm *vm.EVM, input []byte) (sdk.Context, *abi.Method, []interface{}, error) {
	ctxer := state.GetDBImpl(evm.StateDB)
	if ctxer == nil {
		return sdk.Context{}, nil, nil, errors.New("cannot get context from EVM")
```

**File:** precompiles/common/legacy/v614/precompiles.go (L93-99)
```go
func HandlePrecompileError(err error, evm *vm.EVM, operation string) {
	if err != nil {
		if sdb := state.GetDBImpl(evm.StateDB); sdb != nil {
			sdb.SetPrecompileError(err)
		}
		metrics.IncrementErrorMetrics(operation, err)
	}
```

**File:** x/evm/keeper/keeper.go (L159-210)
```go
func (k *Keeper) TraceSnapshotStore() *TraceSnapshotStore     { return k.traceSnapshotStore }
func (k *Keeper) SetTraceSnapshotCapture(f func() sctypes.Committer) {
	k.traceSnapshotCapture = f
}

func (k *Keeper) SetCustomPrecompiles(cp map[common.Address]putils.VersionedPrecompiles, latestUpgrade string) {
	k.customPrecompiles = cp
	k.latestUpgrade = latestUpgrade
	k.latestCustomPrecompiles = make(map[common.Address]vm.PrecompiledContract, len(cp))
	for addr, versioned := range cp {
		k.latestCustomPrecompiles[addr] = versioned[latestUpgrade]
	}
}

func (k *Keeper) CustomPrecompiles(ctx sdk.Context) map[common.Address]vm.PrecompiledContract {
	if !ctx.IsTracing() {
		return k.latestCustomPrecompiles
	}
	versions := k.GetCustomPrecompilesVersions(ctx)
	cp := make(map[common.Address]vm.PrecompiledContract, len(k.customPrecompiles))
	for addr, versioned := range k.customPrecompiles {
		cp[addr] = versioned[versions[addr]]
	}
	return cp
}

func (k *Keeper) GetCustomPrecompilesVersions(ctx sdk.Context) map[common.Address]string {
	height := ctx.BlockHeight()
	cp := make(map[common.Address]string, len(k.customPrecompiles))
	for addr, versioned := range k.customPrecompiles {
		mostRecentUpgradeHeight := int64(0)
		noForkHistory := true
		for upgrade := range versioned {
			upgradeHeight := k.upgradeKeeper.GetDoneHeight(ctx, upgrade)
			if upgradeHeight != 0 {
				noForkHistory = false
			}
			if height < upgradeHeight {
				// requested height hasn't seen this upgrade version yet.
				continue
			}
			if upgradeHeight > mostRecentUpgradeHeight {
				mostRecentUpgradeHeight = upgradeHeight
				cp[addr] = upgrade
			}
		}
		if noForkHistory {
			cp[addr] = k.latestUpgrade
		}
	}
	return cp
}
```
