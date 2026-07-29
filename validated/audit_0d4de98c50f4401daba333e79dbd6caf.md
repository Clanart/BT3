### Title
Balance desync between EVM StateDB and native bank state in recursive/nested precompile calls via `BalanceHandler.prevEventsLen` - (File: precompiles/common/balance_handler.go)

### Summary
`BalanceHandler` tracks bank events emitted between `BeforeBalanceChange` and `AfterBalanceChange` to translate native `x/bank`/`x/precisebank` balance changes into `StateDB.AddBalance`/`SubBalance` calls, so that Solidity-visible balances stay consistent with the underlying Cosmos coin ledger. This is structurally analogous to the Hats Protocol `checkAfterExecution` bug: an invariant-preserving reconciliation step relies on a mutable "window" marker (`prevEventsLen`, analogous to the recomputed threshold) that is not safely scoped/snapshotted across reentrant/nested execution, so it can be silently overwritten mid-flight by intermediate calls, causing the reconciliation to apply the wrong delta.

### Finding Description
Each `Precompile.RunNativeAction` call is intended to bracket a single precompile invocation with `BeforeBalanceChange`/`AfterBalanceChange` to sync bank-keeper events into the EVM `StateDB` [1](#0-0) . `BeforeBalanceChange` simply records the current event count as `bh.prevEventsLen` [2](#0-1) , and `AfterBalanceChange` replays only `events[bh.prevEventsLen:]` to apply balance deltas to the `StateDB` [3](#0-2) .

The repository itself contains a dedicated regression test explicitly describing this as a bug: *"BalanceHandlerTestSuite tests the balance handler bug where recursive precompile calls share the same BalanceHandler instance, causing prevEventsLen to be overwritten. This leads to balance desync between native bank keeper and EVM stateDB."* [4](#0-3) . If a precompile call (e.g., staking, distribution, gov, ICS20, ERC20 precompiles all instantiate a `BalanceHandler` per call via `BalanceHandlerFactory` [5](#0-4) ) triggers, directly or indirectly, another precompile call before the outer call's `AfterBalanceChange` runs (e.g. via a Solidity contract making a nested/recursive call into the same or another precompile, or via callback-style flows), a shared/aliased `BalanceHandler` instance can have `prevEventsLen` advanced by the inner call's `BeforeBalanceChange`. When the outer call's `AfterBalanceChange` subsequently runs, it will only see events emitted after the *inner* call's marker — silently dropping or mis-attributing the earlier balance-change events. This is exactly the "computed check that can be moved mid-flight" pattern from the Hats bug: the reconciliation window is a shared mutable value recomputed relative to *current* state rather than a properly scoped/independent snapshot per call.

### Impact Explanation
If exploitable in a general (non-test-harness) precompile call path, this could cause the EVM `StateDB`'s view of an account's native-token balance to diverge from the true `x/bank`/`x/precisebank` balance — i.e., either under-crediting (fund loss visibility) or over-crediting (duplication of spendable value visible to the EVM) a balance that is not backed by actual bank state. Because `StateDB.Commit()` persists these balances back into the account balance store [6](#0-5) , an over-credit would let an attacker create EVM-spendable balance not backed by underlying bank coins, and an under-credit could permanently strand user funds recorded in the bank layer but invisible/inaccessible via EVM calls. This maps to the "unauthorized minting/duplication" or "permanent freezing/loss of spendable value" Critical impact categories.

### Likelihood Explanation
The precise reachability from an *unprivileged* external caller is not confirmed with the tools available. The bug is demonstrated only via a purpose-built `debug` test precompile with a `callback` recursion path in `evmd/tests/testdata/debug/debug.go` and `contracts/debug` test helpers, not shown to be triggerable through any of the production precompiles (staking, distribution, gov, slashing, erc20, ics20) with a normal Solidity call pattern. Given `RunNativeAction` creates a *new* `BalanceHandler` per call via `p.BalanceHandlerFactory.NewBalanceHandler()` [7](#0-6) , ordinary sequential precompile calls should get independent instances; the sharing/aliasing condition needed to reproduce the bug (a single `BalanceHandler` object reused across a recursive/reentrant call boundary) was only confirmed in the test harness, and I could not verify within the available context that any shipped, non-test precompile holds and reuses a `BalanceHandler` instance across nested calls in a way reachable by a normal user transaction. This is a real, repository-acknowledged defect (there's an explicit regression test for it), but I cannot confirm from the indexed code that it is exploitable through the production precompile surface rather than only through the internal debug/test precompile construct, so I cannot assert Critical-severity exploitability with certainty.

### Recommendation
Scope `BalanceHandler` state per call frame rather than as a field that could be shared/reused across reentrant precompile invocations: e.g., snapshot/restore `prevEventsLen` around every nested precompile invocation (push/pop a stack of markers), or make each `RunNativeAction` invocation strictly own an independent `BalanceHandler` instance that is never referenced by an outer, still-in-flight call. Add invariant checks (e.g., comparing the sum of `StateDB` balance deltas against the actual bank balance delta for the touched accounts before `Commit()`) so any residual desync fails the transaction rather than silently persisting.

### Proof of Concept
The repository's own test demonstrates the mechanism: it registers a debug precompile with recursive `callback` calls and shows the resulting event/state counts diverge from what would be expected if each call's balance reconciliation were properly isolated [8](#0-7) . Reproducing this against a production precompile call chain (rather than the debug precompile) to demonstrate concrete over/under-crediting of a real user balance was not verified within the scope of this analysis — a Devin session with full repository/tooling access would be needed to construct and run such a PoC against the staking/distribution/erc20 precompiles.

### Citations

**File:** precompiles/common/precompile.go (L99-123)
```go
	var balanceHandler *BalanceHandler
	if p.BalanceHandlerFactory != nil {
		balanceHandler = p.BalanceHandlerFactory.NewBalanceHandler()
	}

	if balanceHandler != nil {
		balanceHandler.BeforeBalanceChange(ctx)
	}

	bz, err = action(ctx)
	if err != nil {
		return bz, err
	}

	cost := ctx.GasMeter().GasConsumed() - initialGas

	if !contract.UseGas(cost, nil, tracing.GasChangeCallPrecompiledContract) {
		return nil, vm.ErrOutOfGas
	}

	if balanceHandler != nil {
		if err := balanceHandler.AfterBalanceChange(ctx, stateDB); err != nil {
			return nil, err
		}
	}
```

**File:** precompiles/common/balance_handler.go (L43-48)
```go
// BeforeBalanceChange is called before any balance changes by precompile methods.
// It records the current number of events in the context to later process balance changes
// using the recorded events.
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

**File:** evmd/tests/integration/balance_handler/balance_handler_test.go (L23-26)
```go
// BalanceHandlerTestSuite tests the balance handler bug where recursive precompile calls
// share the same BalanceHandler instance, causing prevEventsLen to be overwritten.
// This leads to balance desync between native bank keeper and EVM stateDB.
type BalanceHandlerTestSuite struct {
```

**File:** evmd/tests/integration/balance_handler/balance_handler_test.go (L43-106)
```go
// TestRecursivePrecompileCallsWithDebugPrecompile demonstrates the balance handler bug
// by triggering recursive calls that share the same BalanceHandler instance.
func (s *BalanceHandlerTestSuite) TestRecursivePrecompileCallsWithDebugPrecompile() {
	evmApp := s.chain.App.(evm.EvmApp)
	ctx := s.chain.GetContext()

	// Create and register debug precompile
	debugPrec := debugprecompile.NewPrecompile(evmApp.GetBankKeeper(), evmApp.GetEVMKeeper())
	// Set the precompile directly in the EVM keeper's precompile map
	evmApp.GetEVMKeeper().RegisterStaticPrecompile(debugPrec.Address(), debugPrec)
	err := evmApp.GetEVMKeeper().EnableStaticPrecompiles(ctx, debugPrec.Address())
	s.Require().NoError(err)

	a, b, c := evmApp.GetEVMKeeper().GetPrecompileInstance(ctx, debugPrec.Address())
	fmt.Println(a, b, c)

	// Deploy caller contract
	callerContract, err := contracts.LoadDebugPrecompileCaller()
	s.Require().NoError(err)

	deploymentData := testutiltypes.ContractDeploymentData{
		Contract:        callerContract,
		ConstructorArgs: []interface{}{},
	}

	// Use local helper function
	callerAddr, err := DeployContract(s.T(), s.chain, deploymentData)
	s.Require().NoError(err)
	s.chain.NextBlock()

	s.T().Logf("Deployed caller contract at %s", callerAddr.Hex())
	s.T().Logf("Debug precompile at %s", debugPrec.Address().Hex())

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

	// Advance to next block to finalize state
	s.chain.NextBlock()
}
```

**File:** precompiles/staking/staking.go (L1-1)
```go
package staking
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
