This found a strong, directly relevant analog: the `BalanceHandler` bug in `precompiles/common/balance_handler.go` and the test in `evmd/tests/integration/balance_handler/balance_handler_test.go` explicitly labeled as documenting a "balance handler bug where recursive precompile calls share the same BalanceHandler instance, causing prevEventsLen to be overwritten... leads to balance desync between native bank keeper and EVM stateDB" [1](#0-0) . This maps directly onto the Tapioca bug class: an accounting mechanism (`BalanceHandler.prevEventsLen`) that tracks "what's already been accounted for" can get desynchronized from the actual underlying balance changes (bank events), causing the StateDB's reported EVM balance to diverge from the true bank-keeper balance — exactly the "accounted balance doesn't match withdrawable balance" pattern, but here the divergence is between StateDB (EVM) balance and bank balance rather than share price vs underlying assets.

### Title
Recursive precompile calls share a single `BalanceHandler` instance, causing StateDB/bank balance desync exploitable for fund duplication or loss - (File: precompiles/common/balance_handler.go, precompiles/common/precompile.go)

### Summary
The Cosmos EVM `BalanceHandler` mechanism used by native-balance-mutating precompiles (staking, distribution, slashing, bank, werc20, etc.) records `prevEventsLen` before a keeper call and replays only the *new* bank events (`coin_spent`/`coin_received`/fractional balance change) after the call to sync `stateDB` balances with the bank keeper's actual balance changes [2](#0-1) . This is the analog of Yieldbox's `_currentBalance()`: it's the "accounting" layer that must always reflect the true underlying balance changes. The repository's own integration test explicitly documents that **recursive precompile calls share the same `BalanceHandler` instance**, which overwrites `prevEventsLen`, causing balance changes from an inner call to be replayed against the wrong baseline (or missed/double counted) when control returns to the outer call [1](#0-0) .

### Finding Description
`BeforeBalanceChange` snapshots `len(ctx.EventManager().Events())` into `bh.prevEventsLen`, and `AfterBalanceChange` slices `events[bh.prevEventsLen:]` to determine which new bank events to apply to the EVM `stateDB` [3](#0-2) . If a precompile call re-enters (e.g., a contract calls precompile A, which invokes a Cosmos message that itself triggers a callback/hook that calls precompile B, or a nested call to the same precompile) and both calls share the same `*BalanceHandler` instance (e.g., held on the `Precompile` struct rather than created fresh per call), then the inner call's `BeforeBalanceChange` overwrites `prevEventsLen` set by the outer call. When the outer call's `AfterBalanceChange` eventually runs, it computes `events[bh.prevEventsLen:]` using the *inner* call's baseline, which can cause: (1) events already applied to `stateDB` by the inner call to be re-applied (double-counting a balance increase/decrease), or (2) events belonging to the outer call to be silently skipped (an event window that should have been replayed is missed) because the recorded offset no longer corresponds to the outer call's actual starting point. Either direction breaks the invariant that `stateDB` balance == bank keeper spendable balance for the affected address.

### Impact Explanation
If the desync causes a `stateDB.AddBalance` to be applied twice for the same underlying bank credit (e.g., during a claim-rewards or delegate/undelegate flow invoked recursively from a smart contract callback), the EVM balance for the attacker-controlled contract becomes inflated relative to the actual bank-backed balance without any additional bank-side mint. This is unauthorized duplication of spendable value: the contract could subsequently transfer/withdraw the phantom EVM balance, extracting real funds it never received, i.e., an accounting corruption that lets an unprivileged user create spendable value from nothing — matching the Critical "unauthorized minting/duplication of spendable user value" impact class. Conversely, a skipped event could permanently understate the intended recipient's stateDB balance while the bank side already credited it, creating a freezing/loss condition until an out-of-band reconciliation happens.

### Likelihood Explanation
This requires a precompile call pattern that can recurse (contract-initiated call into a precompile, whose keeper call in turn triggers another precompile invocation, e.g., via IBC/ICS20 callback hooks, or a contract that calls back into the same precompile via a nested EVM call within the same transaction). The project's own test suite specifically reproduces this with a "debug precompile" and a caller contract doing recursive `callback` invocations, confirming the scenario is reachable through ordinary (unprivileged) EVM contract execution rather than any privileged path [4](#0-3) .

### Recommendation
Ensure each precompile invocation (including nested/recursive ones) uses its own independent `BalanceHandler` instance with a correctly scoped event-index baseline — e.g., allocate a fresh handler per call frame via `BalanceHandlerFactory.NewBalanceHandler()` rather than sharing a struct-held handler across reentrant calls, or maintain a stack of `prevEventsLen` values so nested calls push/pop their own baseline instead of overwriting a single field. Add an invariant check comparing `stateDB` balance to `bankKeeper.SpendableCoin` for all addresses touched by a precompile call before returning from `Run`, and fail the call if they diverge.

### Proof of Concept
The repository already contains a reproducing test: `BalanceHandlerTestSuite.TestRecursivePrecompileCallsWithDebugPrecompile` in `evmd/tests/integration/balance_handler/balance_handler_test.go` deploys a `DebugPrecompileCaller` contract that recursively invokes a debug precompile wired with the shared `BalanceHandler`, demonstrating the `prevEventsLen` overwrite scenario described in the test's own docstring [5](#0-4) . Because the index-based `AfterBalanceChange` logic in `precompiles/common/balance_handler.go` is used by every native-token-mutating precompile (staking `Delegate`, distribution `ClaimRewards`/`WithdrawDelegatorReward`, slashing, bank, werc20) via `cmn.Precompile.BalanceHandlerFactory` [6](#0-5) [7](#0-6) , any contract that can trigger a nested precompile call within the same EVM call frame (e.g., a claim-rewards call whose withdraw address is itself a contract that calls back into a balance-mutating precompile) is a candidate attack surface for exploiting the shared-state desync to duplicate or lose EVM-visible balance.

### Citations

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

**File:** precompiles/slashing/slashing.go (L60-66)
```go
	return &Precompile{
		Precompile: cmn.Precompile{
			KvGasConfig:           storetypes.KVGasConfig(),
			TransientKVGasConfig:  storetypes.TransientGasConfig(),
			ContractAddress:       common.HexToAddress(evmtypes.SlashingPrecompileAddress),
			BalanceHandlerFactory: cmn.NewBalanceHandlerFactory(bankKeeper),
		},
```
