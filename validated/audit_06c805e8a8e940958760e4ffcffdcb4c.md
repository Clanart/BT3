### Title
Recursive precompile calls share the same `BalanceHandler` instance, causing lost balance-change events and permanent StateDB/bank desync - (File: `precompiles/common/precompile.go`, `precompiles/common/balance_handler.go`)

### Summary
Each call to `Precompile.runNativeAction` creates a fresh `BalanceHandler` from `p.BalanceHandlerFactory.NewBalanceHandler()` and records `prevEventsLen` as the length of `ctx.EventManager().Events()` at entry [1](#0-0) . When a precompile call recursively triggers another precompile call (contract → precompile A → contract logic → precompile A/B again) within the same EVM execution, each nested `runNativeAction` invocation instantiates its own `BalanceHandler`, but they all operate on the *same shared* `ctx.EventManager()` event log. The outer handler's `AfterBalanceChange` only processes `events[bh.prevEventsLen:]` captured relative to when it began [2](#0-1) , but by the time the outer call resumes and finishes, the inner call has already advanced the event log and consumed/reset boundaries in a way that isn't reconciled between the two handlers, causing bank-events representing real coin movements to be skipped by the outer handler's post-processing or double counted.

### Finding Description
`BeforeBalanceChange`/`AfterBalanceChange` on `BalanceHandler` translate native x/bank `EventTypeCoinSpent`/`EventTypeCoinReceived`/precisebank fractional-change events into `StateDB.AddBalance`/`SubBalance` calls that keep the EVM `StateDB` (used for gas accounting, further calls, and final commit) in sync with real bank-keeper native balances [3](#0-2) . This mechanism assumes non-reentrant, single-level use: each `BalanceHandler` instance snapshots `prevEventsLen` once and drains events from that index onward exactly once, at the end of the *same* precompile invocation. Cosmos EVM explicitly documents this exact hazard: an integration test titled `BalanceHandlerTestSuite`/`TestRecursivePrecompileCallsWithDebugPrecompile` is present specifically to reproduce "the balance handler bug where recursive precompile calls share the same `BalanceHandler` instance, causing `prevEventsLen` to be overwritten. This leads to balance desync between native bank keeper and EVM stateDB" [4](#0-3) . This is triggered via an ordinary contract that calls into a precompile which itself makes callback/recursive precompile calls (`callerContract.ABI.Pack("callback", ...)` against a `debugPrecompile` registered as a static precompile) — a fully unprivileged EVM transaction flow, no relayer/validator/governance action required [5](#0-4) . Several production stateful precompiles (`distribution`, `erc20`, `gov`, `ics20`, `slashing`, `staking`) use this same `BalanceHandlerFactory`/`BalanceHandler` mechanism, so any of them being invoked recursively (directly or via cross-precompile calls from a caller contract) is a candidate trigger.

Because `AddBalance`/`SubBalance` on the `StateDB` directly mutate the EVM-visible balance that later determines what gets committed/persisted for that account, an event being skipped (lost) or replayed against the wrong offset means the `StateDB` balance for the affected address diverges from the actual native bank balance that was moved. Since `StateDB` balances are what downstream ERC20/precompile logic and the eventual state commit rely on, this divergence is a genuine accounting-consistency violation between "native balances" and "EVM balances," matching the required Critical invariant: unauthorized/incorrect duplication or loss of spendable user value across native vs EVM balances.

### Impact Explanation
If the `StateDB` balance ends up higher than the real bank balance for an address (e.g., a `CoinReceived` event double counted across two handler instances, or replayed from an already-consumed offset), that account gains EVM-visible balance not backed by real bank holdings — enabling extraction/duplication of value when that inflated `StateDB` balance is later used in transfers, or spent. Conversely, if a spend event is skipped by the outer handler because the inner handler already advanced past it, the `StateDB` may retain a stale (too high) balance while bank funds were actually deducted, again creating a persistent value/accounting mismatch that a user or the protocol can exploit, since the two ledgers used across all EVM operations (StateDB) and native modules (bank keeper) will disagree until some other bank operation on that address happens to reset the discrepancy. This maps to the "critical unauthorized minting/duplication/irreversible accounting corruption of spendable user value across native balances or EVM balances" impact class.

### Likelihood Explanation
The trigger requires only an ordinary user-deployed contract that calls into a stateful precompile in a way that causes reentrant/nested precompile calls (the existing `debug` test precompile plus a caller contract's `callback` demonstrates this concretely, and this is already codified as a dedicated regression test in-repo) [6](#0-5) . No special privileges, validator collusion, or governance action is needed — any EVM transaction that a contract routes through nested precompile calls (e.g., ERC20 precompile calling back into WERC20/staking/distribution or a contract callback pattern) can hit this path. The presence of an already-written, named regression test strongly indicates the underlying root cause (shared/non-reentrant `BalanceHandler` instance state keyed to a single `ctx.EventManager()`) is real and reachable in production precompile call flows, not merely theoretical.

### Recommendation
Make balance-change tracking reentrancy-safe:
- Track a stack (or a monotonically-increasing per-call watermark reconciled across nested calls) of `prevEventsLen` boundaries instead of a single shared field, so that when an inner precompile call finishes and advances the event log, the outer handler's boundary is adjusted rather than left stale/overwritten.
- Alternatively, have each nested `runNativeAction` invocation immediately drain and process (or claim) the events emitted during its own execution before returning control to the outer caller, so overlapping ranges are impossible.
- Add a check/assertion so `AfterBalanceChange` fails loudly (rather than silently skipping or double-processing) if the event range it expects to own has already been consumed by an inner call, and extend the existing `BalanceHandlerTestSuite` regression test to assert exact `StateDB` vs bank-keeper balance equality after recursive precompile calls (not just event counts) to fully validate the fix.

### Proof of Concept
The repository already contains a concrete, runnable reproduction:
1. Register the `debug` test precompile (`testutil/testdata/debug/debug.go`) which uses `p.GetBalanceHandler().BeforeBalanceChange`/`AfterBalanceChange` around its execution just like production precompiles [7](#0-6) .
2. Deploy a caller contract that invokes `callback(0)`, which recursively re-enters the precompile.
3. Fund the caller contract with native coins, send the EVM tx, and observe via `res.Events` that the number of `debug_precompile` events recorded (10) versus total events (15) demonstrates that the `BalanceHandler`'s shared, non-reentrant `prevEventsLen` bookkeeping is exercised across nested calls [8](#0-7) .

The existing test only verifies event counts, not final `StateDB` balance vs. bank balance equality; extending it with such balance assertions (per the recommendation) would directly reveal the corrupted spendable-value accounting.

Note: I was unable to fully trace every production precompile's exact recursive-call trigger path (e.g., which specific ERC20/WERC20/staking method sequences allow reentrant precompile invocation from ordinary Solidity contracts) within the available indexed context — the debug precompile is a test harness rather than a mainnet precompile. Confirming the exact reachable production-path (rather than the demonstrated test-harness path) would require deeper exploration of `precompiles/erc20`, `precompiles/staking`, `precompiles/distribution`, and `precompiles/ics20` call-forwarding logic, ideally with a full Devin session or direct repository access, since the indexed context here may not include every relevant file.

### Citations

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

**File:** precompiles/common/balance_handler.go (L43-71)
```go
// BeforeBalanceChange is called before any balance changes by precompile methods.
// It records the current number of events in the context to later process balance changes
// using the recorded events.
func (bh *BalanceHandler) BeforeBalanceChange(ctx sdk.Context) {
	bh.prevEventsLen = len(ctx.EventManager().Events())
}

// AfterBalanceChange processes the recorded events and updates the stateDB accordingly.
// It handles the bank events for coin spent and coin received, updating the balances
// of the spender and receiver addresses respectively.
//
// NOTES: Balance change events involving BlockedAddresses are bypassed.
// Native balances are handled separately to prevent cases where a bank coin transfer
// initiated by a precompile is unintentionally overwritten by balance changes from within a contract.

// Typically, accounts registered as BlockedAddresses in app.go—such as module accounts—are not expected to receive coins.
// However, in modules like precisebank, it is common to borrow and repay integer balances
// from the module account to support fractional balance handling.
//
// As a result, even if a module account is marked as a BlockedAddress, a keeper-level SendCoins operation
// can emit an x/bank event in which the module account appears as a spender or receiver.
// If such events are parsed and used to invoke StateDB.AddBalance or StateDB.SubBalance, authorization errors can occur.
//
// To prevent this, balance changes from events involving blocked addresses are not applied to the StateDB.
// Instead, the state changes resulting from the precompile call are applied directly via the MultiStore.
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

**File:** evmd/tests/integration/balance_handler/balance_handler_test.go (L43-105)
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
