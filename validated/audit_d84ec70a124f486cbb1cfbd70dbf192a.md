### Title
`eth_call` / `eth_estimateGas` Bypass `ErrStateConflict` Detection, Returning Incorrect Success for Transactions That Will Fail On-Chain — (File: `x/evm/keeper/state_transition.go`, `x/evm/statedb/statedb.go`)

---

### Summary

The `ErrStateConflict` guard in `StateDB.Commit()` — which detects when both EVM execution and a nested native precompile action write to the same storage slot — is only evaluated when `commit=true`. Because `eth_call` and `eth_estimateGas` invoke `ApplyMessageWithConfig` with `commit=false`, the conflict check is silently skipped. Any transaction that would fail on-chain with `ErrStateConflict` (status=0) will appear to succeed in simulation, causing users to submit transactions that consume gas fees but produce no state change.

---

### Finding Description

**Root cause — `x/evm/statedb/statedb.go` `Commit()`, lines 758–788:**

The non-overlap invariant is enforced only inside `Commit()`:

```go
// Enforce the non-overlap invariant BEFORE flushing the native cache store.
for _, addr := range s.journal.sortedDirties() {
    ...
    store := s.keeper.GetState(s.ctx, obj.Address(), key)
    if store != origin && store != dirty {
        return fmt.Errorf("%w: ...", ErrStateConflict, ...)
    }
}
``` [1](#0-0) 

**Simulation path — `x/evm/keeper/state_transition.go` `ApplyMessageWithConfig()`, lines 601–623:**

When `commit=false` (the path taken by `eth_call` and `eth_estimateGas`), `stateDB.Commit()` is never called, so the conflict check is never reached. The code itself documents this gap:

```go
// Note: estimateGas and eth_call do not hit this path because commit is
// false for simulations, so they will succeed even when a real execution
// would produce a state conflict.
if errors.Is(err, statedb.ErrStateConflict) { ... }
``` [2](#0-1) 

**Simulation entry point — `x/evm/keeper/grpc_query.go` `EstimateGas()`, line 394:**

```go
rsp, err = k.ApplyMessageWithConfig(ctx, msg, cfg, false)  // commit=false
``` [3](#0-2) 

**Conflict trigger — `x/evm/statedb/statedb.go` `ExecuteNativeAction()`:**

Any precompile that calls `ExecuteNativeAction` and writes to an EVM storage slot that the outer EVM execution also writes to (with a different value) will produce `ErrStateConflict` at commit time. The native action's write is visible through `s.ctx` (the layered cache store), while the EVM's dirty value is tracked in `obj.dirtyStorage`. [4](#0-3) 

---

### Impact Explanation

When a user or DApp calls `eth_call` or `eth_estimateGas` for a transaction that involves a precompile writing to the same EVM storage slot as the outer EVM execution:

1. The simulation returns **success** (no conflict detected, `commit=false`).
2. The user submits the transaction with the estimated gas.
3. On-chain execution calls `stateDB.Commit()` with `commit=true`, detects the conflict, and returns `ErrStateConflict` as a `VmError` (status=0).
4. The transaction is included in the block with status=0; the ante handler has already deducted gas fees.
5. The user's EVM state changes are fully reverted, but **gas fees are permanently consumed**.

This matches the allowed High impact: *"Public JSON-RPC, gRPC, simulation, tracing, receipt/log, or indexer path feeds incorrect consensus-critical data into transaction execution"* — specifically, the simulation path returns a false-positive success that directly drives the user's decision to submit (and pay for) a transaction that will fail.

---

### Likelihood Explanation

The trigger requires a contract that uses a precompile implemented via `ExecuteNativeAction` (e.g., a bank, staking, or governance precompile) that writes to an EVM storage slot also written by the outer EVM call in the same transaction. This pattern is realistic for any precompile that manages per-account EVM state (e.g., a token precompile that mirrors balances into EVM storage). The entry path is fully unprivileged: any user can call `eth_estimateGas` or `eth_call` via the public JSON-RPC endpoint. [5](#0-4) 

---

### Recommendation

Run the conflict detection check even when `commit=false`. One approach: after EVM execution completes (but before discarding the stateDB), iterate the dirty storage and check for native-action conflicts using the same logic as `Commit()`. If a conflict is found, set `VmError = ErrStateConflict.Error()` in the returned `EVMResult` so that `eth_call` and `eth_estimateGas` accurately reflect the on-chain outcome.

---

### Proof of Concept

1. Deploy a precompile-backed contract `C` whose `foo()` function:
   - Calls a native precompile via `ExecuteNativeAction` that writes value `A` to EVM storage slot `S` of `C`.
   - Also executes `SSTORE S, B` (where `B ≠ A`) in the same EVM call frame.

2. Call `eth_estimateGas` for `C.foo()`:
   - `ApplyMessageWithConfig` is invoked with `commit=false`.
   - `stateDB.Commit()` is never called; no conflict is detected.
   - Response: `{ gas: <estimate> }` — **success**.

3. Submit the transaction with the returned gas estimate:
   - `ApplyMessageWithConfig` is invoked with `commit=true`.
   - `stateDB.Commit()` detects `store(S) = A ≠ origin` and `A ≠ B (dirty)`.
   - Returns `EVMResult{ VmError: "state conflict" }` — **status=0**.
   - Gas fees deducted by ante handler are **not refunded**.

4. Result: user paid gas for a transaction that `eth_estimateGas` predicted would succeed, but which fails on-chain — gas fees mis-accounted, state unchanged. [6](#0-5) [7](#0-6)

### Citations

**File:** x/evm/statedb/statedb.go (L41-44)
```go
// ErrStateConflict is returned by Commit() when an EVM-dirty storage key was also
// written by a nested native action (via ExecuteNativeAction). It is treated as an
// EVM-level failure (VmError / status=0) rather than a cosmos-level rejection.
var ErrStateConflict = errors.New("state conflict")
```

**File:** x/evm/statedb/statedb.go (L373-401)
```go
func (s *StateDB) ExecuteNativeAction(contract common.Address, converter EventConverter, action func(ctx sdk.Context) error) error {
	prevStore := s.cacheMS
	prevLayerCount := len(s.cacheLayers)

	nextStore, ok := s.cacheMS.CacheMultiStore().(cachemulti.Store)
	if !ok {
		panic("expect nested CacheMultiStore result to be cachemulti.Store")
	}

	eventManager := sdk.NewEventManager()
	actionCtx := s.ctx.WithMultiStore(nextStore).WithEventManager(eventManager)

	if err := action(actionCtx); err != nil {
		return err
	}

	s.cacheMS = nextStore
	s.cacheLayers = append(s.cacheLayers, nextStore)
	s.ctx = s.ctx.WithMultiStore(nextStore)

	events := eventManager.Events()
	s.emitNativeEvents(contract, converter, events)
	s.nativeEvents = s.nativeEvents.AppendEvents(events)
	s.journal.append(nativeChange{
		previousStore:      prevStore,
		previousLayerCount: prevLayerCount,
		events:             len(events),
	})
	return nil
```

**File:** x/evm/statedb/statedb.go (L758-788)
```go
	// Enforce the non-overlap invariant BEFORE flushing the native cache store.
	// A nested native action (via ExecuteNativeAction) commits its writes into s.cacheMS
	// (readable via s.ctx). If any EVM-dirty key was also written by such an action, the
	// store value visible through s.ctx will differ from originStorage. Detecting this
	// before flushing means we can abort cleanly — the parent context is never touched.
	//
	// Note: only EVM-dirty keys are checked; native-only writes have no EVM dirty bit
	// and are not in scope.
	for _, addr := range s.journal.sortedDirties() {
		obj, exist := s.stateObjects[addr]
		if !exist || obj.selfDestructed {
			continue
		}
		for _, key := range obj.dirtyStorage.SortedKeys() {
			origin := obj.originStorage[key]
			dirty := obj.dirtyStorage[key]
			if dirty == origin {
				continue
			}
			// A native action wrote this slot iff the store value differs from origin.
			// If it also differs from the EVM-dirty value, the two sides disagree — conflict.
			store := s.keeper.GetState(s.ctx, obj.Address(), key)
			if store != origin && store != dirty {
				return fmt.Errorf(
					"%w: address %s key %s modified by both EVM execution and native action (origin=%s, store=%s, dirty=%s)",
					ErrStateConflict,
					obj.Address().Hex(), key.Hex(), origin.Hex(), store.Hex(), dirty.Hex(),
				)
			}
		}
	}
```

**File:** x/evm/keeper/state_transition.go (L601-623)
```go
	// The dirty states in `StateDB` is either committed or discarded after return
	if commit {
		if err := stateDB.Commit(); err != nil {
			// A state conflict between the outer EVM and a nested native action is an
			// EVM-level failure: surface it as a VmError so the transaction is included
			// in the block with status=0 rather than rejected at the cosmos message level.
			// All other commit errors (infrastructure failures) remain cosmos-level errors.
			//
			// Note: estimateGas and eth_call do not hit this path because commit is
			// false for simulations, so they will succeed even when a real execution
			// would produce a state conflict.
			if errors.Is(err, statedb.ErrStateConflict) {
				return &types.EVMResult{
					GasUsed:          gasUsed,
					VmError:          statedb.ErrStateConflict.Error(),
					Hash:             cfg.TxConfig.TxHash.Hex(),
					BlockHash:        ctx.HeaderHash(),
					ExecutionGasUsed: temporaryGasUsed,
				}, nil
			}

			return nil, errorsmod.Wrap(err, "failed to commit stateDB")
		}
```

**File:** x/evm/keeper/grpc_query.go (L389-402)
```go
	executable := func(gas uint64) (vmError bool, rsp *types.EVMResult, err error) {
		// update the message with the new gas value
		msg.GasLimit = gas

		// pass false to not commit StateDB
		rsp, err = k.ApplyMessageWithConfig(ctx, msg, cfg, false)
		if err != nil {
			if errors.Is(err, core.ErrIntrinsicGas) {
				return true, nil, nil // Special case, raise gas limit
			}
			return true, nil, err // Bail out
		}
		return rsp.Failed(), rsp, nil
	}
```
