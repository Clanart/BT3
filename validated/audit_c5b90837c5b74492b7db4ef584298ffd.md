## Title
`BalanceHandler.prevEventsLen` desync on recursive/nested precompile calls corrupts EVM `StateDB` balances relative to native bank state — ([File: precompiles/common/balance_handler.go])

### Summary
The external report describes a class of bug where a piece of "bookkeeping state" that must be updated in lock-step with a balance-affecting operation is instead left stale/overwritten by another code path, causing accounting to diverge (`rebalancingRewardActive` not set when `_setWeightToZero()` runs). The Cosmos EVM analog is the `BalanceHandler.prevEventsLen` field used by the precompile → `StateDB` balance-sync mechanism: it is a single mutable field that gets clobbered whenever a precompile call re-enters (directly or via a contract callback) another (or the same) stateful precompile before the outer call finishes.

### Finding Description
Every stateful precompile (`distribution`, `staking`, `erc20`, `ics20`, `gov`, `slashing`, `bank`, `werc20`, and the test `debug` precompile) embeds `cmn.Precompile`, which owns a `BalanceHandlerFactory` and exposes a `BalanceHandler` used to reconcile native `x/bank`/`x/precisebank` events into the EVM `StateDB`: [1](#0-0) 

`BeforeBalanceChange` records the current length of `ctx.EventManager().Events()` into `bh.prevEventsLen`, and `AfterBalanceChange` later slices `events[bh.prevEventsLen:]` to translate only the events emitted *during that specific precompile call* into `StateDB.AddBalance`/`SubBalance` calls: [2](#0-1) 

The precompile's `Run` method calls `BeforeBalanceChange` before executing the native action and `AfterBalanceChange` after it, using the handler obtained from the precompile instance: [3](#0-2) 

If a precompile's native execution path re-enters the EVM (`evmKeeper.CallEVMWithData`) and that nested EVM execution calls back into a stateful precompile before the outer call returns, the *same* `BalanceHandler` instance's `prevEventsLen` is overwritten by the inner call's `BeforeBalanceChange`. When the inner call finishes and runs `AfterBalanceChange`, it consumes/slices the events starting at the inner offset. Then, when the outer call's execution resumes and eventually calls its own `AfterBalanceChange`, `bh.prevEventsLen` no longer reflects the offset that was valid for the outer call's start — it now reflects the inner call's (later) starting point. This causes the outer call to compute the wrong event slice: either replaying events already consumed by the inner call (double counting bank movements into `StateDB`) or skipping events that were emitted by the outer call before the nested call started (silently dropping balance updates so the EVM-visible balance never reflects native bank state).

This exact bug is called out and reproduced in the test suite: [4](#0-3) 

and a companion IBC-precompile test explicitly documents that "reverted distribution calls leave persistent bank events that are incorrectly aggregated": [5](#0-4) 

This is directly analogous to `handleInvalidConvexPid()` forgetting to update `rebalancingRewardActive`: a shared, mutable bookkeeping variable (`prevEventsLen`/`rebalancingRewardActive`) is updated by one code path but silently invalidated/overwritten by another concurrent/nested code path that shares the same instance, breaking the invariant the rest of the system depends on.

### Impact Explanation
If the outer call's event window is mis-computed such that bank events get double-applied to `StateDB` (e.g., an `AddBalance` replayed), the EVM-visible balance for an account can increase without a corresponding increase in the underlying native `x/bank`/`x/precisebank` coin supply — i.e., duplication of spendable EVM value not backed by native coins. Conversely, if events are skipped, a legitimate native bank credit/debit is never reflected in `StateDB`, causing EVM balances to permanently diverge from bank balances (funds effectively frozen/inaccessible from the EVM view, or a debit applied to bank without ever reducing the EVM balance, which is a duplication/inflation in the EVM domain). Both outcomes break the 1:1 accounting invariant between native coins and EVM-visible balances that the whole `precisebank`/EVM bridging design (see `x/precisebank/README.md`) depends on, which the scope explicitly calls out as Critical (unauthorized minting/duplication or permanent freezing of spendable user value across native/EVM balances).

### Likelihood Explanation
Reachability requires only ordinary, unprivileged transaction flow: any contract can call a stateful precompile (`ics20`, `distribution`, `staking`, `erc20`) whose native handling re-enters the EVM and, in turn, calls another/the same precompile before returning — for example via an ERC20 token with transfer hooks/callbacks used in a precompile-mediated transfer (as reproduced by `ERC20RecursiveReverting`/`ERC20RecursiveNonReverting` contracts in the IBC test), or a contract explicitly designed to recurse into a precompile like the `DebugPrecompileCaller.callback` pattern. No privileged role or relayer/validator collusion is required — a user only needs to deploy/call an ordinary contract that triggers the reentrant pattern.

### Recommendation
`BalanceHandler` state must not be a single shared/reused instance across nested precompile invocations. Either:
- Create a fresh `BalanceHandler` (via `BalanceHandlerFactory.NewBalanceHandler()`) for every precompile `Run` invocation rather than reusing one cached on the `Precompile` struct, or
- Maintain a stack of `prevEventsLen` values (push on `BeforeBalanceChange`, pop on `AfterBalanceChange`) so nested calls do not clobber the outer call's recorded offset, or
- Track events windows keyed by call depth/recursion level instead of a single scalar field.

Whichever approach is chosen, add regression tests asserting that for nested/recursive precompile calls, the aggregate set of `AddBalance`/`SubBalance` calls applied to `StateDB` exactly matches (no duplicates, no gaps) the full set of bank events emitted across the entire transaction, and that final `StateDB` balances equal final native bank balances for every touched account.

### Proof of Concept
The repository already contains a reproducer demonstrating the shared-instance issue via a recursive precompile-calling contract: [6](#0-5) [7](#0-6) 

and the IBC-level analog reproducing the same class of bug through an ERC20 transfer hook that recurses through the `ics20`/`distribution` precompiles during an IBC transfer, checking for incorrect aggregation of bank events into contract/reward balances: [8](#0-7) 

To confirm the Critical accounting-corruption impact precisely, a Devin session with full repo/tooling access should instrument `BalanceHandler.AfterBalanceChange` to log `bh.prevEventsLen` and the resulting slice bounds during the `TestRecursivePrecompileCallsWithDebugPrecompile` and `TestHandleMsgTransfer` ("recursive precompile call without reverts") test runs, and diff the resulting `StateDB` balances against the native `x/bank` balances for all touched accounts to determine whether value is duplicated or dropped in a concrete numeric scenario. This exact quantitative confirmation could not be completed here since it requires running the Go test suite, which is outside the current read-only indexing capability.

### Citations

**File:** precompiles/common/balance_handler.go (L30-48)
```go
func (bhf BalanceHandlerFactory) NewBalanceHandler() *BalanceHandler {
	return &BalanceHandler{
		bankKeeper:    bhf.bankKeeper,
		prevEventsLen: 0,
	}
}

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

**File:** testutil/testdata/debug/debug.go (L76-112)
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

**File:** evmd/tests/ibc/ics20_recursive_precompile_calls_test.go (L52-55)
```go
// Test suite for ICS20 recursive precompile calls
// Tests the native balance handler bug where reverted distribution calls
// leave persistent bank events that are incorrectly aggregated

```

**File:** evmd/tests/ibc/ics20_recursive_precompile_calls_test.go (L260-306)
```go
		{
			"test recursive precompile call with reverts",
			func(senderAcc evmibctesting.SenderAccount) {
				// Deploy recursive ERC20 contract with _beforeTokenTransfer override
				contractData, err := contracts.LoadERC20RecursiveReverting()
				suite.Require().NoError(err)

				deploymentData := testutiltypes.ContractDeploymentData{
					Contract:        contractData,
					ConstructorArgs: []interface{}{"RecursiveRevertingToken", "RRCT", uint8(18)},
				}

				contractAddr, err := DeployContract(suite.T(), suite.chainA, deploymentData)
				suite.chainA.NextBlock()
				suite.Require().NoError(err)

				// Setup contract info and test parameters
				nativeErc20 = &NativeErc20Info{
					ContractAddr: contractAddr,
					ContractAbi:  contractData.ABI,
					Denom:        "erc20:" + contractAddr.Hex(),
					InitialBal:   big.NewInt(InitialTokenAmount),
					Account:      common.BytesToAddress(senderAcc.SenderAccount.GetAddress().Bytes()),
				}

				sourceDenomToTransfer = nativeErc20.Denom
				msgAmount = sdkmath.NewIntFromBigInt(nativeErc20.InitialBal)
				erc20 = true

				// Setup contract for testing
				suite.setupContractForTesting(contractAddr, contractData, senderAcc)
			},
			func(querier distributionkeeper.Querier, valAddr string, eventAmount int) {
				evmAppA := suite.chainA.App.(*evmd.EVMD)
				bondDenom, err := evmAppA.StakingKeeper.BondDenom(suite.chainA.GetContext())
				suite.Require().NoError(err)
				contractBondDenomBalance := evmAppA.BankKeeper.GetBalance(suite.chainA.GetContext(), nativeErc20.ContractAddr.Bytes(), bondDenom)
				suite.Require().Equal(contractBondDenomBalance.Amount, sdkmath.NewInt(0))
				// Check distribution rewards after transfer
				afterRewards, err := querier.DelegationRewards(suite.chainA.GetContext(), &distrtypes.QueryDelegationRewardsRequest{
					DelegatorAddress: utils.Bech32StringFromHexAddress(nativeErc20.ContractAddr.String()),
					ValidatorAddress: valAddr,
				})
				suite.Require().NoError(err)
				suite.Require().Equal(afterRewards.Rewards[0].Amount.String(), ExpectedRewards)
				suite.Require().Equal(eventAmount, 20)
			},
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
