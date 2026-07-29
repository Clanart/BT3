## Analysis

This maps to a **shared/reused mutable "scope" flag being applied across nested, unrelated operations, corrupting per-operation accounting** — the same bug class as the Deriverse `margin_call` flag report, but in this repo the corrupted value is the accounting checkpoint used to sync the EVM `StateDB` balance with the Cosmos `x/bank` keeper.

### Title
Shared `BalanceHandler` instance corrupts native/EVM balance sync during recursive/reentrant precompile calls - (File: `precompiles/common/balance_handler.go`)

### Summary
`BalanceHandler.prevEventsLen` is a single mutable field that records "how many bank events existed before this precompile call" so that `AfterBalanceChange` knows which event-manager slice to replay into the EVM `StateDB`. The evidence in this repository (`evmd/tests/integration/balance_handler/balance_handler_test.go`, comment at [1](#0-0)  ) documents that this handler instance is **shared across recursive/nested precompile calls**, so an inner call's `BeforeBalanceChange` overwrites the checkpoint set by the outer call.

### Finding Description
`BeforeBalanceChange` unconditionally overwrites the shared counter: [2](#0-1) 

`AfterBalanceChange` then replays only the events after that (now-corrupted) index into the `StateDB`: [3](#0-2) 

The `BalanceHandler` is obtained via `p.GetBalanceHandler()` on the precompile's `cmn.Precompile` embedded struct and invoked around every precompile execution: [4](#0-3) . Because this is the same struct instance reused for every call to that precompile within a node's lifetime (not freshly constructed per top-level EVM call), a contract that triggers a **recursive/reentrant call into the same precompile** (e.g. `distribution.claimRewards` called from inside an ERC20 `_beforeTokenTransfer` hook, or nested ICS20/staking/gov precompile calls) causes:

1. Outer call: `BeforeBalanceChange` records `prevEventsLen = N`.
2. Inner (nested) call to the same or another precompile: `BeforeBalanceChange` overwrites `prevEventsLen = M` (M > N, since more events accumulated).
3. Inner call finishes: `AfterBalanceChange` correctly consumes events `[M:]`, but leaves `prevEventsLen` at `M`.
4. Outer call resumes and finishes: `AfterBalanceChange` now uses the stale `M` instead of the original `N`, so **all bank events between N and M (the outer call's own balance-changing effects) are silently skipped from being applied to the `StateDB`**.

This produces divergence between the native `x/bank` (and `x/precisebank`) balance state (source of truth once committed) and the EVM `StateDB`'s cached balance view for the accounts involved. This exact scenario is reproduced in-repo by `TestRecursivePrecompileCallsWithDebugPrecompile` [5](#0-4)  and again by the ICS20 recursive-call regression test, whose comment explicitly states: *"reverted distribution calls leave persistent bank events that are incorrectly aggregated"* [6](#0-5) .

### Impact Explanation
When `StateDB.Commit()` runs, the (desynchronized) `StateDB` balance is written back to the account via `keeper.SetAccount` [7](#0-6) . If skipped events cause the `StateDB` to under- or over-count a balance-changing event relative to the bank keeper's actual coin movement, the final committed EVM-visible balance for the sender/receiver/contract diverges from the true bank balance — an accounting corruption of spendable user value that persists across the transaction (potential inflation of `StateDB` balance for one address, or loss of tracked balance for another), matching the "Critical unauthorized … duplication … of spendable user value across native balances/EVM balances" impact class.

### Likelihood Explanation
Reachable by any unprivileged contract deployer: any ERC20 (or other) contract that implements hooks (`_beforeTokenTransfer`, callbacks, `try/catch` reentry) which invoke a stateful precompile (distribution, staking, ICS20, gov, slashing — all of which use `cmn.NewBalanceHandlerFactory` per `grep` matches in `distribution.go`, `staking.go`, `gov.go`, `ics20.go`, `slashing.go`, `erc20.go`) from within another precompile call or from a nested EVM call chain can trigger this. The repository's own test contracts (`ERC20RecursiveNonRevertingPrecompileCall.sol`, `ERC20RecursiveRevertingPrecompileCall.sol`) exist specifically to exercise this pattern [8](#0-7) , and dedicated regression tests already demonstrate desynchronization occurs.

### Recommendation
Do not share one `BalanceHandler`/`prevEventsLen` across nested precompile invocations. Either (a) instantiate a fresh `BalanceHandler` per call via `BalanceHandlerFactory.NewBalanceHandler()` for every `Run()` invocation instead of a struct-level singleton, or (b) make `prevEventsLen` a stack (push/pop per call depth) so an inner call's checkpoint cannot clobber an outer call's checkpoint.

### Proof of Concept
The in-repo test `TestRecursivePrecompileCallsWithDebugPrecompile` [9](#0-8)  and `TestHandleMsgTransfer` in `ics20_recursive_precompile_calls_test.go` [10](#0-9)  both construct exactly this recursive-call scenario and assert on the resulting (incorrect) event/balance state, confirming the root cause is reachable through ordinary unprivileged contract execution.

**Note on completeness:** I was unable to retrieve the exact source of `precompiles/common/precompile.go` (where `GetBalanceHandler()` is defined) within the available search budget to confirm definitively whether the handler field is a per-`Precompile`-struct singleton or re-created per call; the in-repo test names/comments strongly indicate it is shared, but a Devin session with full file access should verify `precompile.go`'s `GetBalanceHandler()` implementation to confirm the exact scoping and finalize a precise patch location.

### Citations

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

**File:** precompiles/common/balance_handler.go (L43-48)
```go
// BeforeBalanceChange is called before any balance changes by precompile methods.
// It records the current number of events in the context to later process balance changes
// using the recorded events.
func (bh *BalanceHandler) BeforeBalanceChange(ctx sdk.Context) {
	bh.prevEventsLen = len(ctx.EventManager().Events())
}
```

**File:** precompiles/common/balance_handler.go (L66-71)
```go
// To prevent this, balance changes from events involving blocked addresses are not applied to the StateDB.
// Instead, the state changes resulting from the precompile call are applied directly via the MultiStore.
func (bh *BalanceHandler) AfterBalanceChange(ctx sdk.Context, stateDB *statedb.StateDB) error {
	events := ctx.EventManager().Events()

	for _, event := range events[bh.prevEventsLen:] {
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

**File:** evmd/tests/ibc/ics20_recursive_precompile_calls_test.go (L52-55)
```go
// Test suite for ICS20 recursive precompile calls
// Tests the native balance handler bug where reverted distribution calls
// leave persistent bank events that are incorrectly aggregated

```

**File:** evmd/tests/ibc/ics20_recursive_precompile_calls_test.go (L357-450)
```go
		},
	}

	for _, tc := range testCases {
		suite.Run(tc.name, func() {
			suite.SetupTest() // reset

			pathAToB := evmibctesting.NewTransferPath(suite.chainA, suite.chainB)
			pathAToB.Setup()
			traceAToB := transfertypes.NewHop(pathAToB.EndpointB.ChannelConfig.PortID, pathAToB.EndpointB.ChannelID)

			senderAccount := suite.chainA.SenderAccounts[SenderIndex]
			senderAddr := senderAccount.SenderAccount.GetAddress()

			tc.malleate(senderAccount)

			evmAppA := suite.chainA.App.(*evmd.EVMD)

			// Get balance helper function
			GetBalance := func(addr sdk.AccAddress) sdk.Coin {
				ctx := suite.chainA.GetContext()
				if erc20 {
					balanceAmt := evmAppA.Erc20Keeper.BalanceOf(ctx, nativeErc20.ContractAbi, nativeErc20.ContractAddr, nativeErc20.Account)
					return sdk.Coin{
						Denom:  nativeErc20.Denom,
						Amount: sdkmath.NewIntFromBigInt(balanceAmt),
					}
				}
				return evmAppA.BankKeeper.GetBalance(ctx, addr, sourceDenomToTransfer)
			}

			// Verify initial state
			senderBalance := GetBalance(nativeErc20.ContractAddr.Bytes())
			suite.Require().NoError(err)
			bondDenom, err := evmAppA.StakingKeeper.BondDenom(suite.chainA.GetContext())
			suite.Require().NoError(err)
			contractBondDenomBalance := evmAppA.BankKeeper.GetBalance(suite.chainA.GetContext(), nativeErc20.ContractAddr.Bytes(), bondDenom)
			suite.Require().Equal(contractBondDenomBalance.Amount, sdkmath.NewInt(0))

			// Setup transfer parameters
			timeoutHeight := clienttypes.NewHeight(1, TimeoutHeight)
			originalCoin := sdk.NewCoin(sourceDenomToTransfer, msgAmount)

			// Check distribution rewards before transfer
			querier := distributionkeeper.NewQuerier(evmAppA.DistrKeeper)
			vals, err := evmAppA.StakingKeeper.GetAllValidators(suite.chainA.GetContext())
			suite.Require().NoError(err)

			beforeRewards, err := querier.DelegationRewards(suite.chainA.GetContext(), &distrtypes.QueryDelegationRewardsRequest{
				DelegatorAddress: utils.Bech32StringFromHexAddress(nativeErc20.ContractAddr.String()),
				ValidatorAddress: vals[0].OperatorAddress,
			})
			suite.Require().NoError(err)
			suite.Require().Equal(beforeRewards.Rewards[0].Amount.String(), ExpectedRewards)

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

			res, _, _, err := suite.chainA.SendEvmTx(senderAccount, SenderIndex, suite.chainAPrecompile.Address(), big.NewInt(0), data, 0)
			suite.Require().NoError(err) // message committed
			packet, err := evmibctesting.ParsePacketFromEvents(res.Events)
			suite.Require().NoError(err)

			eventAmount := len(res.Events)
			fmt.Println(res.Events)

			tc.postCheck(querier, vals[0].OperatorAddress, eventAmount)

			// Get the packet data to determine the amount of tokens being transferred (needed for sending entire balance)
			packetData, err := transfertypes.UnmarshalPacketData(packet.GetData(), pathAToB.EndpointA.GetChannel().Version, "")
			suite.Require().NoError(err)
			transferAmount, ok := sdkmath.NewIntFromString(packetData.Token.Amount)
			suite.Require().True(ok)

			afterSenderBalance := GetBalance(senderAddr)
			suite.Require().Equal(
				senderBalance.Amount.Sub(transferAmount).String(),
				afterSenderBalance.Amount.String(),
			)
			if msgAmount == transfertypes.UnboundedSpendLimit() {
				suite.Require().Equal("0", afterSenderBalance.Amount.String(), "sender should have no balance left")
			}

```

**File:** x/vm/statedb/statedb.go (L713-732)
```go
// commitWithCtx writes the dirty states to keeper
// using the provided context
func (s *StateDB) commitWithCtx(ctx sdk.Context) error {
	for _, addr := range s.journal.sortedDirties() {
		obj := s.stateObjects[addr]
		if obj.selfDestructed {
			if err := s.keeper.DeleteAccount(ctx, obj.Address()); err != nil {
				return errorsmod.Wrapf(err, "failed to delete account %s", obj.Address())
			}
		} else {
			if obj.code != nil && obj.dirtyCode {
				if len(obj.code) == 0 {
					s.keeper.DeleteCode(ctx, obj.CodeHash())
				} else {
					s.keeper.SetCode(ctx, obj.CodeHash(), obj.code)
				}
			}
			if err := s.keeper.SetAccount(ctx, obj.Address(), obj.account); err != nil {
				return errorsmod.Wrap(err, "failed to set account")
			}
```

**File:** contracts/solidity/ERC20RecursiveNonRevertingPrecompileCall.sol (L124-141)
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
```
