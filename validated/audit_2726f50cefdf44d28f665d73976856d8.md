This one is documented and appears already fixed in production code, but here is the full analysis.

### Title
Recursive/reentrant precompile calls sharing a stateful `BalanceHandler` corrupt `prevEventsLen`, causing StateDB/bank balance desync - (File: `precompiles/common/balance_handler.go`, `precompiles/common/precompile.go`)

### Summary
The Maia bug is a reentrancy pattern where a persistent, non-reentrant-safe piece of state (`initialGas`/`userFeeInfo`) is overwritten/cleared by a nested call before the outer call finishes using it, silently corrupting accounting. The direct Cosmos EVM analog is the `BalanceHandler`'s `prevEventsLen` field, which records "where in the event log to start scanning for balance-changing bank events" for a precompile invocation [1](#0-0) . If a precompile call recursively invokes the EVM again (e.g., via `CallEVMWithData`, or a `call()` back into the same/another precompile) while reusing the *same* `BalanceHandler` instance, the inner call's `BeforeBalanceChange` overwrites `prevEventsLen`, and the outer call's `AfterBalanceChange` then reads the wrong event-window offset, mis-attributing or dropping bank `coin_spent`/`coin_received` events when reflecting native balance changes into the EVM `StateDB` [2](#0-1) .

### Finding Description
- `RunNativeAction` (the current, hardened precompile entrypoint) allocates a *fresh* `BalanceHandler` per call via `p.BalanceHandlerFactory.NewBalanceHandler()`, so `prevEventsLen` is local to that specific invocation [3](#0-2) .
- However, an older/alternate precompile pattern (used by the `debug` test precompile at `testutil/testdata/debug/debug.go` and `evmd/tests/testdata/debug/debug.go`) instead calls `p.GetBalanceHandler()`, implying a single handler instance stored on the precompile struct and reused across calls [4](#0-3) .
- The repository has an explicit regression test, `TestRecursivePrecompileCallsWithDebugPrecompile`, whose docstring states: *"tests the balance handler bug where recursive precompile calls share the same BalanceHandler instance, causing prevEventsLen to be overwritten. This leads to balance desync between native bank keeper and EVM stateDB."* [5](#0-4) 
- The reentrant trigger path is real and reachable by an unprivileged EVM caller: the debug precompile's `Call0` calls back into the EVM via `p.evmKeeper.CallEVMWithData(ctx, p.Address(), &caller, data, true, nil)`, and the `DebugPrecompileCaller.sol` contract drives recursive precompile calls purely from user-controlled input (`counter`) [6](#0-5) [7](#0-6) .
- This is the same invariant break class as the Maia bug: a per-call gas/balance accounting scratch value that is not reentrancy-isolated, so nested execution silently corrupts the outer call's bookkeeping.

### Impact Explanation
If a production (non-debug) precompile that performs native-to-EVM balance reflection (e.g., `erc20`, `staking`, `distribution`, `gov`, `ics20`, `slashing` — all of which reference `BalanceHandlerFactory`/`GetBalanceHandler` per the grep results) were to use the shared-handler pattern instead of the per-call factory pattern, a reentrant call sequence could cause `AfterBalanceChange` to either (a) skip processing legitimate `coin_spent`/`coin_received` events (because `prevEventsLen` was advanced past them by the inner call), or (b) double-process events from the inner call in the outer call's scope. Either outcome desyncs the EVM `StateDB` balance view from the actual bank-module balance — a spendable-value accounting corruption in scope under "irreversible accounting corruption ... across native balances ... or precompile-mediated assets." However, I could not verify from the indexed code that any *currently shipped, non-debug* precompile actually uses the buggy shared-handler pattern; all production precompiles I could confirm (`erc20.go`, `distribution.go`, `staking.go`, `gov.go`, `ics20.go`, `slashing.go`) reference the balance-handler API, but I was not able to fully inspect their construction code to confirm whether they use `BalanceHandlerFactory.NewBalanceHandler()` (safe, per-call) or a shared instance (unsafe) within the tool budget available.

### Likelihood Explanation
The `MaxPrecompileCalls` counter [8](#0-7)  and per-call snapshot/journal mechanism [9](#0-8)  limit unbounded reentrancy but do not by themselves prevent the shared-handler aliasing bug — that class of bug is fixed only by allocating a fresh `BalanceHandler` per call, which `RunNativeAction` does. The presence of a dedicated, named regression test strongly suggests this was a real, previously-exploitable bug in this codebase that has since been patched (or is being guarded against) via the `BalanceHandlerFactory` pattern.

### Recommendation
Confirm that every production precompile (`erc20`, `staking`, `distribution`, `gov`, `ics20`, `slashing`) constructs and uses a fresh `BalanceHandler` per invocation (via `RunNativeAction`/`BalanceHandlerFactory.NewBalanceHandler()`) rather than any shared/singleton `GetBalanceHandler()`-style accessor, and keep the `TestRecursivePrecompileCallsWithDebugPrecompile` regression test (and equivalents) in CI to prevent reintroduction of the shared-instance pattern in any current or future precompile.

### Proof of Concept
The existing test demonstrates the reentrant trigger and observed event/desync symptom: a contract calling the debug precompile recursively (`DebugPrecompileCaller.callback`) drives nested precompile invocations that reuse the shared handler pattern present in `evmd/tests/testdata/debug/debug.go`, and the test asserts on the resulting (currently "fixed-count") event totals to catch regressions [10](#0-9) .

**Note on indexing limits**: due to index size limits, I was unable to fully retrieve/verify the full construction code of `precompiles/erc20/erc20.go`, `precompiles/staking/staking.go`, `precompiles/distribution/distribution.go`, `precompiles/gov/gov.go`, `precompiles/ics20/ics20.go`, and `precompiles/slashing/slashing.go` to conclusively determine whether any of them currently use the unsafe shared-`BalanceHandler` pattern versus the safe per-call factory pattern. If you need a definitive answer on whether a production (non-debug) precompile is presently vulnerable, I recommend starting a Devin session to read those files in full.

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

**File:** precompiles/common/precompile.go (L57-94)
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

**File:** testutil/testdata/debug/debug.go (L58-75)
```go

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

**File:** contracts/solidity/DebugPrecompileCaller.sol (L4-29)
```text
contract DebugPrecompileCaller {
    address constant debugPrecompile = 0x0000000000000000000000000000000000000799;
    error CallFailed(bytes data);
    function callback(uint256 counter) public {
        bool result;
        bytes memory data;

        // emit events
        for (uint i = 0; i < counter; i++) {
            (result, data) = debugPrecompile.call(abi.encodePacked(uint8(1)));
            if (!result) {
                revert CallFailed(data);
            }
        }

        if (counter > 3) {
            // stop the recursion
            return;
        }

        // recursive call
        (result, data) = debugPrecompile.call(abi.encodePacked(uint8(0), counter));
        if (!result) {
            revert CallFailed(data);
        }
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
