### Title
Recursive precompile calls share a single `BalanceHandler` instance, overwriting `prevEventsLen` and desyncing EVM `StateDB` balances from the native bank ledger - (File: `precompiles/common/balance_handler.go`, `testutil/testdata/debug/debug.go`)

### Summary
The Aave report's core defect pattern is that a value used to compute a critical accounting result (`liquidityAdded`) is captured *before* a state-changing external transfer that should have been reflected in it, producing a stale/incorrect accounting input. The matching pattern in this repository is the `BalanceHandler` used by stateful precompiles: it records the event-log length (`prevEventsLen`) as a "before" marker and later diffs events from that marker to reconstruct bank-driven balance changes into the EVM `StateDB`. When a precompile recursively/re-entrantly invokes itself (or another precompile sharing the same handler instance) within a single EVM call, the second `BeforeBalanceChange` overwrites `prevEventsLen`, corrupting the window used to compute which bank events belong to which call.

### Finding Description
`BalanceHandler.BeforeBalanceChange` simply stores `len(ctx.EventManager().Events())` into `bh.prevEventsLen` [1](#0-0) , and `AfterBalanceChange` slices `events[bh.prevEventsLen:]` to determine which `CoinSpent`/`CoinReceived`/fractional-balance events to apply to the `StateDB` [2](#0-1) .

For the "debug" precompile pattern (mirrored by other stateful precompiles that keep a single `BalanceHandler` instance on the precompile struct via `GetBalanceHandler()`, referenced in `precompiles/distribution/distribution.go`, `precompiles/erc20/erc20.go`, `precompiles/gov/gov.go`, `precompiles/ics20/ics20.go`, `precompiles/slashing/slashing.go`, `precompiles/staking/staking.go`), `Run` calls `p.GetBalanceHandler().BeforeBalanceChange(ctx)` then executes the method, then calls `AfterBalanceChange` [3](#0-2) . If the invoked method itself triggers a nested call back into the same precompile (a re-entrant/recursive EVM call, e.g. through a caller contract), the nested invocation's `BeforeBalanceChange` overwrites `bh.prevEventsLen` with a *later* event index. When control returns to the outer call and `AfterBalanceChange` runs, it slices from the now-advanced `prevEventsLen`, silently skipping the bank events that were emitted by the outer call before the nested call began. Those balance changes are never applied to `StateDB`, producing a permanent divergence between the native `x/bank` ledger (source of truth for consensus/state) and the EVM `StateDB` balances used for subsequent EVM-visible reads (`balanceOf`, `SLOAD`-equivalent balance opcodes, `msg.value` accounting for further calls within the same tx, etc.).

The repository itself contains a dedicated regression test explicitly documenting this defect: `evmd/tests/integration/balance_handler/balance_handler_test.go` states in its own comments: "tests the balance handler bug where recursive precompile calls share the same BalanceHandler instance, causing prevEventsLen to be overwritten. This leads to balance desync between native bank keeper and EVM stateDB." [4](#0-3) . The test drives a caller contract that recursively invokes the debug precompile's `callback` and asserts on the number of resulting events/precompile calls [5](#0-4) , but it does not assert that the native bank balance and the EVM `StateDB` balance remain equal after the recursive calls — i.e., the test demonstrates the recursive-call/event-count mechanics without closing the loop on verifying the balance-desync consequence is prevented.

This is architecturally analogous to the Aave issue: a "before" snapshot (`liquidityAdded` computed pre-transfer / `prevEventsLen` captured pre-nested-call) is consumed for a downstream accounting computation without accounting for intervening state-changing operations that should be included in the window.

### Impact Explanation
If a precompile-driven bank transfer's `CoinSpent`/`CoinReceived` events are dropped from `StateDB` application due to a nested/recursive call resetting `prevEventsLen`, an attacker-controlled contract could cause the on-chain `x/bank` balance to decrease (native transfer executed) while the EVM `StateDB` balance for the same address is not correspondingly decremented (or, symmetrically, a receiver's `StateDB` balance is not credited while the sender's is debited, or vice versa depending on which side of the event window is skipped). Because `StateDB` balance is what subsequent EVM opcodes and precompile calls within the same transaction (and cached across the block until the next full state read) observe, this creates a real divergence between spendable value as tracked by consensus-critical bank state and value observable/usable through the EVM execution path — matching the "irreversible accounting corruption of spendable user value across native balances / EVM balances" impact category. Depending on the exact direction of the desync (balance inflated in StateDB relative to bank), this could enable a user to leverage a phantom EVM balance for further precompile calls or contract logic in the same or later transactions, effectively duplicating value.

### Likelihood Explanation
Triggering the bug only requires an unprivileged user to deploy or use a contract that recursively/re-entrantly calls the same stateful precompile from within its own execution (this repository already contains test scaffolding — `DebugPrecompileCaller`, `StakingReverter`, `ERC20RecursiveNonRevertingPrecompileCall` — for exactly this pattern), so the trigger path is realistic and permissionless. However, the concrete exploitability depends on: (1) whether real (non-test) stateful precompiles actually allow reentry into themselves or another precompile sharing a `BalanceHandler` within the same call stack, and (2) whether existing guards (e.g., `MaxPrecompileCalls` limiting `AddPrecompileFn` journal entries [6](#0-5) , or per-call `BalanceHandlerFactory.NewBalanceHandler()` instantiation used in `precompiles/common/precompile.go`'s `runNativeAction` path [7](#0-6) ) already isolate each precompile invocation with a fresh handler instance, which would prevent the shared-instance overwrite described in the debug-precompile test. I was not able to fully confirm within the available searches whether the production precompiles (staking, distribution, erc20, gov, ics20, slashing) route through `RunNativeAction`/`runNativeAction` (which creates a new `BalanceHandler` per call via the factory) versus a shared/cached handler exposed by `GetBalanceHandler()` analogous to the debug precompile's pattern — this distinction is decisive for whether the bug is exploitable in production precompiles or only in the test-only debug precompile.

### Recommendation
- Verify and, if necessary, refactor all stateful precompiles that expose `GetBalanceHandler()` to always obtain a fresh `BalanceHandler` per top-level precompile invocation (as `runNativeAction` already does via `BalanceHandlerFactory.NewBalanceHandler()`), rather than reusing a single instance across nested/recursive calls.
- Make `BeforeBalanceChange`/`AfterBalanceChange` reentrancy-safe by using a stack (push/pop) of event-window markers instead of a single mutable `prevEventsLen` field, so nested calls do not clobber the outer call's window.
- Add an explicit invariant test asserting that `evmApp.BankKeeper.GetBalance(...)` and the EVM `StateDB`/`erc20Keeper.BalanceOf`-equivalent balance remain equal after recursive/re-entrant precompile calls, closing the gap in the existing `TestRecursivePrecompileCallsWithDebugPrecompile` test which currently only checks event counts.

### Proof of Concept
Not independently reproduced beyond what the repository's own test already demonstrates structurally (recursive precompile call via `DebugPrecompileCaller.callback` invoking the debug precompile, which shares a single `BalanceHandler` and records `prevEventsLen` on each nested entry) [8](#0-7) [9](#0-8) . A concrete fund-loss/duplication PoC against a production precompile (e.g. staking or ERC20) would require confirming which precompiles reuse a shared `BalanceHandler` across nested calls, which I could not fully verify within this investigation — flagged above as the key open question before this can be escalated with full confidence to a Critical, exploit-confirmed finding.

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

**File:** precompiles/common/balance_handler.go (L68-72)
```go
func (bh *BalanceHandler) AfterBalanceChange(ctx sdk.Context, stateDB *statedb.StateDB) error {
	events := ctx.EventManager().Events()

	for _, event := range events[bh.prevEventsLen:] {
		switch event.Type {
```

**File:** testutil/testdata/debug/debug.go (L47-115)
```go
func (p Precompile) Run(evm *vm.EVM, contract *vm.Contract, readonly bool) ([]byte, error) {
	stateDB, ok := evm.StateDB.(*statedb.StateDB)
	if !ok {
		return nil, errors.Wrap(errors2.ErrUnauthorized, "could not create statedb in debug precompile")
	}

	// get the stateDB cache ctx
	ctx, err := stateDB.GetCacheContext()
	if err != nil {
		return nil, err
	}

	// take a snapshot of the current state before any changes
	// to be able to revert the changes
	snapshot := stateDB.MultiStoreSnapshot()
	events := ctx.EventManager().Events()

	// add precompileCall entry on the stateDB journal
	// this allows to revert the changes within an evm tx
	err = stateDB.AddPrecompileFn(p.Address(), snapshot, events)
	if err != nil {
		return nil, err
	}

	// commit the current changes in the cache ctx
	// to get the updated state for the precompile call
	if err := stateDB.CommitWithCacheCtx(); err != nil {
		return nil, err
	}

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

**File:** x/vm/statedb/statedb.go (L436-449)
```go
// AddPrecompileFn adds a precompileCall journal entry
// with a snapshot of the multi-store and events previous
// to the precompile call.
func (s *StateDB) AddPrecompileFn(snapshot int, events sdk.Events) error {
	s.journal.append(precompileCallChange{
		snapshot: snapshot,
		events:   events,
	})
	s.precompileCallsCounter++
	if s.precompileCallsCounter > types.MaxPrecompileCalls {
		return fmt.Errorf("max calls to precompiles (%d) reached", types.MaxPrecompileCalls)
	}
	return nil
}
```

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
