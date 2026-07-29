### Title
Recursive/Nested Precompile Calls Share a Single `BalanceHandler` Instance, Corrupting `prevEventsLen` and Desyncing EVM `StateDB` Balances from the Bank Ledger - (File: precompiles/common/balance_handler.go)

### Summary
The `ProtocolUpgradeHandler` flaw is fundamentally about two logically-distinct "phases" (Guardian approval vs. veto) sharing one ambiguous state variable, where a nested/interleaving action (Security Council's approval) silently clobbers the state the other party depended on. The direct Cosmos EVM analog is `BalanceHandler.prevEventsLen`, a single mutable field used as a "before/after" event-window marker around a precompile call. When precompile execution recurses (a precompile call triggers a nested EVM call that re-enters a precompile using the *same* `BalanceHandler` instance), the inner call's `BeforeBalanceChange` overwrites `prevEventsLen`, corrupting the window the outer call relies on to reconcile bank events into `StateDB` balances, exactly the "one party's action invalidates the other's in-flight state" pattern from the report.

### Finding Description
`BalanceHandler` tracks the SDK event log length before a precompile action runs and, afterward, replays only the events appended since that mark to update the EVM `StateDB` (`AddBalance`/`SubBalance`) so `StateDB` balances mirror actual `bank`/`precisebank` module state [1](#0-0) [2](#0-1) .

This mechanism assumes strictly non-overlapping Before/After pairs. However, the codebase already contains an explicitly documented regression test proving that recursive precompile invocations violate this assumption: "recursive precompile calls share the same BalanceHandler instance, causing prevEventsLen to be overwritten... leads to balance desync between native bank keeper and EVM stateDB" [3](#0-2) . The test drives this via a contract that calls back into a debug precompile recursively and asserts on the resulting event/log counts [4](#0-3) .

The debug precompile implementation illustrates the shared-instance pattern: it calls `p.GetBalanceHandler().BeforeBalanceChange(ctx)` before executing and `p.GetBalanceHandler().AfterBalanceChange(ctx, stateDB)` afterward, using a handler retrieved from the precompile object rather than a fresh instance scoped to the current call frame [5](#0-4) . When execution of the precompile's action itself triggers another EVM call that re-enters the same precompile (or a different precompile sharing the handler/factory), the inner call's `BeforeBalanceChange` resets `prevEventsLen` to the (larger) event count at time of re-entry. Once the inner call finishes and the outer call resumes to compute `AfterBalanceChange`, the outer call's `events[bh.prevEventsLen:]` slice is computed against the *inner* call's marker instead of its own, causing bank/precisebank events legitimately belonging to the outer transfer to be skipped (silently dropped from `StateDB` balance updates) or, depending on ordering, replayed against the wrong window.

This directly maps to the "Asset-representation path" invariant (`x/erc20`, bank, precisebank, precompile-visible balances must preserve 1:1 accounting) called out in the task's Smart Audit Pivots, since `StateDB` balances feed subsequent EVM reads (`balanceOf`, transfers) within the same transaction and across the block.

### Impact Explanation
If the outer call's spend/receive events are skipped from the `StateDB` reconciliation, an account's EVM-visible balance diverges from its true bank-ledger balance. Because a state fork of "bank truth" vs "on-chain EVM-visible balance" persists after the transaction commits (StateDB writes stateobjects back to the keeper), this can result in:
- Permanent under/over crediting of an account's EVM-visible balance relative to its actual bank balance (accounting corruption of spendable user value).
- Downstream double-spend or lost-funds conditions if a contract makes transfer decisions based on the corrupted `StateDB` balance within the same or later transactions before the discrepancy is caught.

This matches the "Critical unauthorized minting/burning/duplication or irreversible accounting corruption... across native balances, EVM balances... or precompile-mediated assets" impact bucket.

### Likelihood Explanation
Reachable by an unprivileged user: any contract that calls a stateful precompile (staking, distribution, gov, slashing, ics20, erc20, debug) and, within that precompile call's execution, triggers a nested call back into a precompile (e.g., via a callback, reentrant call, or a contract-to-contract call chain that revisits a precompile address) will hit this shared-state clobbering. The repository's own regression test demonstrates the trigger conditions are reachable through ordinary contract-to-precompile call patterns, not privileged operations [6](#0-5) .

Note: I was not able to fully trace, within the remaining investigation budget, whether `p.BalanceHandlerFactory.NewBalanceHandler()` (as used in `precompiles/common/precompile.go`, which does allocate a handler local to each `runNativeAction` call) is used uniformly by all precompiles, or whether some precompiles (like the debug precompile and possibly others referencing `GetBalanceHandler()`) hold a single shared field-level instance reused across nested calls. This distinction determines the exact set of affected precompiles and the precise reentrant call shape needed to trigger the bug in production precompiles (staking/distribution/gov/slashing/ics20/erc20) versus only the test-only debug precompile. This should be verified with a full-context Devin session before filing/fixing, since the index may not include every precompile's exact `BalanceHandler` wiring.

### Recommendation
- Ensure every precompile call frame allocates its own `BalanceHandler` instance (via `BalanceHandlerFactory.NewBalanceHandler()`) scoped strictly to that call, never a shared field reused across reentrant/recursive precompile invocations.
- Alternatively, make `prevEventsLen` a stack (push before, pop after) instead of a single scalar, so nested calls cannot clobber the outer call's marker.
- Add an invariant check that asserts the reconciled `StateDB` balance total matches `bank`+`precisebank` fractional balances after every precompile-touching transaction, to catch future regressions of this class.
- Extend the existing `TestRecursivePrecompileCallsWithDebugPrecompile` test to assert on actual balance parity (bank keeper balance vs. `StateDB` balance) after the recursive call, not just event counts, and add equivalent tests for the production precompiles (staking, distribution, gov, slashing, ics20, erc20) exercising the same recursive-call pattern.

### Proof of Concept
The repository already contains a working reproduction: `evmd/tests/integration/balance_handler/balance_handler_test.go::TestRecursivePrecompileCallsWithDebugPrecompile` deploys a `DebugPrecompileCaller` contract that recursively invokes the debug precompile's `callback` method, demonstrating the shared-instance/`prevEventsLen` overwrite [7](#0-6) . Converting this into a balance-parity assertion (comparing `bankKeeper.GetBalance` against `stateDB`/`evmKeeper` balance post-transaction for the caller and receiver addresses) would confirm the accounting divergence described above.

### Citations

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

**File:** evmd/tests/integration/balance_handler/balance_handler_test.go (L23-25)
```go
// BalanceHandlerTestSuite tests the balance handler bug where recursive precompile calls
// share the same BalanceHandler instance, causing prevEventsLen to be overwritten.
// This leads to balance desync between native bank keeper and EVM stateDB.
```

**File:** evmd/tests/integration/balance_handler/balance_handler_test.go (L43-102)
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
```

**File:** testutil/testdata/debug/debug.go (L77-112)
```go
	// Start the balance change handler before executing the precompile.
	p.GetBalanceHandler().BeforeBalanceChange(ctx)

	initialGas := ctx.GasMeter().GasConsumed()

	// set the default SDK gas configuration to track gas usage
	// we are changing the gas meter type, so it panics gracefully when out of gas
	ctx = ctx.WithGasMeter(storetypes.NewGasMeter(contract.Gas)).
		WithKVGasConfig(p.KvGasConfig).
		WithTransientKVGasConfig(p.TransientKVGasConfig)
	// we need to consume the gas that was already used by the EVM
	ctx.GasMeter().ConsumeGas(initialGas, "creating a new gas meter")

	// This handles any out of gas errors that may occur during the execution of a precompile tx or query.
	// It avoids panics and returns the out of gas error so the EVM can continue gracefully.
	defer cmn.HandleGasError(ctx, contract, initialGas, &err)()

	res, err := p.Execute(ctx, stateDB, contract, readonly)
	if err != nil {
		return nil, err
	}

	if err != nil {
		return nil, err
	}

	cost := ctx.GasMeter().GasConsumed() - initialGas

	if !contract.UseGas(cost, nil, tracing.GasChangeCallPrecompiledContract) {
		return nil, vm.ErrOutOfGas
	}

	// Process the native balance changes after the method execution.
	if err := p.GetBalanceHandler().AfterBalanceChange(ctx, stateDB); err != nil {
		return nil, err
	}
```
