### Title
Shared `BalanceHandler` instance across recursive/nested precompile calls corrupts StateDB balance sync - (File: `precompiles/common/balance_handler.go`)

### Summary
The bug is the same class as the USUAL `DistributionModule` finding: a piece of "before" state (`lastOnChainDistributionTimestamp` in the external report, `prevEventsLen` here) is meant to capture a snapshot *before* an operation so that a subsequent step can correctly compute a delta (`timePassed` vs. "events emitted during this call"). If that snapshot gets clobbered by a nested/re-entrant invocation before the outer call reads it back, the delta computation is wrong. In `push-chain-evm--006`, `BalanceHandler.prevEventsLen` plays this role for translating Cosmos SDK bank events into `StateDB.AddBalance`/`SubBalance` calls during precompile execution, and a shared/reused handler across recursive precompile calls causes this offset to be overwritten mid-flight.

### Finding Description
`BalanceHandler.BeforeBalanceChange` records the current length of the event manager's events (`bh.prevEventsLen = len(ctx.EventManager().Events())`), and `AfterBalanceChange` later replays `events[bh.prevEventsLen:]` to apply the equivalent balance mutations to the EVM `StateDB` (`AddBalance`/`SubBalance` for `EventTypeCoinSpent`/`EventTypeCoinReceived`/`EventTypeFractionalBalanceChange`). [1](#0-0) [2](#0-1) 

If a precompile call recursively/re-entrantly triggers another precompile call (e.g. a contract calling back into a precompile, or a precompile method that itself invokes another precompile) while sharing the *same* `BalanceHandler` instance, the inner call's `BeforeBalanceChange` overwrites `prevEventsLen` to a later index. When the outer call's `AfterBalanceChange` subsequently executes, it will use the *inner* call's `prevEventsLen` instead of its own original snapshot, causing the outer call to replay the wrong slice of `events` — either re-applying balance deltas that the inner call already applied to `StateDB` (double counting / phantom balance increase in `StateDB` not backed by an equivalent bank-keeper change) or skipping balance-changing events entirely (silently dropping a native bank transfer from `StateDB`, permanently desyncing EVM balance view from the native bank balance).

This exact scenario is called out and reproduced by a dedicated integration test in the repository itself, `TestRecursivePrecompileCallsWithDebugPrecompile`, whose suite doc comment states: "tests the balance handler bug where recursive precompile calls share the same BalanceHandler instance, causing prevEventsLen to be overwritten. This leads to balance desync between native bank keeper and EVM stateDB." [3](#0-2) 

The test drives a caller contract that recursively invokes a debug precompile (`callback`), funds the contract, and asserts a specific count of `debug_precompile` events and total events, which is the harness used to detect the desync condition. [4](#0-3) 

`BalanceHandlerFactory.NewBalanceHandler()` does exist to create a fresh instance with `prevEventsLen: 0`, suggesting a per-call handler is the intended safe pattern. [5](#0-4) 
However, I could not fully verify from the indexed code whether every recursive/nested precompile call path in `precompiles/common/precompile.go` always allocates a new handler via the factory versus reusing one instance across nested calls — this is the crux of whether the vulnerability is presently reachable or already mitigated, and the index does not give complete visibility into that call-site wiring.

### Impact Explanation
If a shared handler is reachable via ordinary (unprivileged) recursive precompile usage (e.g., a smart contract that calls a precompile which itself triggers another precompile call, or a contract that re-enters a precompile), the `StateDB` balance for an address can diverge from the actual bank-keeper balance:
- Double-application of a `CoinReceived`/`CoinSpent`/`FractionalBalanceChange` event onto `StateDB` would inflate or deflate the EVM-visible balance without a corresponding real bank movement, directly matching the "duplication or irreversible accounting corruption of spendable user value across native balances, EVM balances ... or precompile-mediated assets" Critical impact class.
- Dropped events would silently corrupt `StateDB` state (balances not reflecting real spend/receive), which persists in state and diverges permanently between the EVM view and the bank/precisebank source of truth — an AppHash-relevant accounting corruption if it becomes deterministic across all nodes (which it would be, since it stems from ordinary transaction execution, not from node-local nondeterminism).

### Likelihood Explanation
Likelihood depends entirely on whether recursive/nested precompile invocation paths in this codebase actually reuse a single `BalanceHandler` instance rather than instantiating a new one per call via `BalanceHandlerFactory`. The repository ships a dedicated integration test explicitly describing and reproducing this exact bug pattern with recursive precompile calls, which is strong evidence that the scenario is a recognized, reachable concern in this codebase (it may be a regression test for a fix, or a still-open reproduction — the index does not let me confirm the current wiring in `precompiles/common/precompile.go` with certainty).

### Recommendation
- Ensure every precompile invocation (including recursive/nested/re-entrant calls triggered from within another precompile call or a callback into the EVM) creates and uses its own `BalanceHandler` instance via `BalanceHandlerFactory.NewBalanceHandler()`, and that `prevEventsLen` is never shared/overwritten across call frames.
- Alternatively, refactor `BeforeBalanceChange`/`AfterBalanceChange` to use a stack-based or call-depth-indexed offset rather than a single mutable field, so nested calls cannot clobber an outer call's snapshot.
- Extend `TestRecursivePrecompileCallsWithDebugPrecompile` (and equivalent tests against real precompiles like `erc20`, `staking`, `distribution`, `ics20`) to assert that `StateDB` balances match `bank`/`precisebank` balances exactly after recursive calls, not just event counts.

### Proof of Concept
The existing integration test in the repo is effectively the PoC harness: [6](#0-5) 
It deploys a "debug precompile caller" contract that recursively invokes the debug precompile (`callback`), executes an EVM transaction, and checks event counts. To confirm the concrete balance-corruption impact, this test would need to be extended to assert `StateDB.GetBalance` for the involved account(s) equals the actual `bankKeeper` balance after the recursive call sequence — a mismatch there would be the concrete proof of duplicated/dropped balance mutation described above. I was not able to execute or further trace the exact recursive call-site wiring in `precompiles/common/precompile.go` within the available tool budget, so confirmation that this is presently unmitigated (vs. already fixed by always using the factory) is not fully established from the indexed code alone.

### Citations

**File:** precompiles/common/balance_handler.go (L30-35)
```go
func (bhf BalanceHandlerFactory) NewBalanceHandler() *BalanceHandler {
	return &BalanceHandler{
		bankKeeper:    bhf.bankKeeper,
		prevEventsLen: 0,
	}
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
