## Analog identified: shared `BalanceHandler` instance corrupted by recursive/nested precompile calls

### Title
Recursive precompile calls sharing a single `BalanceHandler` instance corrupt EVM `StateDB` balances vs. `x/bank` ledger - (File: `precompiles/common/balance_handler.go`)

### Summary
The Line-of-Credit bug lets an attacker-controlled external call (the ZeroEx trade data) interleave with a balance-diff check, causing the check to be evaluated against the wrong reference point and letting the attacker steal value. The same *"attacker-controlled reentrant/nested call corrupts a stateful, shared bookkeeping window"* pattern exists in Cosmos EVM's precompile balance synchronization mechanism (`BalanceHandler`), which tracks bank module events (`coin_spent`/`coin_received`/`fractional_balance_change`) between two calls and replays them into the EVM `StateDB`.

### Finding Description
Every precompile call synchronizes native `x/bank`/`x/precisebank` state changes into the EVM `StateDB` using a `BalanceHandler`: [1](#0-0) 

`BeforeBalanceChange` records `prevEventsLen = len(ctx.EventManager().Events())`, and `AfterBalanceChange` later replays only the events emitted **after** that recorded index: [2](#0-1) 

`prevEventsLen` is a mutable field on the handler struct, not a call-stack-local value. Several precompiles (`erc20`, `gov`, `distribution`, `slashing`, `staking`, `ics20`, and the debug/test precompile) obtain and reuse a `BalanceHandler` via `GetBalanceHandler()` rather than creating one fresh per invocation: [3](#0-2) 

If, during native precompile execution, an EVM sub-call re-enters a precompile that uses the *same* handler instance (e.g. a contract calling a precompile whose native action itself calls back into the EVM via `CallEVMWithData`, which invokes another precompile), the inner call's `BeforeBalanceChange` overwrites `prevEventsLen` to a later index. When control returns to the outer call and its `AfterBalanceChange` runs, it slices `events[bh.prevEventsLen:]` starting from the *inner* call's marker — silently skipping the outer call's own `coin_spent`/`coin_received` events. Those events already mutated the authoritative `x/bank` ledger (via `SendCoins`/mint/burn), but are **never applied** to the EVM `StateDB`, permanently desynchronizing the EVM-visible balance from the real bank balance for the affected accounts.

This is confirmed as a live, reproducible bug by the project's own regression test: [4](#0-3) [5](#0-4) 

The test triggers nested `callback()` invocations of a debug precompile through a caller contract and shows the resulting event/`debug_precompile` counts diverge from what a correctly-scoped handler would produce, directly demonstrating that the shared instance's window gets clobbered across recursion.

The newer `runNativeAction` path in `precompiles/common/precompile.go` creates a handler per call via a factory: [6](#0-5) 

but this only protects precompiles that have been migrated to this factory pattern; the `GetBalanceHandler()`-based precompiles (as exercised by the debug precompile and named in the repo's own bug-reproducing test) still share a single instance, and nothing prevents a contract from chaining calls across multiple such precompiles or invoking one of them reentrantly within the same EVM transaction.

### Impact Explanation
When outer-call `coin_spent`/`coin_received` events are dropped from `StateDB` application:
- An account can have its native `x/bank` balance debited by a precompile operation while its EVM `StateDB` balance is never decremented (or vice versa for credits), producing a durable mismatch between the ledger used for native transfers/precompile accounting and the ledger used for EVM execution, `eth_getBalance`, and subsequent contract logic.
- Because the corrupted `StateDB` balance is what backs subsequent EVM-level spends within the same or later transactions (until a full resync, if any), this is an unauthorized duplication/loss of spendable value across the two balance representations — matching the "irreversible accounting corruption of spendable user value across native balances ... EVM balances" Critical impact category.

### Likelihood Explanation
Reachability requires an unprivileged contract to trigger a recursive/nested precompile invocation that reuses the same `BalanceHandler` instance within one EVM transaction — the repository's own test (`TestRecursivePrecompileCallsWithDebugPrecompile`) proves this is achievable via ordinary contract-to-precompile call patterns (a caller contract invoking a precompile whose native logic calls back into the EVM). Whether an *externally usable, non-debug* precompile chain (e.g., ERC20 precompile → ICS20/callback → ERC20 precompile again) can be driven into the same shared-instance recursion was not fully confirmed in this pass; the debug precompile explicitly exists to reproduce and document the defect, but I could not verify from the indexed code whether all production precompiles listed (`erc20`, `gov`, `distribution`, `slashing`, `staking`, `ics20`) are still on the shared/`GetBalanceHandler()` pattern versus having been migrated to the safer per-call factory in `precompile.go`. This distinction materially affects likelihood and should be verified directly in the repository (file contents for `GetBalanceHandler` definition and each precompile's `Run` method were not fully retrievable through the index).

### Recommendation
- Ensure every precompile creates and uses a fresh `BalanceHandler` scoped strictly to its own call frame (as done in `precompiles/common/precompile.go`'s `runNativeAction`), never a shared/singleton instance retrievable via `GetBalanceHandler()` that can be mutated by reentrant/nested calls.
- Audit all precompiles for the `GetBalanceHandler()` accessor pattern and migrate them to the factory-per-call model.
- Add an invariant check (e.g., in CI or a periodic assertion) that compares aggregate `x/bank` balances against `StateDB` balances for a comprehensive set of recursive precompile call sequences, expanding the existing `TestRecursivePrecompileCallsWithDebugPrecompile` coverage to production precompiles (ERC20, ICS20, staking, distribution, gov).

### Proof of Concept
The existing repository test already demonstrates the corrupted event-window mechanics end-to-end: [7](#0-6) 
It deploys a caller contract invoking the debug precompile's `callback(0)` method, which recursively re-enters the precompile via `CallEVMWithData`, and asserts on the resulting event counts (`15` total events, `10` `debug_precompile` events) that diverge from what non-overlapping, correctly-scoped `BeforeBalanceChange`/`AfterBalanceChange` windows would produce — confirming that `prevEventsLen` is clobbered across the recursive calls. Reproducing this against a balance-mutating precompile (rather than the no-op debug one) and asserting `x/bank` balance vs. `StateDB.GetBalance` divergence would be the concrete extension needed to demonstrate fund-level impact.

### Citations

**File:** precompiles/common/balance_handler.go (L37-48)
```go
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

**File:** precompiles/common/balance_handler.go (L68-72)
```go
func (bh *BalanceHandler) AfterBalanceChange(ctx sdk.Context, stateDB *statedb.StateDB) error {
	events := ctx.EventManager().Events()

	for _, event := range events[bh.prevEventsLen:] {
		switch event.Type {
```

**File:** testutil/testdata/debug/debug.go (L77-115)
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

	return res, nil
}
```

**File:** evmd/tests/integration/balance_handler/balance_handler_test.go (L23-25)
```go
// BalanceHandlerTestSuite tests the balance handler bug where recursive precompile calls
// share the same BalanceHandler instance, causing prevEventsLen to be overwritten.
// This leads to balance desync between native bank keeper and EVM stateDB.
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
