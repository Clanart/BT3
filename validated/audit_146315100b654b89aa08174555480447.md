### Title
Recursive/reentrant precompile calls can desynchronize EVM StateDB balances from the Bank module ledger via BalanceHandler event-window overlap - (File: precompiles/common/balance_handler.go, precompiles/common/precompile.go)

### Summary
The Fuel report describes a base-asset transfer that is silently dropped because the bridge's accounting path (message-predicate value transfer) is decoupled from the actual crediting logic (`transfer`/`process_message`), trapping value that the low-level mechanism already moved. The Cosmos EVM analog is the `BalanceHandler` used by native precompiles (`ics20`, `erc20`, `staking`, `distribution`, `gov`, `slashing`, `werc20`) to reconcile Bank-module coin-spent/coin-received events into the EVM `StateDB`. This handler records a "before" event-index (`prevEventsLen`) and later replays only the events emitted *after* that index into `StateDB.AddBalance`/`SubBalance`. When a precompile's native action itself triggers another precompile call (recursion/reentrancy — e.g., an ERC20 `_beforeTokenTransfer` hook or nested `CallEVM` invocation during an ICS20 transfer), both the outer and inner calls read/write against the **same, monotonically growing** `ctx.EventManager()` event log. This creates an overlapping "window" of bank events that can be processed more than once (or the wrong slice can be processed), producing a mismatch between what the Bank keeper actually holds and what `StateDB` reflects for a given address — i.e., the EVM-visible balance diverges from the ledger balance, exactly like the trapped/miscredited base-asset scenario in the report, but manifesting as duplicated or lost native-token balance in `StateDB`.

### Finding Description
Each precompile invocation that declares a `BalanceHandlerFactory` gets a `BalanceHandler` via `runNativeAction`: [1](#0-0) 

`BeforeBalanceChange` snapshots the current length of `ctx.EventManager().Events()`: [2](#0-1) 

`AfterBalanceChange` then replays `events[bh.prevEventsLen:]` into the EVM `StateDB`, converting bank `coin_spent`/`coin_received` events into `SubBalance`/`AddBalance` calls: [3](#0-2) 

Because `ctx.EventManager()` is a single, ever-growing log shared across the whole EVM call frame (not scoped per precompile invocation), any native action that re-enters the EVM and triggers a second precompile call (e.g., an ERC20 hook that calls back into a precompile, or the recursive-call test scenario) causes bank events to be produced while the outer handler's window is still open. Depending on ordering, the inner handler's `AfterBalanceChange` can consume (and apply to `StateDB`) events that the outer handler will also see and re-apply when it later runs `AfterBalanceChange` on `events[prevEventsLenOuter:]`, since that slice still includes everything already consumed by the inner call. This produces double-application of a balance delta to `StateDB` (or, in other orderings, dropped deltas), causing the EVM-visible balance to lose 1:1 correspondence with the actual Bank-module coin balance — the same class of "the low-level value movement happened, but the higher-level accounting layer didn't attribute it correctly" bug as the Fuel bridge report.

This exact bug class is called out directly in the repository's own test suite: [4](#0-3) 
and reproduced via a debug precompile invoked recursively through a caller contract: [5](#0-4) 

A second, independent regression test targets the same mechanism specifically for the ICS20 precompile with recursive/reverting ERC20 hooks, explicitly commented as reproducing "the bug": [6](#0-5) [7](#0-6) 

### Impact Explanation
If the event-window overlap causes `StateDB.AddBalance`/`SubBalance` to be invoked more times (or fewer times) than the corresponding Bank-module state change, an unprivileged EVM user could cause their own (or another account's) EVM-visible native balance to diverge from the actual spendable Bank balance. A positive divergence (StateDB credited without matching Bank debit/credit) is unauthorized duplication of spendable value directly in the EVM balance ledger, exploitable to extract or spend value that was never actually escrowed/received — matching the "Critical unauthorized minting/duplication/irreversible accounting corruption of spendable user value across native balances, EVM balances" impact class. A negative divergence would instead freeze/lose value that the Bank module correctly holds but the EVM no longer reflects as spendable, matching the "permanent freezing/locking of user funds" impact class.

### Likelihood Explanation
Triggering the condition requires a native action performed by one of the `BalanceHandler`-enabled precompiles (`ics20`, `erc20`, `staking`, `distribution`, `gov`, `slashing`) to re-enter the EVM and hit a second such precompile call within the same top-level transaction — e.g., an ERC20 token with a transfer hook that calls back into `ics20`/`staking`/etc., or any user-deployed contract that chains precompile calls. This is fully reachable by an ordinary, unprivileged EVM transaction/contract and requires no validator, relayer, or governance privilege — it only requires deploying or using a contract that performs a nested precompile call, which the repository's own test helpers (`ERC20RecursiveReverting`/`ERC20RecursiveNonReverting`, `DebugPrecompileCaller`) demonstrate is straightforward to construct.

### Recommendation
Scope `BalanceHandler` event tracking per precompile call frame instead of relying on a single shared, monotonically increasing `ctx.EventManager()` index shared across nested precompile invocations — e.g., use a stack of `(prevEventsLen, consumedUpTo)` markers, or have each `AfterBalanceChange` mark the events it has consumed so an outer handler never re-processes events already applied by an inner handler. Add invariant checks that assert, after all balance-handler replays for a top-level EVM transaction, that the EVM `StateDB` native balance for every touched address exactly matches the Bank-module balance, failing/reverting the transaction if a mismatch is detected.

### Proof of Concept
The repository already contains a runnable reproduction of the underlying mechanism: [8](#0-7) 
which deploys a contract that recursively calls a debug precompile and asserts on the resulting event/balance-processing count, and: [9](#0-8) 
which drives an ICS20 transfer of a native ERC20 whose `_beforeTokenTransfer` hook recursively triggers additional precompile-mediated state changes (staking delegation/reward distribution) during the same call, and checks the resulting Bank-vs-StateDB balances/rewards for consistency. A background engineer should extend these tests to explicitly compare `StateDB.GetBalance` against `BankKeeper.GetBalance` for all involved addresses after nested/reentrant precompile calls to confirm whether a divergence (duplication or loss) is currently produced in a specific ordering, since I was unable to execute the test suite to confirm exploitability in this session — this should be treated as a strong analog requiring runtime confirmation rather than a fully proven exploit chain.

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

**File:** evmd/tests/integration/balance_handler/balance_handler_test.go (L23-26)
```go
// BalanceHandlerTestSuite tests the balance handler bug where recursive precompile calls
// share the same BalanceHandler instance, causing prevEventsLen to be overwritten.
// This leads to balance desync between native bank keeper and EVM stateDB.
type BalanceHandlerTestSuite struct {
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

**File:** evmd/tests/ibc/ics20_recursive_precompile_calls_test.go (L52-55)
```go
// Test suite for ICS20 recursive precompile calls
// Tests the native balance handler bug where reverted distribution calls
// leave persistent bank events that are incorrectly aggregated

```

**File:** evmd/tests/ibc/ics20_recursive_precompile_calls_test.go (L244-357)
```go
func (suite *ICS20RecursivePrecompileCallsTestSuite) TestHandleMsgTransfer() {
	var (
		sourceDenomToTransfer string
		msgAmount             sdkmath.Int
		err                   error
		nativeErc20           *NativeErc20Info
		erc20                 bool
	)

	// originally a basic test case from the IBC testing package, and it has been added as-is to ensure that
	// it still works properly when invoked through the ics20 precompile.
	testCases := []struct {
		name      string
		malleate  func(senderAcc evmibctesting.SenderAccount)
		postCheck func(querier distributionkeeper.Querier, valAddr string, eventAmount int)
	}{
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
		},
```

**File:** evmd/tests/ibc/ics20_recursive_precompile_calls_test.go (L412-424)
```go
			// Execute ICS20 transfer (this triggers the bug)
			data, err := suite.chainAPrecompile.Pack("transfer",
				pathAToB.EndpointA.ChannelConfig.PortID,
				pathAToB.EndpointA.ChannelID,
				originalCoin.Denom,
				originalCoin.Amount.BigInt(),
				common.BytesToAddress(senderAddr.Bytes()),        // source addr should be evm hex addr
				suite.chainB.SenderAccount.GetAddress().String(), // receiver should be cosmos bech32 addr
				timeoutHeight,
				uint64(0),
				"",
			)
			suite.Require().NoError(err)
```
