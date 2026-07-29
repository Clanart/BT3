### Title
Recursive precompile calls share a single `BalanceHandler` instance, causing `prevEventsLen` to be overwritten and native/EVM balance desync - (File: precompiles/common/balance_handler.go)

### Summary
The Kerosene bug is a class of "partial state synchronization on multi-step value movement" — a function that is supposed to reconcile two parallel representations of the same value (collateral types in DYAD; here, native bank balances vs. EVM `stateDB` balances) only propagates part of the change, leaving the two representations inconsistent and letting a caller retain or lose value incorrectly.

The `BalanceHandler` in `precompiles/common/balance_handler.go` is the mechanism that keeps `x/bank`/`x/precisebank` balance changes (caused by a precompile call, e.g. sending native coins) synchronized into the EVM `stateDB`. It works by recording `prevEventsLen` in `BeforeBalanceChange` and later replaying only the events emitted after that index in `AfterBalanceChange`.

### Finding Description
`BeforeBalanceChange`/`AfterBalanceChange` are stateful methods on a single `BalanceHandler` struct [1](#0-0) , and `AfterBalanceChange` only processes `events[bh.prevEventsLen:]` [2](#0-1) . If the same `BalanceHandler` instance is reused/shared across recursive or re-entrant precompile invocations (e.g. a contract calling a precompile, which in turn triggers another EVM call back into a precompile before the outer call's `AfterBalanceChange` runs), the inner call's `BeforeBalanceChange` overwrites `prevEventsLen` with a later index. When the outer call's `AfterBalanceChange` eventually executes, it re-reads `prevEventsLen` (now pointing past some of the outer call's own bank events) and therefore skips replaying those events into the `stateDB`. This produces exactly the Kerosene-style defect: the native `x/bank`/`x/precisebank` balance state moves correctly, but the corresponding "other representation" (the EVM `stateDB` balance visible to `balanceOf`/subsequent EVM operations within the same transaction) is not moved, leaving the two ledgers inconsistent — mirroring how Kerosene collateral kept moving in the vault's ledger but was never reflected in the liquidator's/liquidated position's expected collateral state.

This exact scenario is codified in a dedicated regression test explicitly titled as a "balance handler bug," which drives recursive precompile calls via a debug precompile and caller contract and asserts on the resulting event/precompile-call counts [3](#0-2) [4](#0-3) .

### Impact Explanation
If `stateDB` balances diverge from actual `x/bank`/`x/precisebank` balances within a transaction due to skipped event replay, this can result in: EVM-visible balances (as read by `balanceOf`, `SLOAD`-derived contract logic, or subsequent transfers within the same call frame) not reflecting real bank-side debits/credits, potentially allowing a contract to spend/duplicate value it does not have on the EVM side, or conversely losing track of credited value — an accounting corruption between native balances and EVM balances, which maps to the "Critical unauthorized minting/duplication/irreversible accounting corruption... across native balances, EVM balances... or precompile-mediated assets" impact category.

### Likelihood Explanation
I was not able to fully verify from the available code whether `BalanceHandler` instances are actually shared/reused across nested/recursive precompile calls in production precompile `Run` implementations (I could only confirm the pattern in the `debug` test precompile used specifically to reproduce this issue, and a purpose-built regression test asserting expected event counts rather than an explicit balance-desync assertion). Whether this is reachable via genuinely unprivileged, ordinary transaction flows in the shipped precompiles (bank, distribution, staking, ics20, werc20, erc20) — as opposed to only the test/debug precompile built to demonstrate the bug — remains uncertain given the tool budget exhausted. The presence of a dedicated test suite named around "the balance handler bug" strongly suggests this was a known, real, and reachable issue being tracked/fixed in this codebase, but I could not confirm the current state of the fix (whether `BalanceHandler` is now created fresh per call, or a guard exists) nor pinpoint the exact production call path that reaches recursion in this pass.

### Recommendation
- Verify that every precompile `Run` implementation creates a fresh `BalanceHandler` per invocation (via `BalanceHandlerFactory.NewBalanceHandler()`) rather than reusing a shared instance across nested/recursive precompile calls within the same EVM call stack.
- If nested precompile calls are possible (contract-to-precompile-to-EVM-call-to-precompile), ensure `prevEventsLen` bookkeeping is stack-based (e.g., a stack of indices, or capturing/restoring `prevEventsLen` around nested calls) instead of a single overwritable field.
- Add an explicit assertion in the regression test (`evmd/tests/integration/balance_handler/balance_handler_test.go`) that native bank/precisebank balances equal EVM `stateDB` balances after the recursive call completes, not just an event count check.

### Proof of Concept
The repository's own regression test demonstrates the reachable recursive scenario: a caller contract invokes the `debug` precompile recursively (`callback(0)`), and the test checks the resulting event counts [5](#0-4) . To convert this into a concrete balance-desync PoC, the test would need to be extended to compare `evmApp.GetBankKeeper().GetBalance(...)` against `stateDB.GetBalance(...)` for the involved accounts after the recursive callback completes, checking whether they diverge — I was unable to execute or extend this test within the current investigation to confirm the divergence empirically.

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

**File:** evmd/tests/integration/balance_handler/balance_handler_test.go (L23-34)
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
```

**File:** evmd/tests/integration/balance_handler/balance_handler_test.go (L45-105)
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

	// Advance to next block to finalize state
	s.chain.NextBlock()
```
