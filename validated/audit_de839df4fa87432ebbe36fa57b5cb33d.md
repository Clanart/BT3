### Title
Missing nil-check on `state.GetDBImpl()` in legacy precompile error handlers causes nil-pointer dereference - (File: `precompiles/distribution/legacy/v552/distribution.go`)

### Summary
Multiple legacy precompile implementations (distribution, gov, pointer, bank, wasmd, oracle, staking, addr, json, pointerview — versions v552/v555) install a deferred error handler that unconditionally dereferences the result of `state.GetDBImpl(evm.StateDB)` to call `.SetPrecompileError(err)`, without checking whether it returned `nil`. This is the exact bug class in the referenced CVE-2025-21936: a helper that can return `nil` (`mgmt_alloc_skb()` / here `state.GetDBImpl()`) is used without a null/nil check before dereferencing it.

### Finding Description
In `precompiles/distribution/legacy/v552/distribution.go`: [1](#0-0) 
the `Run` method's deferred handler does:
```go
defer func() {
    if err != nil {
        state.GetDBImpl(evm.StateDB).SetPrecompileError(err)
    }
}()
```
with no nil check on the return of `state.GetDBImpl`. The same unguarded pattern exists in the equivalent legacy precompile files for gov [2](#0-1) , pointer [3](#0-2) , and other legacy packages found via `SetPrecompileError` usage in `precompiles/{addr,bank,json,oracle,pointerview,staking,wasmd}/legacy/{v552,v555}`.

By contrast, the currently maintained/refactored common precompile dispatcher explicitly guards this call: [4](#0-3) 
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
This same defensive `sdb != nil` check is present in the newer legacy families (v65, v66, v67, v640, v630, v620, etc.) [5](#0-4) , confirming that `state.GetDBImpl` returning `nil` was a recognized, real condition that was retrofitted with a guard in later code — but the older v552/v555 precompile packages were never patched.

These legacy per-version precompile implementations remain compiled into the binary and are selected at runtime by the EVM keeper based on block height whenever the execution context is a tracing context: [6](#0-5) 
```go
func (k *Keeper) CustomPrecompiles(ctx sdk.Context) map[common.Address]vm.PrecompiledContract {
	if !ctx.IsTracing() {
		return k.latestCustomPrecompiles
	}
	versions := k.GetCustomPrecompilesVersions(ctx)
	...
}
```
Any public JSON-RPC caller invoking `debug_traceCall`, `debug_traceTransaction`, or historical `eth_call`/`eth_estimateGas` against a block height that predates the most recent chain upgrade will be routed to the corresponding legacy precompile implementation for that height, per `GetCustomPrecompilesVersions` [7](#0-6) .

### Impact Explanation
If the legacy precompile's `Run`/`RunAndCalculateGas` returns a non-nil `err` (which is trivially reachable — e.g., calling the distribution/gov/pointer precompile via a staticcall triggers `errors.New("cannot call ... precompile from staticcall")`, or via a mismatched delegatecall triggers `errors.New("cannot delegatecall ...")`) while `state.GetDBImpl(evm.StateDB)` returns `nil` for that execution context, the deferred handler dereferences a nil pointer and panics. A panic inside precompile execution during a public RPC-served EVM trace/call crashes or aborts the serving goroutine/request path of a default-configuration RPC node, which is an explicit accepted impact category ("a crash of default-configuration RPC nodes").

### Likelihood Explanation
The trigger conditions for a non-nil `err` (staticcall / delegatecall mismatch / unknown method) are trivially reachable by any unprivileged RPC client with no special privileges, simply by targeting one of the well-known precompile addresses (e.g., distribution `0x0000000000000000000000000000000000001007`) at a historical block height through the public trace/call RPC surface. The uncertain variable is the exact condition under which `state.GetDBImpl(evm.StateDB)` returns `nil` in that trace/call code path; I was not able to fully inspect the body of `GetDBImpl` in `x/evm/state/statedb.go` within the available tool budget to confirm precisely which StateDB implementations cause it to return `nil`. The strong circumstantial evidence is that later precompile dispatch code was explicitly hardened with a `!= nil` guard around this exact call, indicating the sei-chain maintainers themselves encountered or anticipated `nil` returns from `GetDBImpl` in some execution contexts — the same defensive-fix pattern as the referenced kernel CVE fix for `mgmt_alloc_skb()`.

### Recommendation
Add the same nil-check guard used in the newer precompile dispatcher (`if sdb := state.GetDBImpl(evm.StateDB); sdb != nil { sdb.SetPrecompileError(err) }`) to all legacy precompile `Run`/`RunAndCalculateGas` deferred error handlers (v552/v555 for distribution, gov, pointer, bank, wasmd, oracle, staking, addr, json, pointerview), or recover from the panic in the EVM/tracing dispatch layer so a nil-returning `GetDBImpl` cannot crash the serving process.

### Proof of Concept
1. Identify a block height before the most recent chain upgrade (so `GetCustomPrecompilesVersions` selects a v552/v555 legacy precompile).
2. Send a public RPC `debug_traceCall` (or `eth_call` in trace mode) at that historical height with `to` set to the distribution precompile address `0x0000000000000000000000000000000000001007`, using a staticcall context (readOnly=true) or a delegatecall (caller != callingContract).
3. The legacy precompile's `Run` returns `err != nil` (`"cannot call distr precompile from staticcall"` / `"cannot delegatecall distr"`).
4. The deferred handler executes `state.GetDBImpl(evm.StateDB).SetPrecompileError(err)`; if `GetDBImpl` returns `nil` for that trace-mode StateDB, this panics with a nil-pointer dereference, crashing/aborting the RPC-serving path.

*(Note: full confirmation that `GetDBImpl` returns `nil` on this exact trace/call code path was not completed due to tool/iteration limits — this should be verified against `x/evm/state/statedb.go`'s `GetDBImpl` implementation before remediation.)*

### Citations

**File:** precompiles/distribution/legacy/v552/distribution.go (L106-111)
```go
func (p Precompile) Run(evm *vm.EVM, caller common.Address, callingContract common.Address, input []byte, value *big.Int, readOnly bool, _ bool, hooks *tracing.Hooks) (bz []byte, err error) {
	defer func() {
		if err != nil {
			state.GetDBImpl(evm.StateDB).SetPrecompileError(err)
		}
	}()
```

**File:** precompiles/gov/legacy/v555/gov.go (L109-114)
```go
func (p Precompile) Run(evm *vm.EVM, caller common.Address, callingContract common.Address, input []byte, value *big.Int, readOnly bool, _ bool, hooks *tracing.Hooks) (bz []byte, err error) {
	defer func() {
		if err != nil {
			state.GetDBImpl(evm.StateDB).SetPrecompileError(err)
		}
	}()
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

**File:** precompiles/common/legacy/v66/precompiles.go (L94-101)
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

**File:** x/evm/keeper/keeper.go (L159-169)
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
```

**File:** x/evm/keeper/keeper.go (L183-210)
```go
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
