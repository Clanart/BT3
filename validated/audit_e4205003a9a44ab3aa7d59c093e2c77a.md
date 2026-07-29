### Title
Cached event-offset in shared `BalanceHandler` causes native/EVM balance desync on recursive precompile calls - (File: precompiles/common/balance_handler.go)

### Summary
The Compound "cached exchange rate" bug class is a general "compute/checkpoint against a stale cached value, then act against a fresher one" pattern. In Cosmos EVM, `precompiles/common/balance_handler.go` implements an analogous checkpoint/apply pattern: `BeforeBalanceChange` caches `prevEventsLen := len(ctx.EventManager().Events())`, and `AfterBalanceChange` replays only the events emitted after that cached offset to update `stateDB`. When precompile calls recurse (a precompile calling back into a contract that calls the same or another precompile again, all sharing one EVM execution frame), the single `BalanceHandler` instance's `prevEventsLen` field is overwritten by the inner call's `BeforeBalanceChange`, so the outer call's `AfterBalanceChange` replays the wrong event slice — causing the StateDB (EVM-visible) balance to diverge from the actual x/bank ledger balance.

### Finding Description
`BalanceHandler` is created once per precompile call site (via `BalanceHandlerFactory.NewBalanceHandler()`) and used to reconcile bank-module balance-changing events into the EVM `StateDB` so EVM code (via `SLOAD`/native balance opcodes) sees consistent balances immediately after a precompile-triggered bank operation [1](#0-0) .

The reconciliation logic is a two-phase "checkpoint then diff" design:
- `BeforeBalanceChange` records `prevEventsLen` as the cached checkpoint of the current event log length.
- `AfterBalanceChange` reads `events[bh.prevEventsLen:]` — i.e., relies on that cached checkpoint remaining valid until it's consumed — to determine which bank events to translate into `stateDB.AddBalance`/`SubBalance` calls [2](#0-1) .

This is structurally identical to the reported Compound bug: a value is cached at one point in time (`prevEventsLen`, analogous to a cached exchange rate) and used later to drive irreversible state mutation (`stateDB.AddBalance`/`SubBalance`), but if the underlying source of truth advances via another interleaved operation using the *same* `BalanceHandler` instance, the cached checkpoint becomes stale/wrong relative to the actual event stream that occurred.

The repository's own test suite documents this exact scenario as a known bug class: "tests the balance handler bug where recursive precompile calls share the same BalanceHandler instance, causing prevEventsLen to be overwritten. This leads to balance desync between native bank keeper and EVM stateDB." [3](#0-2) . The accompanying integration test drives a contract that recursively calls into a debug precompile via `callback(0)` and asserts on the resulting event count/composition [4](#0-3) .

### Impact Explanation
If a `BalanceHandler` instance is shared/reused across nested precompile invocations within one call stack (e.g., a precompile call that itself triggers a Solidity callback which invokes a bank-moving precompile again), the inner call's `BeforeBalanceChange` overwrites `prevEventsLen`. The outer call's subsequent `AfterBalanceChange` will then either:
- Re-process events already consumed by the inner call (double-crediting/double-debiting `stateDB` balances for the same underlying bank transfer), or
- Skip events that occurred between the outer checkpoint and the inner checkpoint (silently dropping balance updates from stateDB).

Either outcome breaks the 1:1 invariant between the x/bank ledger (source of truth) and the EVM `StateDB` view of balances that all EVM execution (transfers, `balanceOf`-equivalent state reads, subsequent `SubBalance`/`AddBalance` calls, self-destructs, etc.) depends on within the same transaction. Because `StateDB` balances are what get committed to state via `Commit()`/`commitWithCtx` at the end of transaction execution [5](#0-4) , a desync here can result in the EVM-side balance for an account being inflated (spendable-value duplication) or deflated relative to actual bank holdings — matching the "unauthorized duplication ... of spendable user value across ... EVM balances" and "irreversible accounting corruption" impact classes.

### Likelihood Explanation
Whether this is currently exploitable by an unprivileged user depends on two facts I could not fully verify from the indexed code:
1. Whether `BalanceHandler` instances are actually shared (the same object reused) across nested/recursive precompile calls in production precompiles (staking, distribution, bank, ICS20, erc20/werc20), as opposed to a fresh handler being instantiated per call via `BalanceHandlerFactory.NewBalanceHandler()` at each entry point.
2. Whether any of the shipped precompiles (as opposed to the test-only `debugprecompile`) actually support/trigger the recursive-call pattern reachable by an ordinary user (e.g., a Solidity contract that calls a precompile, which internally invokes `CallEVM` back into contract code that calls a precompile again).

The presence of a dedicated regression-style integration test (`BalanceHandlerTestSuite`) strongly suggests this bug was previously identified and is either fixed already or being actively guarded against; I could not confirm from the available index whether the fix is in place (e.g., whether call-sites now create a new `BalanceHandler` per precompile call, or use a stack instead of a single field) versus whether the test is demonstrating a still-open bug. I was not able to locate the call sites that construct/pass `BalanceHandlerFactory`/`BalanceHandler` instances per precompile invocation to confirm sharing behavior, due to index coverage limits.

### Recommendation
- Verify (or enforce) that a new `BalanceHandler` is instantiated per precompile invocation frame rather than reused/shared across nested/recursive calls, or replace the single `prevEventsLen int` field with a stack (push on `BeforeBalanceChange`, pop on `AfterBalanceChange`) so nested calls do not clobber each other's checkpoint.
- Add an invariant check that sums of `stateDB` balance deltas applied via `AfterBalanceChange` match the net bank-module balance deltas for the full precompile call (including nested calls), failing loudly (reverting) rather than silently drifting.
- Extend the existing `BalanceHandlerTestSuite`/`TestRecursivePrecompileCallsWithDebugPrecompile` test to assert on final EVM `StateDB` balances vs. x/bank balances after the recursive call, not just event counts, to catch silent desync rather than only structural event-count regressions.

### Proof of Concept
A precise PoC could not be constructed from the indexed code alone because the call sites that instantiate/share `BalanceHandler` per real (non-debug) precompile invocation were not found within the index. The existing repository test `TestRecursivePrecompileCallsWithDebugPrecompile` (`evmd/tests/integration/balance_handler/balance_handler_test.go:45-106`) demonstrates the reproduction pattern using a test-only debug precompile invoked recursively via a caller contract's `callback(0)` method, and is the closest available reproduction scaffold; a full PoC would require confirming a production precompile pathway (e.g., ICS20 transfer callback re-entering a bank-moving precompile) that shares one `BalanceHandler` instance across the recursive frames.

### Citations

**File:** precompiles/common/balance_handler.go (L30-48)
```go
func (bhf BalanceHandlerFactory) NewBalanceHandler() *BalanceHandler {
	return &BalanceHandler{
		bankKeeper:    bhf.bankKeeper,
		prevEventsLen: 0,
	}
}

// BalanceHandler is a struct that handles balance changes in the Cosmos SDK context.
type BalanceHandler struct {
	bankKeeper    BankKeeper
	prevEventsLen int
}

// BeforeBalanceChange is called before any balance changes by precompile methods.
// It records the current number of events in the context to later process balance changes
// using the recorded events.
func (bh *BalanceHandler) BeforeBalanceChange(ctx sdk.Context) {
	bh.prevEventsLen = len(ctx.EventManager().Events())
}
```

**File:** precompiles/common/balance_handler.go (L68-105)
```go
func (bh *BalanceHandler) AfterBalanceChange(ctx sdk.Context, stateDB *statedb.StateDB) error {
	events := ctx.EventManager().Events()

	for _, event := range events[bh.prevEventsLen:] {
		switch event.Type {
		case banktypes.EventTypeCoinSpent:
			spenderAddr, err := ParseAddress(event, banktypes.AttributeKeySpender)
			if err != nil {
				return fmt.Errorf("failed to parse spender address from event %q: %w", banktypes.EventTypeCoinSpent, err)
			}
			if bh.bankKeeper.BlockedAddr(spenderAddr) {
				// Bypass blocked addresses
				continue
			}

			amount, err := ParseAmount(event)
			if err != nil {
				return fmt.Errorf("failed to parse amount from event %q: %w", banktypes.EventTypeCoinSpent, err)
			}

			stateDB.SubBalance(common.BytesToAddress(spenderAddr.Bytes()), amount, tracing.BalanceChangeUnspecified)

		case banktypes.EventTypeCoinReceived:
			receiverAddr, err := ParseAddress(event, banktypes.AttributeKeyReceiver)
			if err != nil {
				return fmt.Errorf("failed to parse receiver address from event %q: %w", banktypes.EventTypeCoinReceived, err)
			}
			if bh.bankKeeper.BlockedAddr(receiverAddr) {
				// Bypass blocked addresses
				continue
			}

			amount, err := ParseAmount(event)
			if err != nil {
				return fmt.Errorf("failed to parse amount from event %q: %w", banktypes.EventTypeCoinReceived, err)
			}

			stateDB.AddBalance(common.BytesToAddress(receiverAddr.Bytes()), amount, tracing.BalanceChangeUnspecified)
```

**File:** evmd/tests/integration/balance_handler/balance_handler_test.go (L23-25)
```go
// BalanceHandlerTestSuite tests the balance handler bug where recursive precompile calls
// share the same BalanceHandler instance, causing prevEventsLen to be overwritten.
// This leads to balance desync between native bank keeper and EVM stateDB.
```

**File:** evmd/tests/integration/balance_handler/balance_handler_test.go (L76-102)
```go
	// Pack the input for callback(0)
	input, err := callerContract.ABI.Pack("callback", big.NewInt(0))
	s.Require().NoError(err)

	// Fund Contract
	err = evmApp.GetBankKeeper().SendCoins(ctx, s.chain.SenderAccounts[0].SenderAccount.GetAddress(), callerAddr.Bytes(), types.NewCoins(types.NewCoin("aatom", sdkmath.NewInt(10000000))))
	s.Require().NoError(err)

	res, _, _, err := s.chain.SendEvmTx(
		s.chain.SenderAccounts[0],
		0,             // sender index
		callerAddr,    // to address
		big.NewInt(0), // value
		input,         // data
		0,             // gas price multiplier
	)
	s.Require().NoError(err, "callback transaction should succeed")
	s.Require().False(res.IsErr(), "callback should not fail: %s", res.Events)

	s.Require().Equal(len(res.Events), 15, "callback should have 15 events")
	debug_count := 0
	for _, event := range res.Events {
		if event.Type == "debug_precompile" {
			debug_count++
		}
	}
	s.Require().Equal(10, debug_count, "callback should have 1 debug precompile")
```

**File:** x/vm/statedb/statedb.go (L695-745)
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

// CommitWithCacheCtx writes the dirty states to keeper using the cacheCtx.
// This function is used before any precompile call to make sure the cacheCtx
// is updated with the latest changes within the tx (StateDB's journal entries).
func (s *StateDB) CommitWithCacheCtx() error {
	return s.commitWithCtx(s.cacheCtx)
}

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
