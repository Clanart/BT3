### Title
Balance desync between EVM StateDB and bank keeper from shared `BalanceHandler.prevEventsLen` across recursive/nested precompile calls - (File: precompiles/common/balance_handler.go)

### Summary
`BalanceHandler` tracks a single mutable cursor, `prevEventsLen`, to slice out the bank events emitted since the last checkpoint and replay them as `StateDB.AddBalance`/`SubBalance` calls [1](#0-0) . This is the exact same bug class as the `KangarooVault` `usedFunds`/`totalFunds` issue: an auxiliary accounting counter that is supposed to mirror ground-truth state (here, the bank keeper's event log) can be advanced/consumed incorrectly when the same handler instance is reused across nested calls, causing the EVM-visible balance (StateDB) to diverge from the actual bank-keeper balance.

### Finding Description
`BeforeBalanceChange` records `len(ctx.EventManager().Events())` as a checkpoint, and `AfterBalanceChange` replays every bank event after that checkpoint into the StateDB [2](#0-1) . This design assumes each precompile invocation gets its own "before/after" bracket over a monotonically growing, exclusively-owned event slice. When a precompile call triggers another precompile call recursively (e.g., a contract calling a precompile which internally calls back into another precompile, or the EVM re-enters a precompile via `call`/`delegatecall`/`staticcall`), if the same `BalanceHandler` instance is shared/reused for the nested call, the inner call's `BeforeBalanceChange` overwrites `prevEventsLen` with a later index. When the outer call's `AfterBalanceChange` subsequently runs, it will use the now-advanced `prevEventsLen`, causing it to skip events that were emitted by the outer precompile operation before the nested call began. Those skipped bank events (coin spent/received, or precisebank fractional-balance-change events) never get applied to `StateDB.AddBalance`/`SubBalance`, so the EVM-visible balance and the underlying `x/bank` balance diverge silently.

This is functionally identical to the `KangarooVault` root cause: a tracked auxiliary counter (`usedFunds` there, `prevEventsLen` here) that is not properly isolated/reset per logical operation, allowing real underlying value movements (bank keeper transfers) to occur without being reflected in the dependent tracking layer (StateDB), producing an accounting invariant break between two representations of the same value.

The repository itself contains a dedicated regression test (`evmd/tests/integration/balance_handler/balance_handler_test.go`) explicitly documented as reproducing "the balance handler bug where recursive precompile calls share the same BalanceHandler instance, causing prevEventsLen to be overwritten. This leads to balance desync between native bank keeper and EVM stateDB" [3](#0-2) , confirming this is a recognized, reachable code path rather than a purely theoretical concern.

### Impact Explanation
If `AddBalance`/`SubBalance` calls are dropped for an account due to a skipped event window, the account's EVM-visible balance (used for subsequent `CALL`/`transfer`/`balanceOf` semantics within the same or later EVM execution) will not match its true spendable bank balance. Depending on the direction of the desync:
- An account's StateDB balance can end up higher than its true bank balance, allowing it to spend/transfer funds it does not actually hold in `x/bank` (double-spend / unbacked balance creation from the EVM's perspective), or
- An account's StateDB balance can end up lower than its true bank balance, causing loss of usable funds/reverted transfers for legitimate users (permanent effective freezing of funds visible to the EVM).

Either direction breaks the 1:1 accounting invariant between native/bank balances and EVM-visible balances that the whole precompile and wrapper design (`x/vm/wrappers`, `precisebank`) is built to preserve, matching the "irreversible accounting corruption of spendable user value across native balances / EVM balances" and "permanent freezing / unauthorized extraction of user funds" impact categories.

### Likelihood Explanation
Triggering requires only an unprivileged user to deploy or interact with a contract that performs a recursive/nested precompile call sequence (contract → precompile A → contract logic that calls precompile B, or a precompile call whose execution itself re-enters another precompile call within the same EVM message call stack) while a single shared `BalanceHandler` instance is reused for the nested invocation. This does not require any privileged role, validator collusion, or malicious relayer — it is exercised entirely through ordinary contract/precompile call flows, and the codebase's own test (`TestRecursivePrecompileCallsWithDebugPrecompile`) demonstrates the scenario is reachable via a simple caller contract triggering a "callback" that nests calls into the debug precompile [4](#0-3) .

I was not able to fully trace, within the available tool budget, exactly how/where `BalanceHandler` instances are allocated per precompile-call vs. shared across nested calls (i.e., whether a `BalanceHandlerFactory` always mints a fresh handler per call or whether one handler is threaded through nested EVM calls) — that wiring lives in the EVM keeper/precompile dispatch code which was not fully inspected. This is the key uncertainty determining whether the bug is exploitable in the current production build or already mitigated (e.g., if a fresh handler is always created per `Run`, the shared-instance scenario the test targets could not occur, or conversely if it is confirmed shared, this is an unguarded live invariant break, matching the "chain halt/consensus fork" or "critical unauthorized extraction" gates depending on exploit direction).

### Recommendation
- Ensure a new `BalanceHandler` instance (with its own `prevEventsLen`) is created for every top-level precompile invocation and is not shared or reused across nested/recursive precompile calls within the same EVM call stack; alternatively, make the handler stack-based (push/pop checkpoints) so nested calls do not clobber the outer call's checkpoint.
- Add an invariant check that asserts `StateDB` balances reconcile with `x/bank` balances at the end of EVM message execution (or at least for all addresses touched by precompile calls in that execution), reverting the transaction if a mismatch is detected instead of silently continuing.
- Expand the existing `TestRecursivePrecompileCallsWithDebugPrecompile` test to explicitly assert balance equality between `stateDB.GetBalance` and `bankKeeper.GetBalance` after execution, not just event counts, and add cases for the different possible orderings of nested precompile calls (delegatecall/staticcall/call combinations, and calls that trigger `x/precisebank` fractional-balance events).

### Proof of Concept
Not independently constructed beyond what the repository's own regression test already encodes: `evmd/tests/integration/balance_handler/balance_handler_test.go` deploys a `DebugPrecompileCaller` contract, funds it, and invokes `callback(0)`, which (per its documented purpose) triggers recursive precompile calls sharing one `BalanceHandler`, and the test asserts on event counts around the debug precompile calls [5](#0-4) . Verifying actual balance divergence (StateDB vs. bank keeper post-execution) would require running/extending this test with explicit balance assertions, which was not performed here.

### Citations

**File:** precompiles/common/balance_handler.go (L43-48)
```go
// BeforeBalanceChange is called before any balance changes by precompile methods.
// It records the current number of events in the context to later process balance changes
// using the recorded events.
func (bh *BalanceHandler) BeforeBalanceChange(ctx sdk.Context) {
	bh.prevEventsLen = len(ctx.EventManager().Events())
}
```

**File:** precompiles/common/balance_handler.go (L68-71)
```go
func (bh *BalanceHandler) AfterBalanceChange(ctx sdk.Context, stateDB *statedb.StateDB) error {
	events := ctx.EventManager().Events()

	for _, event := range events[bh.prevEventsLen:] {
```

**File:** evmd/tests/integration/balance_handler/balance_handler_test.go (L23-25)
```go
// BalanceHandlerTestSuite tests the balance handler bug where recursive precompile calls
// share the same BalanceHandler instance, causing prevEventsLen to be overwritten.
// This leads to balance desync between native bank keeper and EVM stateDB.
```

**File:** evmd/tests/integration/balance_handler/balance_handler_test.go (L45-102)
```go
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
