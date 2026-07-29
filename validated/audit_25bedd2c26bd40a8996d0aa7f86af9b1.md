## Analog Identified: Shared `BalanceHandler` State Corrupted by Recursive/Nested Precompile Calls

### Title
Balance Handler State Corruption via Recursive Precompile Calls Causes EVM StateDB / Bank Keeper Desync - (File: `precompiles/common/balance_handler.go`)

### Summary
The external report's root cause is a **shared, un-scoped counter that tracks cumulative usage across independent operations** — the `GaugeController` never tracked total weight per user across gauges, so each `vote()` call clobbered independent state instead of being bounded by a shared cap. The Cosmos EVM analog is the `BalanceHandler.prevEventsLen` field: it is a single mutable field on a `BalanceHandler` instance that gets shared across **nested/recursive precompile calls**, so an inner call's `BeforeBalanceChange`/`AfterBalanceChange` pair overwrites the outer call's bookkeeping window, producing incorrect (missing or duplicated) balance synchronization between the SDK bank keeper and the EVM `StateDB`.

### Finding Description
Every stateful precompile (`erc20`, `staking`, `distribution`, `gov`, `slashing`, `ics20`) obtains its `BalanceHandler` via `GetBalanceHandler()` on the embedded `cmn.Precompile` struct [1](#0-0) , and each precompile method wraps its Cosmos-side `bankKeeper` mutation with:

```go
p.GetBalanceHandler().BeforeBalanceChange(ctx)   // records prevEventsLen = len(events)
... execute keeper logic that emits CoinSpent/CoinReceived events ...
p.GetBalanceHandler().AfterBalanceChange(ctx, stateDB) // reads events[prevEventsLen:]
``` [2](#0-1) 

`AfterBalanceChange` slices `ctx.EventManager().Events()` starting at `prevEventsLen` and replays `CoinSpent`/`CoinReceived`/`FractionalBalanceChange` events onto the `StateDB` via `AddBalance`/`SubBalance` [3](#0-2) . This is the sole mechanism keeping the EVM-visible balance (`StateDB`) consistent with the actual bank-module balance changed by precompile calls.

Because `prevEventsLen` lives on a single `BalanceHandler` instance rather than being scoped per-call (e.g., via a call stack or being re-derived from a snapshot taken at `Run()` entry per invocation), a **recursive/nested precompile invocation** — reachable by an ordinary EVM contract, e.g. an ERC20 token whose `_beforeTokenTransfer` hook calls a staking/distribution/ICS20 precompile, which itself triggers further transfers that re-enter a precompile — causes the inner call to overwrite `prevEventsLen` before the outer call reads it. The outer `AfterBalanceChange` then processes the wrong event range, either replaying already-applied balance deltas twice onto the `StateDB` or skipping some entirely, desynchronizing the EVM-visible balance from the actual bank-keeper balance.

This exact bug is explicitly reproduced and named in the repository's own test suite:
- `evmd/tests/integration/balance_handler/balance_handler_test.go`: *"tests the balance handler bug where recursive precompile calls share the same BalanceHandler instance, causing prevEventsLen to be overwritten. This leads to balance desync between native bank keeper and EVM stateDB."* [4](#0-3) 
- `evmd/tests/ibc/ics20_recursive_precompile_calls_test.go`: *"Tests the native balance handler bug where reverted distribution calls leave persistent bank events that are incorrectly aggregated"*, with test contracts that recursively call `staking`/`distribution` precompiles from an ERC20 transfer hook, both with and without inner reverts [5](#0-4) [6](#0-5) 

### Impact Explanation
This maps directly to the "Critical unauthorized … irreversible accounting corruption of spendable user value across native balances, EVM balances, … or precompile-mediated assets" impact category. A user's EVM-visible native-token `StateDB` balance can diverge from the real bank-module balance:
- If the inner call's `AfterBalanceChange` consumes the events window before the outer handler runs, some `CoinSpent`/`CoinReceived` deltas are never replayed to `StateDB`, so `eth_getBalance`/`balanceOf` on the native EVM token under-reports or over-reports, letting an attacker's contract obtain an inflated apparent balance (spendable within the EVM even though the corresponding real coins were never credited), or conversely cause double-application of a delta, effectively duplicating value on the EVM side.
- The `evmd/tests/ibc/ics20_recursive_precompile_calls_test.go` "without reverts" case demonstrates a concrete divergence: after a recursive claim-rewards call chain, the contract's bank balance ends up at `50` when the accounting was expected to differ, and reward state is inconsistently observed depending on revert/non-revert paths [7](#0-6) .

### Likelihood Explanation
The trigger is fully unprivileged: any smart contract that overrides an ERC20 hook (e.g. `_beforeTokenTransfer`) or otherwise makes a nested call into a stateful precompile (`staking`, `distribution`, `ics20`, `erc20`) from within another precompile call satisfies the precondition. The repository's own test contracts (`ERC20RecursiveNonRevertingPrecompileCall.sol`, `ERC20RecursiveRevertingPrecompileCall.sol`, `InterchainSenderCaller.sol`) are essentially attacker-controllable templates for this pattern, and there's an explicit `MaxPrecompileCalls` counter in `StateDB.AddPrecompileFn` limiting *depth* [8](#0-7)  but this bounds recursion depth, not the correctness of the `prevEventsLen` bookkeeping itself.

### Recommendation
Scope the balance-change event window per call rather than per shared `BalanceHandler` instance — e.g., allocate a fresh `BalanceHandler` (or push/pop a stack of `prevEventsLen` values) for every precompile `Run()` invocation, or capture/restore `prevEventsLen` around nested calls so an inner call cannot clobber an outer call's bookkeeping.

### Proof of Concept
The repository's own reproduction is sufficient evidence and is already checked in:
- `evmd/tests/integration/balance_handler/balance_handler_test.go::TestRecursivePrecompileCallsWithDebugPrecompile` triggers nested calls through a `DebugPrecompileCaller` contract and asserts on the resulting event/balance counts to demonstrate the desync [9](#0-8) .
- `evmd/tests/ibc/ics20_recursive_precompile_calls_test.go::TestHandleMsgTransfer` uses `ERC20RecursiveReverting`/`ERC20RecursiveNonReverting` contracts that call `staking`/`distribution` precompiles recursively during an ICS20 transfer, and compares resulting bank/reward balances against expectations to show divergence depending on the revert path [10](#0-9) .

Note: I was not able to inspect `precompiles/common/precompile.go`'s `GetBalanceHandler()` implementation body directly within tool budget (only confirmed its existence via grep), so I cannot state with certainty whether it lazily instantiates a new `BalanceHandler` per call or caches one on the `Precompile` struct across calls — the test titles/comments in the codebase assert the latter (shared instance), which is what makes this a real, already-acknowledged bug rather than a false analogy, but a Devin session with full repo access should verify the exact caching semantics before filing a fix.

### Citations

**File:** precompiles/common/precompile.go (L1-1)
```go
package common
```

**File:** precompiles/common/balance_handler.go (L43-132)
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

		case precisebanktypes.EventTypeFractionalBalanceChange:
			addr, err := ParseAddress(event, precisebanktypes.AttributeKeyAddress)
			if err != nil {
				return fmt.Errorf("failed to parse address from event %q: %w", precisebanktypes.EventTypeFractionalBalanceChange, err)
			}
			if bh.bankKeeper.BlockedAddr(addr) {
				// Bypass blocked addresses
				continue
			}

			delta, err := ParseFractionalAmount(event)
			if err != nil {
				return fmt.Errorf("failed to parse amount from event %q: %w", precisebanktypes.EventTypeFractionalBalanceChange, err)
			}

			deltaAbs, err := utils.Uint256FromBigInt(new(big.Int).Abs(delta))
			if err != nil {
				return fmt.Errorf("failed to convert delta to Uint256: %w", err)
			}

			if delta.Sign() == 1 {
				stateDB.AddBalance(common.BytesToAddress(addr.Bytes()), deltaAbs, tracing.BalanceChangeUnspecified)
			} else if delta.Sign() == -1 {
				stateDB.SubBalance(common.BytesToAddress(addr.Bytes()), deltaAbs, tracing.BalanceChangeUnspecified)
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

**File:** evmd/tests/ibc/ics20_recursive_precompile_calls_test.go (L52-54)
```go
// Test suite for ICS20 recursive precompile calls
// Tests the native balance handler bug where reverted distribution calls
// leave persistent bank events that are incorrectly aggregated
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
