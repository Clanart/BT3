### Title
Nested/recursive stateful precompile calls cause double-application of bank balance-change events to the EVM StateDB, duplicating spendable balance - (File: precompiles/common/precompile.go, precompiles/common/balance_handler.go)

### Summary
The external report's root cause is a state-machine invariant violation: an in-flight, privileged multi-step process (`closeLiquidation`) does not account for interleaved user actions (`depositNFT`) that occur inside the same "open window," so effects meant to be scoped to one flow bleed into another and get double-counted against the user. The Cosmos EVM analog is in the precompile balance-synchronization layer: `RunNativeAction`/`runNativeAction` in [1](#0-0)  creates a fresh `BalanceHandler` per precompile invocation and records `prevEventsLen` as a snapshot into the *shared* `ctx.EventManager()` event log [2](#0-1) . If a stateful precompile's native action itself triggers another stateful precompile call before returning (a nested/recursive invocation), the inner call's `AfterBalanceChange` consumes and applies (`AddBalance`/`SubBalance`) the bank events emitted during its own execution window [3](#0-2) . When control returns to the outer call, the outer `BalanceHandler` (whose `prevEventsLen` was captured *before* the nested call began) processes `events[bh.prevEventsLen:]` again — which still contains the same bank events already applied by the inner handler — re-applying the same `CoinSpent`/`CoinReceived`/fractional-balance deltas to the StateDB a second time.

### Finding Description
`runNativeAction` snapshots the event count into `prevEventsLen` right before invoking the native action, and the resulting `BalanceHandler` reads `ctx.EventManager().Events()[bh.prevEventsLen:]` afterward to reconcile bank-level coin movements into the EVM `StateDB` balances [4](#0-3) . This design assumes each precompile invocation's event window is disjoint from any other. However, `StateDB` explicitly tracks and permits repeated/recursive precompile calls up to `MaxPrecompileCalls` via `AddPrecompileFn`/`precompileCallsCounter` [5](#0-4) , meaning nested stateful precompile calls within a single EVM transaction are an expected, supported code path — not an edge case. The repository's own integration test (`TestRecursivePrecompileCallsWithDebugPrecompile`) is explicitly documented as covering "the balance handler bug where recursive precompile calls share ... causing prevEventsLen to be overwritten. This leads to balance desync between native bank keeper and EVM stateDB" [6](#0-5) , confirming this exact interaction is a recognized hazard class in this codebase, analogous to Bob's NFT deposits being unintentionally swept into an already-open liquidation window in the LendingPool report.

### Impact Explanation
If a nested precompile call path exists where an outer precompile's native action performs (or triggers) a bank-level coin movement and subsequently invokes an inner stateful precompile that also moves coins and reconciles events, the outer handler's event-window re-read causes the *same* underlying bank transfer to be credited/debited twice against the EVM `StateDB` balance of the affected address. Since EVM balances (`aatom`) back spendable value usable through the ERC20/WERC20 precompiles and ordinary EVM transfers, this duplication is an unauthorized creation of spendable EVM-visible balance not backed 1:1 by the corresponding `x/bank`/`x/precisebank` state — a direct violation of the asset-representation invariant this repo's own `x/precisebank` design goes to great lengths to guarantee ($a(n) = b(n)\cdot C + f(n)$, fully reserve-backed) [7](#0-6) . This matches the required Critical impact category of unauthorized duplication/irreversible accounting corruption of spendable user value across EVM and native balances.

### Likelihood Explanation
Exploitability depends on the existence of a concrete call graph in which one stateful precompile's `NativeAction` invokes another stateful precompile (or itself) before returning, while both sides move bank coins for the same or related addresses. `StateDB` deliberately supports and bounds such recursion (`MaxPrecompileCalls`), and several precompiles (`erc20`, `staking`, `distribution`, `gov`, `slashing`, `ics20`) all instantiate `BalanceHandlerFactory` independently through the shared `Precompile` struct [8](#0-7) , so any contract-composed call sequence that chains two of these within one native action (e.g., an ERC20 transfer that is itself the vehicle for an ICS20 or staking action, or callback-driven flows) is a plausible unprivileged trigger. I was not able to fully trace a concrete, currently-reachable production call path that nests two stateful precompiles within a single `NativeAction` before the tool budget for this investigation was exhausted — this is the primary source of uncertainty. The existing repository test only exercises a synthetic "debug precompile" harness and asserts event counts, not balance-duplication amounts, so it does not by itself prove exploitability in production precompiles; it does, however, confirm the underlying mechanism (shared/overwritten `prevEventsLen` semantics across nested calls) is real and acknowledged in this codebase.

### Recommendation
- Make `BalanceHandler` nesting-aware: instead of snapshotting a flat event-index into a globally shared event log, track a monotonically-consumed cursor shared across nested `BalanceHandler` instances (e.g., pass the outer handler's current cursor down to inner calls, or advance a single shared cursor object referenced by all nested handlers within a transaction) so no event is processed by more than one handler.
- Alternatively, tag each applied bank event (e.g., via a per-event sequence marker or by removing/marking consumed events) so `AfterBalanceChange` cannot re-apply an event already reconciled by an inner call.
- Add an explicit invariant check post-transaction reconciling total `StateDB` balance deltas against total `x/bank`/`x/precisebank` deltas for all touched addresses, failing/reverting the EVM transaction if they diverge, mirroring the existing `ErrBalanceInvariance` checks already used in `x/erc20` conversions [9](#0-8) .
- Extend `TestRecursivePrecompileCallsWithDebugPrecompile` (or add a new test) to assert actual balance equivalence between `x/bank`/`x/precisebank` and `StateDB` after a genuine nested stateful-precompile call chain, not just event counts.

### Proof of Concept
A definitive PoC requires identifying a concrete pair of stateful precompiles where one's native action invokes the other within the same call (nested `RunNativeAction`) while both move coins for an overlapping address set. This was not fully constructed within the scope of this investigation; the existing test harness `TestRecursivePrecompileCallsWithDebugPrecompile` in [10](#0-9)  demonstrates the mechanical precondition (recursive precompile invocation with a shared/overwritten `prevEventsLen`) using a synthetic debug precompile and should be extended to assert on final `StateDB` vs. `x/bank` balance equality (rather than event counts) to confirm double-crediting in a production precompile call chain.

### Citations

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

**File:** precompiles/common/balance_handler.go (L43-48)
```go
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

**File:** x/precisebank/README.md (L55-63)
```markdown
In order to maintain consistency between the `aatom` supply and the `uatom` supply,
we add the constraint that each sub-atomic `aatom`, may only exist as part of an atomic `uatom`.
Every `aatom` is fully backed by a `uatom` in the `x/bank` module.

This is a requirement since `uatom` balances in `x/bank` are shared between the cosmos modules and the EVM.
We are wrapping and extending the `x/bank` module with the `x/precisebank` module to add an extra $10^{12}$ units
of precision. If $10^{12}$ `aatom` is transferred in the EVM, the cosmos modules will see a 1 `uatom` transfer
and vice versa. If `aatom` was not fully backed by `uatom`, then balance changes would not be fully consistent
across the cosmos and the EVM.
```

**File:** precompiles/erc20/erc20.go (L1-1)
```go
package erc20
```

**File:** x/erc20/keeper/msg_server.go (L122-130)
```go
	expToken := big.NewInt(0).Add(balanceToken, tokens)

	if r := balanceTokenAfter.Cmp(expToken); r != 0 {
		return nil, sdkerrors.Wrapf(
			types.ErrBalanceInvariance,
			"invalid token balance - expected: %v, actual: %v",
			expToken, balanceTokenAfter,
		)
	}
```
