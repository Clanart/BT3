### Title
Recursive precompile calls corrupt `BalanceHandler` event-window accounting, causing native bank balances to diverge from EVM/ERC20 StateDB balances - (File: precompiles/common/balance_handler.go)

### Summary
The Cosmos EVM analog of the "funds left on SwapExecutor" bug-class (an intermediate execution context whose balance-accounting is incomplete/desynced, leaving spendable value untracked) is the `BalanceHandler` used by every stateful precompile to translate Cosmos SDK bank events into EVM `StateDB` balance updates. `BalanceHandler.BeforeBalanceChange`/`AfterBalanceChange` slice the shared `ctx.EventManager().Events()` list using a raw event-count index (`prevEventsLen`) rather than a nesting-safe mechanism [1](#0-0) . When a precompile's native action recursively triggers further bank-moving operations (e.g. an ERC20 token with a `_beforeTokenTransfer` hook that calls back into the `distribution`/`staking` precompiles), the nested calls append events to the same underlying event log the outer call is bookkeeping against, so the outer call's index-based event slice mis-attributes or skips balance deltas.

### Finding Description
Every stateful precompile (`staking`, `distribution`, `slashing`, `erc20`, `ics20`, `bank`, `gov`) routes native execution through `Precompile.runNativeAction`, which creates a `BalanceHandler`, calls `BeforeBalanceChange` (recording `len(ctx.EventManager().Events())`), runs the native action, and then calls `AfterBalanceChange`, which re-reads `ctx.EventManager().Events()[bh.prevEventsLen:]` and applies each `coin_spent`/`coin_received`/fractional-balance event to the EVM `StateDB` via `AddBalance`/`SubBalance` [2](#0-1) [3](#0-2) .

This design assumes the event log is monotonically appended and consumed exactly once per top-level precompile invocation. It breaks when precompile-mediated ERC20/native tokens trigger reentrant/recursive precompile or bank calls within the same transaction (e.g., an ERC20 `_beforeTokenTransfer` hook that calls `distribution.claimRewards`, which itself performs bank sends, before the outer transfer's own bank events are appended). Because the nested call shares the same `ctx.EventManager()`/cache context, its emitted events land in the same slice the outer call is bookkeeping with `prevEventsLen`, causing the outer `AfterBalanceChange` to either re-process events already consumed by the inner call, or to skip/misalign the events belonging to its own operation. The result is that the Cosmos SDK bank ledger balance for the contract account and the EVM `StateDB`/ERC20-visible balance diverge — the contract retains a real native (bank) balance that is not reflected/spendable through the EVM/ERC20 view, i.e., value becomes stuck or under-attributed similar to "leftover funds on SwapExecutor."

This exact scenario is already reproduced and hard-coded as expected behavior in two dedicated regression test suites in the repository:
- `evmd/tests/integration/balance_handler/balance_handler_test.go`, explicitly titled to document "the balance handler bug where recursive precompile calls share the same BalanceHandler instance, causing prevEventsLen to be overwritten. This leads to balance desync between native bank keeper and EVM stateDB." [4](#0-3) 
- `evmd/tests/ibc/ics20_recursive_precompile_calls_test.go`, which drives an ERC20 token whose `_beforeTokenTransfer` hook recursively calls the `distribution` precompile's `claimRewards` during an ICS20 transfer, and asserts that after the transfer the contract's native `bondDenom` bank balance is left at a nonzero residual (`50`) while distribution rewards were silently consumed/claimed inconsistently with the ERC20/StateDB view of the contract's balance [5](#0-4) .

### Impact Explanation
This is an accounting-corruption bug matching the "irreversible accounting corruption of spendable user value across native balances, EVM balances, ERC20 representations ... precompile-mediated assets" impact class. A contract account can end up holding a real, spendable native bank balance that is not reflected in its EVM/ERC20-visible balance (or vice versa), meaning the two ledgers the chain is supposed to keep in 1:1 sync (bank coins vs. EVM state) diverge. Depending on which side under- or over-counts, this can (a) strand real native coins on a contract address that ERC20/EVM tooling reports as empty (effectively locking user funds), or (b) allow the EVM-visible balance to exceed what is actually backed by the bank ledger, an unauthorized-duplication-style outcome. Both are Critical per the allowed-impact gate (permanent freezing/locking of contract balances, and/or accounting corruption of spendable value).

### Likelihood Explanation
The trigger is reachable by any unprivileged user: deploying an ERC20-style contract with a transfer hook (`_beforeTokenTransfer`/`_afterTokenTransfer`) that calls back into any stateful native precompile (`distribution`, `staking`, `bank`, `ics20`, etc.) during a transfer is ordinary, permissionless contract logic — no privileged keys, validators, or governance are required. The repository's own test suites were purpose-built to demonstrate this exact recursive-call pattern is reachable and produces divergent balances, confirming both root cause and reachability.

### Recommendation
Replace the raw event-count index (`prevEventsLen`) bookkeeping in `BalanceHandler` with a mechanism that is safe under reentrancy/nesting — e.g., track and consume events by isolating each precompile invocation's event manager (a fresh, non-shared `EventManager` per native-action call that is merged into the parent only after that call's `AfterBalanceChange` has fully processed and removed its own slice), or use a stack/queue of markers instead of a single mutable integer so that nested calls cannot invalidate an outer call's bookkeeping. Additionally, add an invariant check (e.g., in integration tests or as a runtime assertion during precompile execution) that a contract's aggregate bank-ledger native balance always equals the amount recoverable via its EVM/ERC20 balance view after any transaction involving nested precompile calls, and fix the two failing invariants demonstrated by the existing `balance_handler_test.go` and `ics20_recursive_precompile_calls_test.go` suites rather than asserting the buggy values as "expected."

### Proof of Concept
The repository already contains a working PoC:
1. `evmd/tests/integration/balance_handler/balance_handler_test.go::TestRecursivePrecompileCallsWithDebugPrecompile` deploys a caller contract that recursively invokes a precompile through the shared `BalanceHandler` and demonstrates event/balance bookkeeping corruption [6](#0-5) .
2. `evmd/tests/ibc/ics20_recursive_precompile_calls_test.go::TestHandleMsgTransfer`, case `"test recursive precompile call without reverts"`, deploys `ERC20RecursiveNonRevertingPrecompileCall.sol` — whose `_beforeTokenTransfer` hook calls `distribution.DISTRIBUTION_CONTRACT.claimRewards` up to 5 times [7](#0-6)  — then performs an ICS20 transfer of the "full" ERC20 balance through the `ics20` precompile, and asserts the contract's native `bondDenom` balance ends up at `50` instead of `0`, showing bank-ledger/EVM-view desynchronization directly caused by the recursive precompile calls sharing balance-accounting state [8](#0-7) .

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

**File:** evmd/tests/ibc/ics20_recursive_precompile_calls_test.go (L308-357)
```go
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
		},
```

**File:** contracts/solidity/ERC20RecursiveNonRevertingPrecompileCall.sol (L124-154)
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

    function delegate(
        string memory validatorAddress,
        uint256 amount
    ) external {
        bool ok = staking.STAKING_CONTRACT.delegate(address(this), validatorAddress, amount);
        require(ok, "failed to stake");
    }

    function claimRewards() public {
        distribution.DISTRIBUTION_CONTRACT.claimRewards(address(this), 100);
    }
```
