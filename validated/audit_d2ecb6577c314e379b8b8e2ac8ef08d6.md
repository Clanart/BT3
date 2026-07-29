### Title
Shared `BalanceHandler` instance across recursive/nested precompile calls causes native balance desync between `x/bank` and EVM `StateDB` - (File: precompiles/common/balance_handler.go)

### Summary
The Symmetrical `deferredLiquidatePartyA`/`deferredSetSymbolsPrice` bug is a class of "checkpoint-window accounting" flaw: a value is captured/zeroed at one checkpoint, more value flows into the account before a second checkpoint reconciles it, and the second checkpoint's delta math wrongly attributes/duplicates that intervening value. The Cosmos EVM analog lives in `precompiles/common/balance_handler.go`'s `BeforeBalanceChange`/`AfterBalanceChange` pair, which records `prevEventsLen` (the "checkpoint") before a precompile call and processes only events emitted after that index once the call returns. If a precompile call is nested/recursive (a precompile internally triggers another precompile call, e.g. via `CallEVM`/`CallEVMWithData` in ERC20/staking/distribution/ICS20 precompiles), the same `BalanceHandler` instance's `prevEventsLen` field gets overwritten by the inner call, which is exactly the multi-step-shared-mutable-checkpoint pattern in the Sherlock report.

### Finding Description
`BalanceHandler.BeforeBalanceChange` snapshots `bh.prevEventsLen = len(ctx.EventManager().Events())` before a precompile executes, and `AfterBalanceChange` later slices `events[bh.prevEventsLen:]` to translate emitted `x/bank` `coin_spent`/`coin_received` (and `precisebank` fractional-balance) events into `StateDB.AddBalance`/`SubBalance` calls [1](#0-0) . Each precompile (`erc20`, `staking`, `distribution`, `slashing`, `gov`, `ics20`) exposes a single `GetBalanceHandler()` accessor used by the generic `Run` dispatch path (mirrored in the test/debug precompile) that calls `BeforeBalanceChange` then executes the method then calls `AfterBalanceChange` [2](#0-1) .

The repository itself already documents this exact defect in its test suite: `evmd/tests/integration/balance_handler/balance_handler_test.go` states plainly: *"BalanceHandlerTestSuite tests the balance handler bug where recursive precompile calls share the same BalanceHandler instance, causing prevEventsLen to be overwritten. This leads to balance desync between native bank keeper and EVM stateDB."* [3](#0-2) . A companion IBC test, `ics20_recursive_precompile_calls_test.go`, is titled around "the native balance handler bug where reverted distribution calls leave persistent bank events that are incorrectly aggregated" [4](#0-3) .

The mechanics mirror the Sherlock report's structure precisely:
- Checkpoint 1 (`BeforeBalanceChange`) records `prevEventsLen`, analogous to `deferredLiquidatePartyA` zeroing `allocatedBalances[partyA]` and recording state at T2.
- Between checkpoint 1 and checkpoint 2, a *nested* precompile call (analogous to Bob's liquidator role crediting funds at T3) re-enters the same handler and overwrites `prevEventsLen` to a new, later index.
- Checkpoint 2 (`AfterBalanceChange` of the *outer* call) then slices events from the wrong (advanced) index, silently dropping/misattributing bank events that occurred in the intervening window — this can cause `StateDB` balances to diverge from the actual `x/bank`/`x/precisebank` balances, i.e., either under-crediting a legitimate recipient (funds effectively vanish from the EVM-visible balance while still resident in `x/bank`) or over/under-debiting a spender, corrupting the EVM view of spendable value while the ledger and EVM state disagree.

### Impact Explanation
If `StateDB.AddBalance`/`SubBalance` calls are skipped or duplicated because `prevEventsLen` was clobbered by a nested precompile call, EVM-visible account balances diverge from the actual `x/bank`/`x/precisebank` ledger balances. This breaks the "Asset-representation path" invariant (1:1 accounting between native coins and EVM-visible/precompile-mediated balances) described in the scan's pivots, and can be triggered by an unprivileged user simply calling a contract that makes nested/recursive calls into precompiles (staking, distribution, ERC20, ICS20 are all EVM-reachable from ordinary contracts). Depending on which side is affected, an attacker-controlled contract could get EVM balance credited without the underlying bank debit ever being reflected in `StateDB` for the counterparty, or vice versa — a state divergence that a subsequent EVM operation (transfer/withdrawal) could exploit to spend or duplicate value that isn't truly backed, or to permanently strand real bank balance that the EVM never "sees" (effectively locking funds). This matches the required Critical impact classes (accounting corruption / unauthorized value extraction or freezing across native/EVM/precompile-mediated balances).

### Likelihood Explanation
The precompiles are reachable directly from ordinary, unprivileged smart-contract calls; the repository's own test files (`StakingReverter.sol` performing `nestedTryCatchDelegations`, `ics20_recursive_precompile_calls_test.go`, and `balance_handler_test.go`) demonstrate the team has already built reproduction harnesses for recursive/nested precompile calls hitting exactly this code path, confirming the trigger is realistic and reachable without any privileged role. However, I was unable to fully read the current bodies of `precompiles/common/balance_handler.go`, `precompiles/common/precompile.go`, and the full `ics20_recursive_precompile_calls_test.go` in this final pass (tool calls did not return content), so I cannot confirm from source whether a fix (e.g., per-call handler instantiation, a handler stack, or event-index reconciliation) has already landed to close this gap versus the tests only documenting a *known, still-open* issue. Given the test names explicitly say "tests ... the bug," it is plausible this is a currently-tracked/reproducible defect rather than a hypothetical one, but confirmation requires reading the current implementation and whether assertions in these tests currently pass (regression-locked) or are structured to catch a regression of an already-applied fix.

### Recommendation
- Make balance-change tracking re-entrant/nesting-safe: use a stack (push/pop) of `prevEventsLen` checkpoints per nested precompile invocation instead of a single mutable field on a shared `BalanceHandler`, or instantiate a fresh `BalanceHandler` per call frame (via `BalanceHandlerFactory.NewBalanceHandler()`) rather than reusing one instance for the whole precompile object across nested calls.
- Ensure `AfterBalanceChange` for an outer call only ever processes the exact event slice generated by that specific call frame, unaffected by any inner call's bookkeeping.
- Add invariant checks (e.g., in CI/integration tests) that assert `StateDB` balances equal `x/bank` + `x/precisebank` balances after any transaction involving nested precompile calls, not just simple call sequences.

### Proof of Concept
Reproduction scaffold already exists in-repo and should be run/extended to confirm exploitability end-to-end:
1. `evmd/tests/integration/balance_handler/balance_handler_test.go::TestRecursivePrecompileCallsWithDebugPrecompile` — deploys `DebugPrecompileCaller`, which calls into the debug precompile recursively (`callback(0)` chain), and asserts on `debug_precompile` event counts [5](#0-4) . This should be extended to assert `StateDB.GetBalance` vs `bankKeeper.GetBalance` equality for the involved accounts after the recursive call, to directly surface the desync.
2. `precompiles/testutil/contracts/StakingReverter.sol::nestedTryCatchDelegations` performs nested try/catch delegate calls through the staking precompile, explicitly built to probe partial-revert/nested-call balance bookkeeping [6](#0-5) .
3. `evmd/tests/ibc/ics20_recursive_precompile_calls_test.go` documents a companion scenario where "reverted distribution calls leave persistent bank events that are incorrectly aggregated" through recursive ICS20/distribution precompile calls [4](#0-3) .

I was not able to retrieve the current full source of `precompiles/common/balance_handler.go`'s handler-acquisition path (`GetBalanceHandler()` on the `Precompile` struct) or confirm from the live code whether these tests currently fail (open bug) or pass as regression guards (already patched) — this should be verified directly in a Devin session with full repository access before treating this as an unpatched, exploitable Critical finding.

### Citations

**File:** precompiles/common/balance_handler.go (L43-69)
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

**File:** evmd/tests/ibc/ics20_recursive_precompile_calls_test.go (L52-55)
```go
// Test suite for ICS20 recursive precompile calls
// Tests the native balance handler bug where reverted distribution calls
// leave persistent bank events that are incorrectly aggregated

```

**File:** contracts/solidity/precompiles/testutil/contracts/StakingReverter.sol (L52-80)
```text
    /// @dev nestedTryCatchDelegations performs nested try/catch calls to precompile
    /// where inner calls revert intentionally. Only the successful delegations
    /// outside the reverting scope should persist.
    ///
    /// Expected successful delegations: 1 (before loop) + outerTimes (after each catch) + 1 (after loop)
    function nestedTryCatchDelegations(uint outerTimes, uint innerTimes, string calldata validatorAddress) external {
        // Initial successful delegate before any nested reverts
        STAKING_CONTRACT.delegate(address(this), validatorAddress, 10);

        for (uint i = 0; i < outerTimes; i++) {
            // Outer call that will revert and be caught
            try StakingReverter(address(this)).performDelegation(validatorAddress) {
                // no-op
            } catch {
                // After catching the revert, perform a successful delegate
                STAKING_CONTRACT.delegate(address(this), validatorAddress, 10);

                // Inner nested loop of reverting calls
                for (uint j = 0; j < innerTimes; j++) {
                    try StakingReverter(address(this)).performDelegation(validatorAddress) {
                        // no-op
                    } catch {}
                }
            }
        }

        // Final successful delegate after the loops
        STAKING_CONTRACT.delegate(address(this), validatorAddress, 10);
    }
```
