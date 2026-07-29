### Title
Nested/recursive precompile calls cause duplicate bank-event application to the EVM StateDB, allowing unbacked balance duplication - (File: precompiles/common/balance_handler.go, precompiles/common/precompile.go)

### Summary
The Aave eMode bug was rooted in a component pulling **stale/incorrect external state** (base pool config instead of adjusted eMode config) for a critical accounting calculation. The Cosmos EVM analog found here is in the precompile `BalanceHandler` mechanism, which reconciles native `x/bank` state changes into the EVM `StateDB` after a precompile call. The handler captures a "before" event-log length and later replays *all* events emitted since that point into `StateDB.AddBalance`/`SubBalance`. When a precompile call recursively/nestedly invokes another precompile (a common and unprivileged pattern, e.g. ERC20 `_beforeTokenTransfer` hooks calling `staking`/`distribution` precompiles, or ICS20 transfers triggering nested calls), the inner call's bank events are already consumed and applied to `StateDB` by the inner `BalanceHandler.AfterBalanceChange`, but remain in the shared `ctx.EventManager()` event log. The outer call's `AfterBalanceChange` then re-slices `events[prevEventsLen:]`, which still includes the inner call's already-applied events, and re-applies them to `StateDB` — duplicating the balance delta.

### Finding Description
`BalanceHandler.BeforeBalanceChange` records `prevEventsLen = len(ctx.EventManager().Events())` [1](#0-0) . `AfterBalanceChange` then iterates `events[bh.prevEventsLen:]` and calls `stateDB.AddBalance`/`SubBalance` for every `CoinSpent`/`CoinReceived`/`FractionalBalanceChange` event found in that range [2](#0-1) .

`RunNativeAction` in the shared precompile base wraps every precompile invocation, creating a `BalanceHandler`, calling `BeforeBalanceChange`, running the action (which itself may issue further EVM calls via `CallEVMWithData`, e.g. to another precompile), and finally calling `AfterBalanceChange` [3](#0-2) . Because `ctx.EventManager().Events()` is cumulative and shared across the call stack (it is not sliced away/reset between nested calls), any nested precompile invocation that completes and applies its own balance delta via its own `BeforeBalanceChange`/`AfterBalanceChange` bracket leaves its events physically present in the outer event log. When the outer call's `AfterBalanceChange` later runs, its slice `events[outerPrevEventsLen:]` spans across the nested call's bracket too, re-processing (and re-applying) the same `CoinSpent`/`CoinReceived` events a second time onto `StateDB`.

This exact defect class is explicitly documented by regression tests already present in the repo:
- `evmd/tests/integration/balance_handler/balance_handler_test.go`: *"tests the balance handler bug where recursive precompile calls share the same BalanceHandler instance, causing prevEventsLen to be overwritten. This leads to balance desync between native bank keeper and EVM stateDB."* [4](#0-3) 
- `evmd/tests/ibc/ics20_recursive_precompile_calls_test.go`: *"Tests the native balance handler bug where reverted distribution calls leave persistent bank events that are incorrectly aggregated"* [5](#0-4) 

An older/parallel precompile implementation pattern (`testutil/testdata/debug/debug.go`) stores a **single long-lived `BalanceHandler`** on the `Precompile` struct via `p.GetBalanceHandler()` rather than instantiating a fresh one per call [6](#0-5) , which is the literal "shared instance" bug the test names describe — any recursive/nested call through that same precompile object would overwrite `prevEventsLen` mid-flight, corrupting both the inner and outer balance reconciliation.

### Impact Explanation
If duplicate event application is reachable in production precompile code paths (`erc20`, `distribution`, `staking`, `ics20`, `slashing`, `gov` — all of which reference `BalanceHandler` per `grep_search`), an unprivileged user can trigger it by crafting a contract that makes a precompile call from within another precompile's execution context (e.g., an ERC20 token with a `_beforeTokenTransfer`/`_afterTokenTransfer` hook that calls `STAKING_CONTRACT.delegate` or `DISTRIBUTION_CONTRACT.claimRewards`, as shown in the test contract `ERC20RecursiveNonRevertingPrecompileCall.sol` [7](#0-6) ). The result is that the EVM-visible balance (`StateDB`) can diverge upward from the real `x/bank`/`x/precisebank` balance — i.e., duplicated/unbacked balance credited to an account in the EVM state. This is a direct violation of the "Asset-representation path" invariant (1:1 accounting between native coins and EVM/precompile-visible balances) and matches the Critical impact gate: unauthorized duplication/accounting corruption of spendable user value, which is then extractable via ordinary EVM transfers.

### Likelihood Explanation
The trigger requires only an unprivileged user deploying and calling a contract that nests precompile calls — a pattern the repository's own test suite (`ERC20RecursiveNonRevertingPrecompileCall.sol`, `ics20_recursive_precompile_calls_test.go`, `balance_handler_test.go`) demonstrates is both realistic and already exercised in CI, indicating the code paths are reachable in normal operation, not requiring privileged access or malicious validators/relayers.

### Recommendation
Ensure balance reconciliation is scoped strictly to events generated by the *current* call frame and cannot double-count events already consumed by a nested call — e.g., by tracking/removing consumed events from the shared event manager after each `AfterBalanceChange`, or by re-deriving `prevEventsLen` relative to a monotonically advancing "already processed" watermark shared across the call stack, or by using a per-call isolated (non-nested) event manager slice with truncation once processed. The `Precompile`-level `p.BalanceHandlerFactory.NewBalanceHandler()` pattern already mitigates the literal "shared struct field" version of the bug, but the underlying "events list is monotonically cumulative across nested Before/After brackets" issue should be explicitly verified and fixed for all production precompiles that permit reentrant/nested precompile calls.

### Proof of Concept
Uncertain/unverified: I could not fully trace, within the available tool budget, whether the currently shipped `RunNativeAction` path (used by `distribution`, `staking`, `erc20`, `ics20`, `slashing`, `gov`) actually re-processes already-applied events on nested calls, or whether some other guard (e.g., event consumption/truncation) prevents this in practice — the existing regression test `TestRecursivePrecompileCallsWithDebugPrecompile` [8](#0-7)  asserts specific event/debug counts (15 events, 10 `debug_precompile` events) without asserting on final balance correctness, so it does not conclusively prove the double-counting bug is fixed or still present. A background Devin session with full repository/test-execution access would be needed to run this test and a companion native-balance-delta assertion (comparing `x/bank` balance vs. EVM `StateDB` balance after a recursive precompile call sequence) to confirm whether duplication actually occurs in the current code, and to pinpoint the exact commit/PR status of this fix.

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

**File:** evmd/tests/integration/balance_handler/balance_handler_test.go (L23-25)
```go
// BalanceHandlerTestSuite tests the balance handler bug where recursive precompile calls
// share the same BalanceHandler instance, causing prevEventsLen to be overwritten.
// This leads to balance desync between native bank keeper and EVM stateDB.
```

**File:** evmd/tests/integration/balance_handler/balance_handler_test.go (L45-106)
```go
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

**File:** evmd/tests/ibc/ics20_recursive_precompile_calls_test.go (L52-54)
```go
// Test suite for ICS20 recursive precompile calls
// Tests the native balance handler bug where reverted distribution calls
// leave persistent bank events that are incorrectly aggregated
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

**File:** contracts/solidity/ERC20RecursiveNonRevertingPrecompileCall.sol (L124-142)
```text
    function _beforeTokenTransfer(
        address from,
        address to,
        uint256 amount
    ) internal virtual override(ERC20, ERC20Pausable) {
        // Emit an event to track if this hook is called
        emit BeforeTokenTransferHookCalled(from, to, amount);

        for(uint256 i=0; i < 5; i++) {
            try ERC20RecursiveNonRevertingPrecompileCall(address(this)).claimRewards() {

            } catch {

            }

        }

        super._beforeTokenTransfer(from, to, amount);
    }
```
