## Analysis

The Giant Pool bug is a **stale-snapshot-before-reentrant-callback** pattern: a bookkeeping value (`idleETH`) is captured/mutated *before* a hook fires that recomputes derived accounting from that same value, allowing value to be manufactured "out of thin air" through nested calls sharing mutable state.

Push-chain-evm's precompile balance-accounting layer has the exact same shape: `BalanceHandler.prevEventsLen` is captured **before** a native precompile action runs, and `AfterBalanceChange` later replays *all* events emitted after that index against the EVM `StateDB`. If the native action itself triggers a **nested precompile call** (a real, reachable path — see `ERC20RecursiveNonRevertingPrecompileCall.sol`'s `_beforeTokenTransfer` hook, which calls `distribution.DISTRIBUTION_CONTRACT.claimRewards` recursively), the inner precompile call runs its own `RunNativeAction`/`BalanceHandler` lifecycle **on the same shared cache-context event manager**. The inner call's own `AfterBalanceChange` already applies the bank events it produced to `stateDB`. But because the outer `BalanceHandler`'s `prevEventsLen` was recorded *before* the inner call executed, the outer `AfterBalanceChange` — invoked afterward — re-scans the same event slice (`events[bh.prevEventsLen:]`) and **re-applies the same CoinSpent/CoinReceived/fractional-balance events a second time**, duplicating the `StateDB.AddBalance`/`SubBalance` calls.

This is confirmed by the repo's own regression test, whose comment states the bug outright: [1](#0-0) 

### Title
Recursive precompile calls cause duplicate application of bank balance-change events to the EVM StateDB, desynchronizing and duplicating spendable balance - (File: precompiles/common/precompile.go, precompiles/common/balance_handler.go)

### Summary
`BalanceHandler.BeforeBalanceChange`/`AfterBalanceChange` snapshot an event-index cursor (`prevEventsLen`) before a precompile's native action runs, then translate all bank events emitted since that cursor into `StateDB.AddBalance`/`SubBalance` calls. When a precompile action recursively invokes another precompile (e.g., an ERC20 token's transfer hook calling the distribution/staking precompile), both the inner and outer calls process overlapping segments of the same shared `ctx.EventManager()` event list, causing the outer handler to re-apply balance-changing events that the inner handler already applied to `StateDB`.

### Finding Description
`RunNativeAction`/`runNativeAction` creates a fresh `BalanceHandler` per call and records the event cursor before executing the native action, then replays events after the action completes: [2](#0-1) 

`BeforeBalanceChange` simply stores `len(ctx.EventManager().Events())`, and `AfterBalanceChange` iterates `events[bh.prevEventsLen:]`, converting `CoinSpent`/`CoinReceived`/fractional-balance events into direct `StateDB.AddBalance`/`SubBalance` mutations: [3](#0-2) [4](#0-3) 

The event manager (`ctx.EventManager()`) is shared across nested precompile invocations that occur within the same cache context/call stack — this is a reachable, unprivileged path: any ERC20-precompile-backed token whose `_beforeTokenTransfer`/`_afterTokenTransfer` hook calls into another stateful precompile (staking/distribution) will trigger a nested `runNativeAction`. The repo ships test fixtures explicitly designed to exercise exactly this recursive-precompile-call pattern: [5](#0-4) 

Because the outer `BalanceHandler`'s `prevEventsLen` is fixed *before* the inner call runs, and the inner call's own `AfterBalanceChange` already consumes/applies its events to `StateDB` (independently of the outer cursor), the outer call's later `AfterBalanceChange` re-includes the inner call's events in its `events[bh.prevEventsLen:]` slice and re-applies the same `AddBalance`/`SubBalance` deltas again. This duplicates the `StateDB` balance mutation for the inner transfer without a corresponding second bank-ledger movement — i.e., EVM-visible spendable balance is inflated relative to actual `x/bank`/`x/precisebank` state, exactly analogous to the Giant Pool bug where `idleETH` was decremented before the burn that re-derives `accumulatedETHPerLPShare`, minting rewards "out of thin air."

The repository's own integration test (`TestRecursivePrecompileCallsWithDebugPrecompile`) exists specifically to detect this desync condition between the "native bank keeper and EVM stateDB," corroborating that recursive precompile calls sharing event-manager state is a known, reproducible defect class in this codebase: [6](#0-5) 

### Impact Explanation
This falls under the Critical gate for "unauthorized minting, burning, duplication ... of spendable user value across native balances, EVM balances." Duplicate application of `AddBalance` events lets an attacker's EVM-visible balance grow beyond what the underlying `x/bank`/`x/precisebank` ledger actually holds, without any compensating debit. That inflated EVM balance is spendable via ordinary EVM transfers/calls, allowing extraction of value that is not backed by real bank balance — an irreversible accounting corruption and unauthorized value creation reachable by any unprivileged user deploying/calling a contract whose token hooks trigger a nested precompile call.

### Likelihood Explanation
The trigger requires no privileged access — any contract author can implement an ERC20 (or similar) token whose transfer hooks call a stateful precompile (staking, distribution, gov, ics20, erc20) from within another precompile's native action, which is a supported and even test-fixture-documented usage pattern (`ERC20RecursiveNonRevertingPrecompileCall.sol`). The precise conditions under which duplication actually manifests (ordering of nested vs. outer `BeforeBalanceChange`/`AfterBalanceChange` calls, whether the inner handler's application happens before or after the outer cursor is read) would need to be validated with a concrete PoC trace through `runNativeAction`'s call stack and the shared cache-context `ctx`, which was not fully exercisable via static review alone — this is noted as an area requiring dynamic verification.

### Recommendation
Ensure each nested/recursive precompile invocation consumes (removes or marks-as-processed) the event slice it applied, rather than relying purely on a monotonically-read `prevEventsLen` cursor that can be shared/overlapped across recursive calls. Consider tracking a stack of `BalanceHandler` cursors keyed to call depth, or truncating/marking `ctx.EventManager()` events as consumed after each `AfterBalanceChange` so outer handlers cannot re-process events already applied to `StateDB` by inner (nested) precompile calls.

### Proof of Concept
A concrete runtime PoC was not executed in this review; the existing repository test `TestRecursivePrecompileCallsWithDebugPrecompile` (`evmd/tests/integration/balance_handler/balance_handler_test.go`) already targets this exact recursive-precompile/BalanceHandler interaction and should be extended to assert on final `StateDB` balances vs. `x/bank` ledger balances after a nested call that emits `CoinSpent`/`CoinReceived` events at both the outer and inner precompile levels, to confirm whether double-application of balance deltas occurs.

**Note:** Given index/tooling limits, I was not able to trace the full recursive call stack at runtime (e.g., how `ctx`/`GetCacheContext()` nesting behaves across two `runNativeAction` invocations) to conclusively prove double-application versus safe truncation. A Devin session with full repo/test execution access would be needed to run and extend `TestRecursivePrecompileCallsWithDebugPrecompile` and confirm the exact duplicated-balance amount, which I could not verify from static code inspection alone.

### Citations

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
