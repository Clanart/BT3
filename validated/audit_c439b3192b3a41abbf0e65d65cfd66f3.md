Found `p.GetBalanceHandler()` — meaning the debug precompile (and by the pattern shown, potentially other precompiles) keeps the `BalanceHandler` as a **shared instance retrieved via a getter rather than instantiated fresh per `Run()` call**, unlike the pattern in `precompiles/common/precompile.go` `runNativeAction` (which creates a fresh `balanceHandler := p.BalanceHandlerFactory.NewBalanceHandler()` local to each invocation) [1](#0-0) . This is exactly the class of bug the repo's own test suite documents and labels "the balance handler bug" [2](#0-1) .

### Title
Shared/overwritten `BalanceHandler.prevEventsLen` across nested precompile calls causes StateDB/bank balance desync — (File: `precompiles/common/balance_handler.go`, `precompiles/common/precompile.go`)

### Summary
`BalanceHandler` records the bank event-log length before a precompile executes (`BeforeBalanceChange`) and replays only the events emitted after that point (`AfterBalanceChange`) to update the EVM `StateDB` balances [3](#0-2) . When a precompile call triggers a **nested/recursive precompile call** (e.g., an ERC20 `_beforeTokenTransfer` hook calling back into a precompile, or a precompile calling `CallEVM`/`CallEVMWithData` which re-enters another precompile), and that inner call reuses the *same* `BalanceHandler` instance instead of getting a fresh one, the inner call's `BeforeBalanceChange` overwrites `prevEventsLen` to a later index. When the *outer* call's `AfterBalanceChange` finally runs, it only replays events from the inner call's (later) `prevEventsLen` onward, silently dropping the outer call's own bank events from being applied to `StateDB`.

### Finding Description
This is the direct Cosmos EVM analog of the Reserve `[M-12]` bug class: the Reserve bug exploited **stale/decoupled valuation state** (previous basket composition vs. current price) that a downstream check assumed was still consistent. Here, the downstream invariant assumed to hold is "the events recorded between `BeforeBalanceChange` and `AfterBalanceChange` for a given precompile invocation exactly correspond to that invocation's own bank-keeper side effects." Nested precompile re-entrancy invalidates this assumption in the same way an upward depeg invalidated the Reserve Protocol's basket-value assumption — the check doesn't verify freshness/ownership of the referenced state, it just replays "whatever's since the last recorded position," which can now belong to an unrelated (inner) call.

Concretely:
- `precompiles/common/precompile.go`'s `runNativeAction` creates a new `BalanceHandler` per `Run()` invocation via `p.BalanceHandlerFactory.NewBalanceHandler()` [1](#0-0) , which is safe if each precompile instance always allocates fresh state per call.
- The debug precompile, however, calls `p.GetBalanceHandler()` [4](#0-3) [5](#0-4) , implying a handler obtained from the base `Precompile` struct rather than freshly constructed per call — the exact pattern flagged by the repo's own regression test as causing `prevEventsLen` to be overwritten across recursive calls [2](#0-1) .
- The related `ics20_recursive_precompile_calls_test.go` test suite explicitly documents this pattern for production precompiles: "Tests the native balance handler bug where reverted distribution calls leave persistent bank events that are incorrectly aggregated" [6](#0-5) , triggered via an ERC20 token with a recursive `_beforeTokenTransfer` hook that calls back into the staking/distribution precompiles during an ICS20 transfer [7](#0-6) .

### Impact Explanation
If `AfterBalanceChange` replays the wrong (later) window of events, or misses events belonging to the outer call, the EVM `StateDB`'s view of an account's native balance can diverge from the actual `x/bank` keeper balance — the accounting corruption the Allowed Impact Gate explicitly calls out ("irreversible accounting corruption... across native balances, EVM balances... escrowed assets"). Since `StateDB` balances are what subsequent EVM operations (transfers, `CALL` with value, `SELFBALANCE`) read and act on within the same transaction, and what gets committed to state, this can let a caller either (a) have EVM-visible balance understate/overstate the true bank balance, enabling double-spend-like extraction of value in a later operation in the same call, or (b) desynchronize balances between bank and EVM such that funds become permanently stuck or duplicated after commit.

### Likelihood Explanation
Triggering nested precompile calls is achievable by an unprivileged user: deploying an ERC20 contract with a transfer hook (`_beforeTokenTransfer`) that calls back into a precompile (staking/distribution/bank) during an ICS20 transfer of that token, as the repository's own test harness demonstrates is a realistic, buildable scenario using standard tooling (`ERC20RecursiveReverting`/`ERC20RecursiveNonReverting` contracts) [8](#0-7) . No privileged role, relayer collusion, or governance action is required.

### Recommendation
Ensure every precompile allocates and uses a `BalanceHandler` instance scoped strictly to its own `Run()` invocation (as `precompiles/common/precompile.go`'s `runNativeAction` already does via `p.BalanceHandlerFactory.NewBalanceHandler()`), never via a shared/cached getter such as `p.GetBalanceHandler()` that could return a handler mutated by a re-entrant inner call. Additionally, `AfterBalanceChange` should validate that the event window it processes is exactly the one produced by its own call (e.g., by snapshotting and restoring `prevEventsLen` around nested invocations, or using a call-stack-aware handler) rather than trusting a single mutable `prevEventsLen` field shared across the call stack.

### Proof of Concept
The repository already contains a reproducing regression test: `evmd/tests/integration/balance_handler/balance_handler_test.go`'s `TestRecursivePrecompileCallsWithDebugPrecompile`, which deploys a contract that recursively calls back into a debug precompile and asserts on the resulting event/balance-handler counts [9](#0-8) , and `evmd/tests/ibc/ics20_recursive_precompile_calls_test.go`, which reproduces the same class of issue in production precompiles (staking/distribution) via an ERC20 with a reentrant `_beforeTokenTransfer` hook during an ICS20 transfer [7](#0-6) .

**Note on confidence**: I could not fully trace whether `p.GetBalanceHandler()` in the debug precompile is a leftover test-only artifact (`testutil/testdata/debug` is explicitly marked "not for use in production" [10](#0-9) ) versus a pattern also present in a production precompile's dispatch path, nor whether the current state of `x/vm/statedb`'s `AddPrecompileFn`/journal snapshot mechanism already neutralizes the desync on revert. The index does not contain the full `x/vm/statedb` journal implementation or the exact production precompile that shares `BalanceHandler` state, so I cannot confirm whether this is a currently *open* Critical-severity bug or one already mitigated elsewhere (e.g., by the snapshot/revert journal). I recommend a Devin session with full repository/terminal access to trace `stateDB.AddPrecompileFn`, `CommitWithCacheCtx`, and the precompile dispatch call stack (`x/vm/keeper` `EVMCall`) to confirm whether the balance desync survives to a committed block state or is fully reverted by the existing snapshot mechanism before declaring this exploitable end-to-end.

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

**File:** precompiles/common/balance_handler.go (L43-68)
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
```

**File:** testutil/testdata/debug/debug.go (L1-1)
```go
// Package debug defines test utilities that are meant for debugging the chain, and *not* for use in production.
```

**File:** testutil/testdata/debug/debug.go (L78-78)
```go
	p.GetBalanceHandler().BeforeBalanceChange(ctx)
```

**File:** testutil/testdata/debug/debug.go (L110-110)
```go
	if err := p.GetBalanceHandler().AfterBalanceChange(ctx, stateDB); err != nil {
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
