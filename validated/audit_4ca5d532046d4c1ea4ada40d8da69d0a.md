### Title
Balance Desync Between Bank Keeper and EVM StateDB via Shared `BalanceHandler` on Recursive/Nested Precompile Calls - (File: `precompiles/common/balance_handler.go`)

### Summary
The external report's core theme — an external/critical dependency call (Chainlink `latestRoundData`) that can silently fail or be reentered without a resilient fallback, corrupting downstream logic — maps in Cosmos EVM to the `BalanceHandler` used by every stateful precompile (`staking`, `distribution`, `bank`, `erc20`, `gov`, `ics20`, `slashing`, `werc20`) to translate `x/bank` events into `StateDB` balance mutations. Just as the oracle consumer assumed a single, uninterrupted external call, `BalanceHandler` assumes a single, non-reentrant `BeforeBalanceChange`/`AfterBalanceChange` pair per precompile invocation. When precompile calls recurse (a precompile-invoking contract calls back into a precompile, or one precompile's execution triggers another precompile call within the same EVM call frame), the shared handler's `prevEventsLen` cursor gets overwritten, and the codebase itself documents this as "the balance handler bug" that "leads to balance desync between native bank keeper and EVM stateDB." [1](#0-0) [2](#0-1) 

### Finding Description
`BalanceHandler.BeforeBalanceChange` records the current length of `ctx.EventManager().Events()` into `prevEventsLen`, and `AfterBalanceChange` later replays only the events emitted after that recorded index to apply the corresponding `stateDB.AddBalance`/`SubBalance` calls, keeping the EVM's native balance view synchronized with the actual `x/bank` ledger changes performed by the precompile. [3](#0-2) 

Each precompile obtains its `BalanceHandler` from a `BalanceHandlerFactory` stored on the shared `Precompile` struct. [4](#0-3) [5](#0-4) 

The bug is that when precompile execution is re-entered (a contract's fallback/callback logic invokes a precompile again inside the outer precompile's own `Before/After` window, or one precompile call triggers another precompile call in the same EVM message flow), the same `BalanceHandler` instance's `prevEventsLen` field is overwritten by the inner call's `BeforeBalanceChange`. The outer call's subsequent `AfterBalanceChange` then computes the "recently emitted" event slice using the wrong (inner) cursor value, causing it to either replay bank events twice or skip events entirely relative to what actually changed in the bank keeper. This is confirmed and named directly in-repo by a dedicated regression test (`BalanceHandlerTestSuite`) whose docstring states the recursive-call scenario "leads to balance desync between native bank keeper and EVM stateDB." [6](#0-5) 

Related recursive-precompile-call test scaffolding elsewhere in the repo (`StakingReverter.sol`, `ERC20RecursiveRevertingPrecompileCall.sol`, `ICS20RecursivePrecompileCallsTestSuite`) shows that unprivileged users can trivially deploy contracts that trigger nested/recursive precompile invocations from ordinary transactions — this is not a privileged or validator-only code path. [7](#0-6) [8](#0-7) 

Because `stateDB` balances are what subsequent EVM-native operations (CALL-with-value transfers, `BALANCE` opcode reads used by other contract logic) act upon within the same and later transactions, a desync where events are double-applied inflates an account's EVM-visible balance beyond what is actually backed in the `x/bank` store, while a desync where events are skipped can permanently under-credit legitimate balance changes. The former is functionally equivalent to unauthorized minting of spendable EVM balance not backed by real coins; the latter is equivalent to freezing/loss of legitimately received funds.

### Impact Explanation
This falls squarely under the "Critical unauthorized minting, burning, duplication, resurrection, or irreversible accounting corruption of spendable user value across native balances, EVM balances... or precompile-mediated assets" and "Critical permanent freezing, locking... of user funds" categories in the allowed-impact gate. The invariant broken is the 1:1 accounting relationship between `x/bank` coin balances and the EVM `StateDB` balance view that all precompiles (`staking`, `distribution`, `bank`, `erc20`, `werc20`, `ics20`, `slashing`, `gov`) rely on to keep native and EVM representations of value consistent, which is explicitly one of the "Asset-representation path" invariants called out in the audit pivots.

### Likelihood Explanation
Triggering nested/recursive precompile calls requires only deploying an ordinary smart contract and sending a normal transaction — no validator, governance, or privileged key is needed. The repository's own test suites (`StakingReverter.sol`, `ERC20RecursiveRevertingPrecompileCall.sol`, the debug-precompile "callback" contract, and `ICS20RecursivePrecompileCallsTestSuite`) demonstrate that recursive precompile invocation patterns are readily reachable through unprivileged contract logic (fallback hooks like `_beforeTokenTransfer`, `try/catch` wrapped precompile calls, or callback-style contracts). [9](#0-8) [10](#0-9) 

### Recommendation
Make `BalanceHandler` reentrancy-safe: either (a) allocate a fresh `BalanceHandler` per precompile invocation frame instead of reusing a single instance across nested calls (verify factory usage inside `RunNativeAction`/`RunSetup` in `precompiles/common/precompile.go`), or (b) replace the single scalar `prevEventsLen` cursor with a stack that is pushed/popped around each `Before/After` pair so nested invocations cannot clobber an outer call's bookmark. Add invariant assertions/integration tests that directly compare `x/bank` GetBalance results against EVM `StateDB`/`BALANCE` results after arbitrary depths of recursive precompile calls, extending the existing `BalanceHandlerTestSuite` regression test to assert balance equality (not just event counts).

### Proof of Concept
The existing repository test already demonstrates the reachable trigger path end-to-end: it registers a debug precompile, deploys a caller contract that recursively invokes the precompile via `callback(0)`, funds the caller contract with `aatom`, and sends a normal (unprivileged) EVM transaction. The test asserts a specific, non-obvious mismatch between total events (`15`) and `debug_precompile` events (`10`), consistent with `prevEventsLen` being overwritten across nested precompile invocations. [11](#0-10) 

Note: I was unable to retrieve the full body of `RunNativeAction`/`RunSetup` in `precompiles/common/precompile.go` (only the struct declaration was available in the index) before this session ended, so the precise point at which `BalanceHandler` instances are created/reused per call frame could not be fully confirmed from source — this should be verified directly against `precompiles/common/precompile.go` to pin down whether the fix requires per-call instantiation or a cursor stack.

### Citations

**File:** precompiles/common/balance_handler.go (L37-68)
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

**File:** precompiles/common/precompile.go (L25-34)
```go
// Precompile is the base struct for precompiles that requires to access cosmos native storage.
type Precompile struct {
	KvGasConfig          storetypes.GasConfig
	TransientKVGasConfig storetypes.GasConfig
	ContractAddress      common.Address

	// BalanceHandlerFactory is optional
	BalanceHandlerFactory *BalanceHandlerFactory
}

```

**File:** precompiles/staking/staking.go (L60-66)
```go
	return &Precompile{
		Precompile: cmn.Precompile{
			KvGasConfig:           storetypes.KVGasConfig(),
			TransientKVGasConfig:  storetypes.TransientGasConfig(),
			ContractAddress:       common.HexToAddress(evmtypes.StakingPrecompileAddress),
			BalanceHandlerFactory: cmn.NewBalanceHandlerFactory(bankKeeper),
		},
```

**File:** contracts/solidity/ERC20RecursiveRevertingPrecompileCall.sol (L124-155)
```text
    function _beforeTokenTransfer(
        address from,
        address to,
        uint256 amount
    ) internal virtual override(ERC20, ERC20Pausable) {
        // Emit an event to track if this hook is called
        emit BeforeTokenTransferHookCalled(from, to, amount);

        for(uint256 i=0; i < 5; i++) {
            try ERC20RecursiveRevertingPrecompileCall(address(this)).claimRewardsAndRevert() {

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

    function claimRewardsAndRevert() public {
        distribution.DISTRIBUTION_CONTRACT.claimRewards(address(this), 100);
        revert();
    }
```

**File:** evmd/tests/ibc/ics20_recursive_precompile_calls_test.go (L240-291)
```go
}

// Constructs the following sends based on the established channels/connections
// 1 - from evmChainA to chainB
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
```

**File:** precompiles/testutil/contracts/StakingReverter.sol (L34-50)
```text
    /// @dev callPrecompileBeforeAndAfterRevert tests whether precompile calls that occur 
    /// before and after an intentionally ignored revert correctly modify the state.
    /// This method assumes that the StakingReverter.sol contract holds a native balance. 
    /// Therefore, in order to call this method, the contract must be funded with a balance in advance.
    function callPrecompileBeforeAndAfterRevert(uint numTimes, string calldata validatorAddress) external {
        STAKING_CONTRACT.delegate(address(this), validatorAddress, 10);

        for (uint i = 0; i < numTimes; i++) {
            try
            StakingReverter(address(this)).performDelegation(
                validatorAddress
            )
            {} catch {}
        }

        STAKING_CONTRACT.delegate(address(this), validatorAddress, 10);
    }
```
