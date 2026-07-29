### Title
Shared `BalanceHandler` instance corrupts EVM StateDB balances on nested/recursive precompile calls - (File: `precompiles/common/balance_handler.go`)

### Summary
The external report's bug class is a "guard applied inconsistently across sibling operations" — `checkDeviation` protects most liquidity operations but is silently missing from `init()`, breaking an invariant only on a specific code path. The Cosmos EVM analog is the `BalanceHandler` used by every precompile's `Run()` method: it tracks bank-module events via a single mutable field (`prevEventsLen`) that is meant to bracket "before" and "after" a precompile call, but the same `BalanceHandler` instance is reused/shared when a precompile call triggers another (nested/recursive) precompile call before the outer call's `AfterBalanceChange` executes. This is a native invariant-tracking mechanism missing "reentrancy-safe" isolation, exactly analogous to a missing protective check on a specific control-flow path.

### Finding Description
`BalanceHandler.BeforeBalanceChange` records the current bank event log length into `bh.prevEventsLen`, and `AfterBalanceChange` later replays `events[bh.prevEventsLen:]` to mirror bank-module `coin_spent`/`coin_received`/precisebank fractional events into the EVM `StateDB` via `AddBalance`/`SubBalance`. [1](#0-0) [2](#0-1) 

This handler is invoked from the generic precompile `Run()` entrypoint pattern seen in `testutil/testdata/debug/debug.go`: `BeforeBalanceChange` is called, the precompile method executes (which may itself call into another precompile, e.g., a token contract's transfer hook invoking the ICS20, bank, staking, or ERC20 precompile), and only afterward is `AfterBalanceChange` invoked to sync the recorded slice of events into the StateDB. [3](#0-2) 

If a precompile call is nested inside another precompile call using the same shared `BalanceHandler` instance (rather than a fresh instance per call frame), the inner call's `BeforeBalanceChange` overwrites `prevEventsLen`, so when the *outer* call later runs `AfterBalanceChange`, it slices the event log using the wrong (inner) offset. This causes either (a) events already applied by the inner call to be replayed a second time against the StateDB (double-crediting/debiting), or (b) real balance-changing events from the outer call to be skipped (silently dropped from stateDB sync) — corrupting the reconciliation between native bank-keeper balances and EVM-visible balances.

This exact bug class is explicitly documented and regression-tested in the repository itself: [4](#0-3) 
and reproduced via a contract that recursively calls a debug precompile: [5](#0-4) 
with a companion IBC/ICS20 recursive-precompile-call test suite exercising the same code path with real bank/staking/distribution precompiles under reverts: [6](#0-5) 

### Impact Explanation
If unpatched/reachable, this breaks the "Asset-representation path" invariant (1:1 accounting between native coins and EVM-visible balances) called out in the Smart Audit Pivots. A desynchronized `StateDB` balance versus the true bank-keeper balance is a Critical, irreversible accounting corruption of spendable user value: EVM-side balance could be inflated (double-credited) relative to the actual bank balance, enabling a user to spend EVM-native-token balance that doesn't actually exist in the bank module (extraction of value), or conversely balances could be permanently under-counted (freezing of funds visible only to the EVM). Both map directly to the allowed Critical impact categories (unauthorized/irreversible accounting corruption of spendable value across native/EVM balances; permanent freezing/theft of user funds).

### Likelihood Explanation
Triggering nested precompile calls is achievable by an unprivileged user: any ERC20-style contract with transfer hooks (e.g. `_beforeTokenTransfer`) that itself calls a precompile (bank, staking, distribution, ICS20) during a transfer initiated through another precompile call will produce the nested pattern. The repository's own test suites (`TestRecursivePrecompileCallsWithDebugPrecompile`, `ICS20RecursivePrecompileCallsTestSuite`) demonstrate this is a known, deliberately-tested scenario reachable through ordinary EVM transaction execution — not requiring any privileged role, validator, or relayer behavior.

However, I could not confirm from the available index whether the current production `BalanceHandler` instantiation is per-call (fresh `NewBalanceHandler()` per `Run()`) or a singleton reused across nested calls within the same top-level EVM transaction — the constructor `NewBalanceHandlerFactory(...).NewBalanceHandler()` exists and both the debug test and the ICS20 recursive test suite pass with specific expected event counts/balances, which suggests the current code may already correctly isolate handler state per call frame (i.e., the invariant may already be preserved by existing guards, and the tests could be regression tests confirming the fix rather than proof of a live bug). I was not able to fully trace `p.GetBalanceHandler()`'s per-call vs. shared lifecycle from the indexed code alone.

### Recommendation
Verify (or enforce) that each precompile `Run()` invocation obtains an independent `BalanceHandler` instance (e.g., via `BalanceHandlerFactory.NewBalanceHandler()` scoped to the call frame, not a field reused across nested/recursive precompile invocations), and add an explicit invariant check comparing total EVM-side balance deltas against final bank-keeper balances at the end of the outermost EVM message execution to reject transactions where they diverge — mirroring the `ErrBalanceInvariance` checks already present in `x/erc20/keeper/msg_server.go`.

### Proof of Concept
The existing regression tests already constitute a partial PoC of the underlying mechanism (shared/overwritten `prevEventsLen` during nested precompile execution): [7](#0-6) 
A full exploit PoC would require confirming the handler-sharing lifecycle in production precompile dispatch code (not fully available in the index) and constructing a contract that, during a transfer via one precompile, re-enters a second precompile that moves native coins, then asserting the resulting `StateDB` balance no longer matches `BankKeeper.GetBalance` for the same account after the outer call completes. This last verification step requires deeper access to the full precompile dispatch/`Run()` wiring than is available in the current index — a Devin session with full repository access would be needed to confirm whether the handler is shared or freshly allocated per call and to complete the exploit trace.

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

**File:** precompiles/common/balance_handler.go (L68-90)
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

**File:** contracts/solidity/DebugPrecompileCaller.sol (L1-30)
```text
// SPDX-License-Identifier: UNLICENSED
pragma solidity ^0.8.4;

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
}
```

**File:** evmd/tests/ibc/ics20_recursive_precompile_calls_test.go (L52-55)
```go
// Test suite for ICS20 recursive precompile calls
// Tests the native balance handler bug where reverted distribution calls
// leave persistent bank events that are incorrectly aggregated

```
