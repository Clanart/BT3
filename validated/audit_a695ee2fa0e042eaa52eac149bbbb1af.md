## Analysis Result

The Fluid `FluidVaultT1` bug is a **stale-cache-after-reentrancy** pattern: a value is snapshotted before an operation, an external call is allowed to mutate real state mid-flight, and the stale snapshot is written back afterward, clobbering (or duplicating) the intervening state change. The direct Cosmos EVM analog is in the **precompile balance-tracking path** (`x/vm` precompile execution / `BalanceHandler`), not in `x/precisebank` (out of scope) but in the shared native-action wrapper `precompiles/common/precompile.go`.

### Title
Nested/recursive precompile calls cause double-application of bank balance deltas to the EVM StateDB via `BalanceHandler` event-window overlap - (File: `precompiles/common/balance_handler.go`, `precompiles/common/precompile.go`)

### Summary
`Precompile.runNativeAction` records the current length of `ctx.EventManager().Events()` in a `BalanceHandler` (`BeforeBalanceChange`) before invoking the native action, then after execution replays every event *emitted since that recorded index* (`AfterBalanceChange`) to update `StateDB` balances via `AddBalance`/`SubBalance`. [1](#0-0) [2](#0-1) 

When a precompile call (e.g. an ERC20 token with a malicious `_beforeTokenTransfer`/hook) reenters the EVM and triggers a **second, nested** precompile call (e.g. `distribution.claimRewards`, `staking.delegate`, or `bank`/`ics20` transfer) before the outer call returns, each nested invocation gets its own `BalanceHandler` instance with its own `prevEventsLen`, but they all share the *same underlying event manager/context*. The inner call's `AfterBalanceChange` consumes and applies events `[innerPrevLen:]`, but when control returns to the outer call, the outer's `AfterBalanceChange` window `[outerPrevLen:]` still includes those same already-applied bank events (since `outerPrevLen < innerPrevLen`), because the outer index was captured before the inner call started. This causes the outer call to **re-apply `CoinSpent`/`CoinReceived` deltas that were already applied by the inner call**, double-crediting or double-debiting `StateDB` balances relative to the real `x/bank` balances.

This is structurally the Fluid bug: a "snapshot index" (`vaultVariables_`/`prevEventsLen`) captured before a reentrant call is used to reconcile state *after* the reentrant call has already mutated the same domain, without accounting for the nested mutation window.

### Finding Description
- `runNativeAction` allocates a fresh `BalanceHandler` per precompile call, but the events they observe come from a single per-tx `sdk.Context.EventManager()` that is shared/threaded across nested calls via the cache context mechanism. [3](#0-2) 
- The repo has a dedicated regression test explicitly acknowledging this exact bug class: *"tests the balance handler bug where recursive precompile calls share the same BalanceHandler instance, causing prevEventsLen to be overwritten. This leads to balance desync between native bank keeper and EVM stateDB."* [4](#0-3) 
- Multiple test fixtures exist specifically to exercise recursive/reentrant precompile calls through ERC20 transfer hooks and IBC transfer flows (`ERC20RecursiveNonRevertingPrecompileCall.sol`, `ics20_recursive_precompile_calls_test.go`), confirming this reentrancy path is reachable by an ordinary unprivileged contract deployer/caller — no privileged role is required. [5](#0-4) [6](#0-5) 

### Impact Explanation
If the window-overlap causes double-application of `CoinSpent`/`CoinReceived` deltas to `StateDB`, an attacker-controlled contract (e.g., ERC-20 with reentrant hooks calling `distribution`/`staking`/`bank`/`ics20` precompiles) could cause the EVM-visible balance (`StateDB.GetBalance`) to diverge from the actual `x/bank` ledger balance for arbitrary addresses within a single transaction. Since `StateDB.Commit()` ultimately persists `stateObjects` back through `commitWithCtx`, a stateDB balance corrupted by double-counting can propagate into account state, causing irreversible accounting corruption of spendable native/EVM balances — a Critical-severity impact matching the allowed-impact gate (unauthorized duplication/corruption of spendable user value).

### Likelihood Explanation
Reachable by any unprivileged EOA that deploys a contract implementing a reentrant hook (ERC-20 `_beforeTokenTransfer`, `receive`/`fallback`, or an IBC callback) that calls a second bank-affecting precompile mid-flow — a pattern the repository's own test suite explicitly constructs and names as demonstrating the bug. This requires no validator, relayer, or governance privilege, only standard contract deployment and a single transaction.

### Recommendation
- Thread the "processed-up-to" event index through the call stack instead of independently re-deriving `prevEventsLen` per nested call, or have the outer `BalanceHandler` snapshot/restore the event slice boundary such that a nested call's already-applied events are excluded from the outer's replay window (e.g., advance the outer's `prevEventsLen` to the post-inner-call event count before continuing, or mark consumed events).
- Alternatively, process balance-affecting events exactly once by tagging them (e.g., via a monotonically consumed cursor stored on the shared `StateDB`/cache context) rather than by raw slice-length comparison, which is not reentrancy-safe.
- Add invariant checks (e.g., in integration tests) asserting `StateDB.GetBalance(addr)` equality with `bankKeeper.GetBalance(addr)` after any transaction involving nested/recursive precompile calls, for all addresses touched by bank events.

### Proof of Concept
1. Deploy an ERC-20-like contract (as in `ERC20RecursiveNonRevertingPrecompileCall.sol`) whose `_beforeTokenTransfer` hook calls `distribution.DISTRIBUTION_CONTRACT.claimRewards(...)` — a bank-balance-affecting precompile call — while already inside another bank-affecting precompile call (e.g., an ICS-20 transfer of the same token, as exercised in `ics20_recursive_precompile_calls_test.go`). [5](#0-4) 
2. Trigger the outer transfer (e.g., via the ICS-20 precompile or a direct ERC-20 transfer) so that the outer `runNativeAction`/`BalanceHandler.BeforeBalanceChange` records `prevEventsLen = N`, then the reentrant hook fires and its own nested `BalanceHandler` records `prevEventsLen = M > N` and applies bank events `[M:]`. [7](#0-6) 
3. When the outer call resumes and calls its own `AfterBalanceChange`, it replays events `[N:]`, which still includes the events at indices `[M:]` already applied by the inner call, double-applying those `CoinSpent`/`CoinReceived` deltas to `StateDB`. [8](#0-7) 
4. Compare `StateDB.GetBalance(addr)` to `bankKeeper.GetBalance(addr, denom)` for the affected addresses after the transaction — a discrepancy confirms the double-application/accounting corruption, consistent with the repository's own `BalanceHandlerTestSuite` framing of this exact defect. [9](#0-8) 

**Note on certainty:** I was not able to fully execute the test suite or trace whether a later patch (e.g., resetting the shared event-manager boundary, or making `prevEventsLen` a shared/synchronized cursor) has already closed this specific overlap window — the existence of `BalanceHandlerTestSuite` and the recursive-call test fixtures strongly suggests the maintainers are aware of and actively testing this exact bug class, so it may already be mitigated in ways not fully visible from the indexed code shown. Confirming the current live behavior (whether the test asserts a *fixed* correct balance or merely exercises the path) would require running the test suite directly, which is outside the scope of this read-only index-based review.

### Citations

**File:** precompiles/common/precompile.go (L63-123)
```go
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
```

**File:** precompiles/common/balance_handler.go (L43-71)
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

**File:** evmd/tests/ibc/ics20_recursive_precompile_calls_test.go (L260-291)
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
```
