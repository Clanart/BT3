### Title
Balance desync between EVM StateDB and native bank keeper via shared `BalanceHandler` in recursive/nested precompile calls - (File: `precompiles/common/precompile.go`, `precompiles/common/balance_handler.go`)

### Summary
The elfi-protocol bug pattern is: a balance is decremented through one accounting path (direct `subTokenIgnoreUsedAmount`) while a second, dependent accounting structure (`positions.fromBalance`) is never updated to reflect that change, producing an inflated "available value" that lets a user open larger positions than their real collateral allows. The Cosmos EVM analog is the `BalanceHandler` used by stateful precompiles to reconcile native `x/bank`/`x/precisebank` balance-change events with the EVM `StateDB`'s in-memory balance cache. If the handler instance's bookkeeping window (`prevEventsLen`) is shared or gets overwritten across nested/recursive precompile invocations within a single EVM transaction, some bank events are never translated into `StateDB.AddBalance`/`SubBalance` calls, so the EVM-visible balance diverges from the actual bank-keeper balance for the remainder of the transaction and any dependent contract logic.

### Finding Description
`BalanceHandler.BeforeBalanceChange` records `len(ctx.EventManager().Events())` as `prevEventsLen` before a precompile action runs, and `AfterBalanceChange` reads `events[bh.prevEventsLen:]` to find newly emitted `EventTypeCoinSpent`/`EventTypeCoinReceived`/`EventTypeFractionalBalanceChange` events, translating them into `StateDB.AddBalance`/`SubBalance` calls: [1](#0-0) [2](#0-1) 

In `precompiles/common/precompile.go`'s `runNativeAction`, a *fresh* `BalanceHandler` is created via `p.BalanceHandlerFactory.NewBalanceHandler()` for every call, which is safe for that call path: [3](#0-2) 

However, the repository ships a documented, reproducible variant of this exact defect class: the integration test suite explicitly states "TestRecursivePrecompileCallsWithDebugPrecompile demonstrates the balance handler bug by triggering recursive calls that share the same BalanceHandler instance, causing prevEventsLen to be overwritten. This leads to balance desync between native bank keeper and EVM stateDB." [4](#0-3) 

The debug precompile (used as the reproduction vehicle) calls `p.GetBalanceHandler()` rather than constructing a new handler per call, i.e. it retrieves a shared/cached handler off the precompile instance: [5](#0-4) [6](#0-5) 

When a contract makes nested/recursive calls into a precompile (or a precompile action itself triggers another precompile call within the same EVM transaction), each nested call re-invokes `BeforeBalanceChange`, which overwrites `prevEventsLen` on the *same* handler object. When the outer call's `AfterBalanceChange` finally runs, it slices `events[bh.prevEventsLen:]` using the innermost call's window, silently skipping the coin-spent/coin-received events that were emitted by the outer (or intermediate) call(s). Those skipped events are never applied to `StateDB` via `AddBalance`/`SubBalance`, while the underlying `x/bank`/`x/precisebank` state has already changed (since these are applied directly to the multistore through `CommitWithCacheCtx`/`RevertMultiStore`, see `precompile.go` lines 63-84 and `statedb.go` `AddPrecompileFn`/`RevertMultiStore`). This produces a StateDB balance value for the affected address(es) that is inconsistent with the real bank-keeper-backed balance for the rest of the EVM transaction. [7](#0-6) [8](#0-7) 

Any subsequent EVM-level logic in that same transaction (e.g., `balanceOf`/`transfer` checks performed via opcodes rather than another precompile call, ERC20 wrapper accounting, or a contract's own bookkeeping of `msg.sender.balance`) will observe the stale/incorrect StateDB balance rather than the true post-precompile-call balance — this is structurally the same invariant violation as the elfi report: a balance changed through one path is not reflected in a second value that downstream logic depends on for authorization/available-value decisions.

### Impact Explanation
If StateDB balance for an address diverges from the real spendable bank balance within a transaction, it can be leveraged to:
- Read/observe an inflated native-token balance via `BALANCE` opcode or precompile `balanceOf` calls mid-transaction, potentially enabling a contract to pass balance-sufficiency checks it should fail, extract or transfer more value than is actually backed, or trigger duplicate accounting of the same funds (double-counted balance) — matching the "unauthorized minting/duplication/irreversible accounting corruption of spendable user value across native balances/EVM balances" impact category.
- Because the underlying bank-keeper state (source of truth) is correctly updated but the EVM's cached view is not, this is a genuine one-way desync, not merely a display bug — the corrupted state (StateDB balance) persists for the remainder of the transaction and is committed at `Commit()`, which writes the StateDB's balance directly into the keeper via `SetAccount`/`SetBalance` at the top level, potentially permanently corrupting the on-chain balance to a value inconsistent with actual bank-module accounting (mint or burn drift).

### Likelihood Explanation
Reachable by any unprivileged user: nested/recursive precompile calls can be triggered by ordinary smart-contract logic that calls a precompile (e.g., bank, staking, distribution, erc20, ics20 precompiles) from within a callback or delegate pattern that itself re-enters a precompile — a pattern the repository's own `StakingReverter.sol` test contracts and the dedicated `BalanceHandlerTestSuite` were built specifically to exercise and detect. The existence of a named, purpose-built regression test ("balance handler bug") strongly indicates this was a genuinely identified defect in this class of code; however, I could not confirm from the available index whether the currently shipped `BalanceHandler`/`BalanceHandlerFactory` logic in `precompiles/common/precompile.go` (which creates a new handler per `runNativeAction` call) has fully eliminated the sharing defect for all precompiles, or whether it persists specifically for precompiles (like the debug/test precompile) that fetch a handler via `GetBalanceHandler()` instead of the factory-per-call pattern. This distinction affects whether the vulnerability is exploitable in the production precompile set (bank, staking, distribution, gov, slashing, ics20, erc20) versus only in test-only code.

### Recommendation
- Ensure every stateful precompile always obtains a *new* `BalanceHandler` instance per top-level `Run`/`runNativeAction` invocation (as the factory pattern in `precompiles/common/precompile.go` already does), and audit all precompiles for any use of a cached/shared handler (e.g., via a `GetBalanceHandler()` accessor that returns a struct-level field) that could be reused across nested/recursive calls within the same EVM transaction.
- For nested precompile calls, propagate/nest the event-window bookkeeping (e.g., a stack of `prevEventsLen` values, or scope `AfterBalanceChange` processing strictly to events emitted between snapshot and current call depth) so an inner call's processing cannot clobber an outer call's marker.
- Add an invariant check (e.g., in `StateDB.Commit()` or via a dedicated post-transaction assertion) comparing StateDB balance of every dirtied address against the true `x/bank`/`x/precisebank` spendable balance, failing loudly on mismatch, to catch regressions of this class.
- Extend `BalanceHandlerTestSuite`-style tests to cover all production precompiles (not just the debug precompile) with recursive/nested call patterns, particularly staking/distribution/erc20/ics20 precompiles that are commonly composed in DeFi-style contracts.

### Proof of Concept
The repository already contains a working reproduction: `evmd/tests/integration/balance_handler/balance_handler_test.go` deploys a caller contract that invokes the debug precompile recursively (`callback(0)`) and asserts on the resulting event count/desync behavior: [9](#0-8) 
To adapt this into a Critical-impact PoC, the reproduction should be extended from the debug precompile to a production precompile with real transferable value (e.g., `bank` or `erc20` precompile) invoked recursively from a malicious contract, then assert that `StateDB.GetBalance`/`erc20.balanceOf` observed mid-transaction (or the final committed balance) diverges from the true `x/bank` `GetBalance`/`SpendableCoin` value — demonstrating a mintable/duplicable balance discrepancy rather than just an event-count anomaly.

**Note on completeness:** Because of index size limits, I was not able to inspect the full contents of `precompiles/staking/staking.go`, `precompiles/distribution/distribution.go`, `precompiles/gov/gov.go`, `precompiles/ics20/ics20.go`, or `precompiles/slashing/slashing.go` to confirm whether any of these production precompiles use a shared/cached `GetBalanceHandler()` pattern (like the debug/test precompile) rather than the safe per-call factory pattern. Confirming exploitability in a production precompile (versus only the test-only debug precompile) would require a Devin session with full file access to verify this definitively.

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

**File:** precompiles/common/precompile.go (L57-123)
```go
func (p Precompile) runNativeAction(evm *vm.EVM, contract *vm.Contract, action NativeAction) (bz []byte, err error) {
	stateDB, ok := evm.StateDB.(*statedb.StateDB)
	if !ok {
		return nil, errors.New(ErrNotRunInEvm)
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
	if err := stateDB.AddPrecompileFn(snapshot, events); err != nil {
		return nil, err
	}

	// commit the current changes in the cache ctx
	// to get the updated state for the precompile call
	if err := stateDB.CommitWithCacheCtx(); err != nil {
		return nil, err
	}

	initialGas := ctx.GasMeter().GasConsumed()

	defer HandleGasError(ctx, contract, initialGas, &err)()

	// set the default SDK gas configuration to track gas usage
	// we are changing the gas meter type, so it panics gracefully when out of gas
	ctx = ctx.WithGasMeter(storetypes.NewGasMeter(contract.Gas)).
		WithKVGasConfig(p.KvGasConfig).
		WithTransientKVGasConfig(p.TransientKVGasConfig)

	// we need to consume the gas that was already used by the EVM
	ctx.GasMeter().ConsumeGas(initialGas, "creating a new gas meter")

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

**File:** testutil/testdata/debug/debug.go (L77-78)
```go
	// Start the balance change handler before executing the precompile.
	p.GetBalanceHandler().BeforeBalanceChange(ctx)
```

**File:** testutil/testdata/debug/debug.go (L109-112)
```go
	// Process the native balance changes after the method execution.
	if err := p.GetBalanceHandler().AfterBalanceChange(ctx, stateDB); err != nil {
		return nil, err
	}
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
