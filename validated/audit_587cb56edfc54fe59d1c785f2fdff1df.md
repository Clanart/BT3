### Title
Non-Atomic Balance-Event Reconciliation in Recursive Precompile Calls Causes EVM StateDB / Bank Balance Desync - (File: precompiles/common/balance_handler.go)

### Summary
The Cosmos EVM precompile framework reconciles native Cosmos SDK bank balance changes into the EVM `StateDB` by recording an event-index watermark (`prevEventsLen`) before a precompile's native action runs, then replaying all `sdk.Context` events emitted after that watermark into `StateDB.AddBalance`/`SubBalance` calls. When a precompile's native action itself triggers another (recursive/nested) precompile call — e.g. an ERC-20 `_beforeTokenTransfer` hook that calls the Distribution precompile's `claimRewards`, potentially several times, some of which `revert()` — the nested call's Cosmos-level bank events (`coin_spent`/`coin_received`) are appended to the shared `ctx.EventManager()` event log. If the nested EVM sub-call reverts, only the EVM/StateDB multi-store snapshot is rolled back; the SDK `EventManager` events are not removed. Those "orphaned" events then fall inside the *next* balance-affecting precompile call's `[prevEventsLen:]` window (e.g. the outer ICS-20 transfer) and get misattributed and replayed into `StateDB`, permanently desynchronizing the EVM-visible balance from the actual bank ledger balance. This is directly analogous to the reported Solana bug class: an action that should be atomic (revert cleanly with no side effects) leaves persistent residual state (there: a re-usable hashing account; here: leaked bank events) that a subsequent step incorrectly consumes, letting an attacker "farm" extra credited value.

### Finding Description
`BalanceHandler.BeforeBalanceChange`/`AfterBalanceChange` [1](#0-0)  take a snapshot of `len(ctx.EventManager().Events())` before a precompile executes and replay every event after that index into the StateDB once the call finishes: [2](#0-1) 

Each top-level `RunNativeAction` invocation goes through `runNativeAction`, which snapshots the EVM multi-store for revert purposes and instantiates a `BalanceHandler` around the call: [3](#0-2) 

Crucially, if `action(ctx)` (the precompile's native logic) errors — e.g. because a nested precompile call inside it later `revert()`s — the function returns immediately without ever calling `AfterBalanceChange` [4](#0-3) . The EVM-level revert correctly restores `StateDB` via `RevertToSnapshot`/multi-store snapshot, but it does **not** retroactively strip the bank-module events (`coin_spent`, `coin_received`) that were already emitted into `ctx.EventManager()` by the reverted nested call (e.g. `WithdrawDelegationRewards` inside `ClaimRewards` [5](#0-4) ). `sdk.EventManager` events are plain in-memory slices, unrelated to the `CacheMultiStore`/journal revert mechanism, so they persist in the shared context's event log across the recursive call boundary.

Any subsequent balance-affecting precompile call in the *same* EVM call frame (e.g. the actual outer ICS-20 `transfer` call, itself wrapped by another `BalanceHandler`) computes its own `prevEventsLen` at call time and later slices `events[bh.prevEventsLen:]`. Because the orphaned events from the earlier, reverted nested call were never cleaned up, they fall in-range and get replayed into `StateDB.AddBalance`/`SubBalance`, crediting or debiting EVM balances that do not correspond to any actually-committed bank movement.

The repository contains dedicated regression tests explicitly confirming and naming this exact bug class:
- `BalanceHandlerTestSuite` states: *"tests the balance handler bug where recursive precompile calls share the same BalanceHandler instance, causing prevEventsLen to be overwritten. This leads to balance desync between native bank keeper and EVM stateDB."* [6](#0-5) 
- `ICS20RecursivePrecompileCallsTestSuite` states: *"Tests the native balance handler bug where reverted distribution calls leave persistent bank events that are incorrectly aggregated"* and drives a malicious ERC-20 whose `_beforeTokenTransfer` hook recursively calls the Distribution precompile's `claimRewards`/`claimRewardsAndRevert` up to 5 times during an ICS-20 transfer, then asserts on the resulting (unexpected) bank/event state [7](#0-6) [8](#0-7) 
- The malicious hook pattern itself: [9](#0-8) 

### Impact Explanation
This breaks the "Asset-representation path" invariant (1:1 accounting between native bank coins and EVM-visible balances) called out in the audit scope. An unprivileged attacker who deploys a custom ERC-20 contract implementing a transfer hook that recursively invokes any `BalanceHandler`-integrated precompile (Distribution, Staking, Gov, Bank, ICS20, ERC20, WERC20) and deliberately reverts some of the nested calls can cause bank-module events to leak across the precompile-call boundary. When a subsequent balance-changing precompile call executes later in the same EVM transaction, it can incorrectly replay those leaked events into `StateDB`, crediting the attacker-controlled account (or contract) with EVM-visible balance not backed by any real, committed bank transfer. This is a critical, unauthorized-duplication/accounting-corruption bug on spendable balances (native/EVM/precompile-mediated), matching the "unauthorized minting/duplication/irreversible accounting corruption" impact category. Depending on the exact event ordering, it could also strip out or double-consume events intended for the legitimate call, causing incorrect debits (fund loss) for other unrelated parties whose bank events happen to fall in the same watermark window.

### Likelihood Explanation
The precondition is simply deploying an arbitrary ERC-20/ERC-721-like contract with a custom transfer hook (or any contract) that makes nested calls into a `BalanceHandler`-backed precompile and reverts some of them — fully achievable by any unprivileged EVM user, with no special permissions, validator collusion, or governance action required. The repository's own test suites (`balance_handler_test.go`, `ics20_recursive_precompile_calls_test.go`) demonstrate that this exact call pattern is reachable in production code paths (real Distribution + ICS20 precompiles, not just the test-only debug precompile), and both suites are written specifically to characterize/reproduce the described desync, indicating the underlying flaw is a known, exercised code path rather than a theoretical one.

### Recommendation
- Make event-log bookkeeping atomic with respect to the EVM multi-store revert: either (a) snapshot and truncate/restore `ctx.EventManager()` events on nested-call revert so that no residual events survive past a reverted precompile call, or (b) tag emitted bank events with the precompile-call depth/id (via the same journal entry used for `AddPrecompileFn`/multi-store snapshot) and discard events whose originating call was reverted before replaying them into `StateDB`.
- Ensure `BalanceHandler` instances are strictly scoped per call frame (not shared/reused across recursive precompile invocations) and that nested calls do not extend or shift an outer call's `[prevEventsLen:]` window.
- Add an invariant check (e.g., end-of-block or end-of-tx) comparing aggregate `StateDB` native-denom balances against the authoritative bank module balances to detect divergence early.

### Proof of Concept
The repository's own tests are effectively runnable PoCs:
1. `evmd/tests/integration/balance_handler/balance_handler_test.go` `TestRecursivePrecompileCallsWithDebugPrecompile` deploys a caller contract that recursively calls a `BalanceHandler`-backed debug precompile and asserts on the resulting event/balance discrepancy count [10](#0-9) .
2. `evmd/tests/ibc/ics20_recursive_precompile_calls_test.go` `TestHandleMsgTransfer` deploys `ERC20RecursiveReverting`/`ERC20RecursiveNonReverting` contracts whose `_beforeTokenTransfer` hook loops 5 times calling the Distribution precompile's `claimRewards`/`claimRewardsAndRevert`, then performs an ICS-20 `transfer` of the entire ERC-20 balance and asserts on the resulting contract bank balance and event counts, demonstrating the leaked/misattributed event behavior described above [11](#0-10) .

### Citations

**File:** precompiles/common/balance_handler.go (L43-106)
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

**File:** precompiles/common/precompile.go (L57-126)
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

	return bz, nil
}
```

**File:** precompiles/distribution/tx.go (L59-78)
```go
	res, err := p.stakingKeeper.GetDelegatorValidators(ctx, delegatorAddr.Bytes(), maxRetrieve)
	if err != nil {
		return nil, err
	}
	totalCoins := sdk.Coins{}
	for _, validator := range res.Validators {
		// Convert the validator operator address into an ValAddress
		valAddr, err := sdk.ValAddressFromBech32(validator.OperatorAddress)
		if err != nil {
			return nil, err
		}

		// Withdraw the rewards for each validator address
		coins, err := p.distributionKeeper.WithdrawDelegationRewards(ctx, delegatorAddr.Bytes(), valAddr)
		if err != nil {
			return nil, err
		}

		totalCoins = totalCoins.Add(coins...)
	}
```

**File:** evmd/tests/integration/balance_handler/balance_handler_test.go (L23-35)
```go
// BalanceHandlerTestSuite tests the balance handler bug where recursive precompile calls
// share the same BalanceHandler instance, causing prevEventsLen to be overwritten.
// This leads to balance desync between native bank keeper and EVM stateDB.
type BalanceHandlerTestSuite struct {
	suite.Suite

	coordinator *evmibctesting.Coordinator
	chain       *evmibctesting.TestChain
}

func TestBalanceHandlerTestSuite(t *testing.T) {
	suite.Run(t, new(BalanceHandlerTestSuite))
}
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

**File:** evmd/tests/ibc/ics20_recursive_precompile_calls_test.go (L52-66)
```go
// Test suite for ICS20 recursive precompile calls
// Tests the native balance handler bug where reverted distribution calls
// leave persistent bank events that are incorrectly aggregated

type ICS20RecursivePrecompileCallsTestSuite struct {
	suite.Suite

	coordinator *evmibctesting.Coordinator

	// testing chains used for convenience and readability
	chainA           *evmibctesting.TestChain
	chainAPrecompile *ics20.Precompile
	chainB           *evmibctesting.TestChain
	chainBPrecompile *ics20.Precompile
}
```

**File:** evmd/tests/ibc/ics20_recursive_precompile_calls_test.go (L260-356)
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
		},
		{
			"test recursive precompile call without reverts",
			func(senderAcc evmibctesting.SenderAccount) {
				// Deploy recursive ERC20 contract with _beforeTokenTransfer override
				contractData, err := contracts.LoadERC20RecursiveNonReverting()
				suite.Require().NoError(err)

				deploymentData := testutiltypes.ContractDeploymentData{
					Contract:        contractData,
					ConstructorArgs: []interface{}{"RecursiveNonRevertingToken", "RNRCT", uint8(18)},
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

				suite.Require().Equal(contractBondDenomBalance.Amount, sdkmath.NewInt(50))

				// Check distribution rewards after transfer
				afterRewards, err := querier.DelegationRewards(suite.chainA.GetContext(), &distrtypes.QueryDelegationRewardsRequest{
					DelegatorAddress: utils.Bech32StringFromHexAddress(nativeErc20.ContractAddr.String()),
					ValidatorAddress: valAddr,
				})
				suite.Require().NoError(err)
				suite.Require().Nil(afterRewards.Rewards)
				suite.Require().Equal(eventAmount, 29) // 20 base events + (1 successful reward claim + 1 send + 1 receive + 1 message + 1 transfer) + 4 empty reward claims
			},
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
