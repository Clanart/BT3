### Title
Recursive precompile `BalanceHandler` reuses stale event boundary causing StateDB/native balance desync - (File: precompiles/common/balance_handler.go)

### Summary
The `BalanceHandler` used by stateful precompiles records a single `prevEventsLen` integer at the start of a precompile call and later processes all events from that index to the current length. When precompile calls are nested (re-entrant or recursive), the same `BalanceHandler` instance—or the outer call's instance—processes events already handled by inner calls, causing balance changes to be double-applied or skipped in the EVM `StateDB`. This desynchronizes EVM account balances from native `x/bank`/`x/precisebank` state, enabling unauthorized minting, burning, or duplication of value across native balances, EVM balances, and precompile-mediated assets.

### Finding Description
`BalanceHandler.BeforeBalanceChange` stores the current event-manager length in one mutable field: [1](#0-0) 

`BalanceHandler.AfterBalanceChange` then iterates `events[prevEventsLen:]` and applies `stateDB.SubBalance`/`AddBalance` for every `coin_spent`, `coin_received`, and `fractional_balance_change` event: [2](#0-1) 

The base `Precompile.runNativeAction` creates one `BalanceHandler` per call, calls `BeforeBalanceChange` before the action, and `AfterBalanceChange` after: [3](#0-2) [4](#0-3) 

Because the event manager is shared and `prevEventsLen` is a single scalar, an outer precompile's `AfterBalanceChange` runs after nested inner precompiles return and re-processes the inner events. The debug precompile makes the issue explicit by using one shared `BalanceHandler` instance whose `prevEventsLen` is overwritten on re-entry: [5](#0-4) [6](#0-5) 

The integration test suite explicitly documents this as a balance-desync bug: [7](#0-6) 

### Impact Explanation
Critical accounting corruption. Native bank transfers executed inside precompiles are recorded correctly in `x/bank`/`x/precisebank`, but the EVM `StateDB` balance is updated incorrectly because the same events are applied multiple times (or, in reverse edge cases, skipped). When `StateDB.Commit()` writes dirty stateObjects back through `commitWithCtx`: [8](#0-7) [9](#0-8) 

the EVM-visible balance diverges from the native ledger. An attacker can exploit an inflated `StateDB` balance to transfer or withdraw value that is not backed by native bank state, satisfying the Critical impact gate for unauthorized duplication, minting, or theft of user funds across native balances, EVM balances, ERC20 representations, and precompile-mediated assets.

### Likelihood Explanation
Reachable through ordinary EVM transaction flow. Any stateful precompile whose `NativeAction` calls back into the EVM (for example via `evmKeeper.CallEVM`) or is invoked recursively can trigger nested precompile calls. The base `Precompile.runNativeAction` does not isolate event ranges per nesting level, so the flaw is inherent to the precompile execution framework. The existing integration test reproduces the desync with recursive debug precompile calls.

### Recommendation
Make `BalanceHandler` re-entrant safe. Track a stack of event-boundary snapshots (one entry per nesting level) and have each `AfterBalanceChange` pop only the events emitted since its own `BeforeBalanceChange`. Alternatively, have nested precompile calls operate on a fresh event manager/snapshot and reconcile only their own emitted events with `StateDB`.

### Proof of Concept
The integration test `TestRecursivePrecompileCallsWithDebugPrecompile` in `evmd/tests/integration/balance_handler/balance_handler_test.go` deploys a caller contract that triggers recursive debug precompile calls and asserts the resulting event count, demonstrating the shared-handler/desync behavior.

A conceptual exploit:
1. Attacker deploys a contract that calls precompile A, whose `NativeAction` calls back into the EVM and invokes precompile B (or calls A recursively).
2. Precompile A's `BalanceHandler` records `prevEventsLen = 0`.
3. Precompile B's action transfers 100 units and emits `CoinSpent(A, 100)` and `CoinReceived(B, 100)`.
4. Precompile B's `AfterBalanceChange` updates `StateDB`: A -= 100, B += 100.
5. Control returns to precompile A. Precompile A's `AfterBalanceChange` processes the same events again: A -= 100, B += 100.
6. `StateDB` now credits B with +200 and debits A with -200, while `x/bank` only moved 100.
7. Attacker uses B's inflated EVM balance in a transfer or ERC20 conversion to extract value, leaving the native ledger under-collateralized.

### Citations

**File:** precompiles/common/balance_handler.go (L46-48)
```go
func (bh *BalanceHandler) BeforeBalanceChange(ctx sdk.Context) {
	bh.prevEventsLen = len(ctx.EventManager().Events())
}
```

**File:** precompiles/common/balance_handler.go (L68-71)
```go
func (bh *BalanceHandler) AfterBalanceChange(ctx sdk.Context, stateDB *statedb.StateDB) error {
	events := ctx.EventManager().Events()

	for _, event := range events[bh.prevEventsLen:] {
```

**File:** precompiles/common/precompile.go (L99-106)
```go
	var balanceHandler *BalanceHandler
	if p.BalanceHandlerFactory != nil {
		balanceHandler = p.BalanceHandlerFactory.NewBalanceHandler()
	}

	if balanceHandler != nil {
		balanceHandler.BeforeBalanceChange(ctx)
	}
```

**File:** precompiles/common/precompile.go (L119-123)
```go
	if balanceHandler != nil {
		if err := balanceHandler.AfterBalanceChange(ctx, stateDB); err != nil {
			return nil, err
		}
	}
```

**File:** testutil/testdata/debug/debug.go (L78-78)
```go
	p.GetBalanceHandler().BeforeBalanceChange(ctx)
```

**File:** testutil/testdata/debug/debug.go (L110-112)
```go
	if err := p.GetBalanceHandler().AfterBalanceChange(ctx, stateDB); err != nil {
		return nil, err
	}
```

**File:** evmd/tests/integration/balance_handler/balance_handler_test.go (L23-25)
```go
// BalanceHandlerTestSuite tests the balance handler bug where recursive precompile calls
// share the same BalanceHandler instance, causing prevEventsLen to be overwritten.
// This leads to balance desync between native bank keeper and EVM stateDB.
```

**File:** x/vm/statedb/statedb.go (L695-704)
```go
// Commit writes the dirty states to keeper
// the StateDB object should be discarded after committed.
func (s *StateDB) Commit() error {
	// writeCache func will exist only when there's a call to a precompile.
	// It applies all the store updates preformed by precompile calls.
	if s.writeCache != nil {
		s.writeCache()
	}
	return s.commitWithCtx(s.ctx)
}
```

**File:** x/vm/statedb/statedb.go (L713-745)
```go
// commitWithCtx writes the dirty states to keeper
// using the provided context
func (s *StateDB) commitWithCtx(ctx sdk.Context) error {
	for _, addr := range s.journal.sortedDirties() {
		obj := s.stateObjects[addr]
		if obj.selfDestructed {
			if err := s.keeper.DeleteAccount(ctx, obj.Address()); err != nil {
				return errorsmod.Wrapf(err, "failed to delete account %s", obj.Address())
			}
		} else {
			if obj.code != nil && obj.dirtyCode {
				if len(obj.code) == 0 {
					s.keeper.DeleteCode(ctx, obj.CodeHash())
				} else {
					s.keeper.SetCode(ctx, obj.CodeHash(), obj.code)
				}
			}
			if err := s.keeper.SetAccount(ctx, obj.Address(), obj.account); err != nil {
				return errorsmod.Wrap(err, "failed to set account")
			}

			for _, key := range obj.dirtyStorage.SortedKeys() {
				valueBytes := obj.dirtyStorage[key].Bytes()
				if len(valueBytes) == 0 {
					s.keeper.DeleteState(ctx, obj.Address(), key)
				} else {
					s.keeper.SetState(ctx, obj.Address(), key, valueBytes)
				}
			}
		}
	}
	return nil
}
```
