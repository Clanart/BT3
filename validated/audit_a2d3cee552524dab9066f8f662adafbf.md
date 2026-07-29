## Title
Balance handler reads stale `prevEventsLen` across recursive/nested precompile calls, causing StateDB/bank balance desync - (File: `precompiles/common/balance_handler.go`, `precompiles/common/precompile.go`)

### Summary
The reported analog bug class is "reading a value after it has been reset/mutated, so the read reflects a stale marker rather than the value that existed before the state change" (the `get_root()`-after-`reset()` pattern). The closest reachable native analog in this repo is the `BalanceHandler.prevEventsLen` mechanism used by stateful precompiles to translate Cosmos SDK bank events into `StateDB` balance deltas. The repository's own integration test explicitly documents this as a known bug: `evmd/tests/integration/balance_handler/balance_handler_test.go` states "recursive precompile calls share the same BalanceHandler instance, causing `prevEventsLen` to be overwritten. This leads to balance desync between native bank keeper and EVM stateDB." [1](#0-0) 

### Finding Description
`BalanceHandler.BeforeBalanceChange` records the number of already-emitted events as a low-water mark (`prevEventsLen`), and `AfterBalanceChange` later replays only the events emitted after that mark to update `StateDB` balances via `AddBalance`/`SubBalance`: [2](#0-1) [3](#0-2) 

This "mark now, diff later" pattern is structurally the same class of bug as the reported issue: a positional/pointer value (`prevEventsLen`, analogous to `next_commitment_ptr`) is captured before a mutation and consumed after further mutation, so if the marker gets reset or overwritten by a nested/recursive call before the outer call reads back its own diff, the outer call computes the wrong delta (or replays events belonging to a different call, or misses some of its own events) — precisely mirroring "storage root read after reset returns default/wrong value."

In `runNativeAction` (used by generic stateful precompiles like the `ics20`, `staking`, `distribution`, `gov`, `slashing`, `erc20` precompiles) a new `BalanceHandler` instance is created via the factory per call: [4](#0-3) 

However, other precompiles (e.g. the debug/testutil precompile pattern and, per the integration test title, others reachable through recursive precompile-to-precompile calls or precompile-to-contract-callback-to-precompile reentry) call `p.GetBalanceHandler()`, returning a handler instance stored on the precompile struct rather than freshly created per invocation: [5](#0-4) [6](#0-5) 

If a single shared `BalanceHandler` instance is reused across nested/recursive precompile invocations within the same EVM call stack (e.g., a contract that calls precompile A, which internally triggers a callback that calls precompile A or another precompile that shares the handler again), the inner call's `BeforeBalanceChange` overwrites `prevEventsLen` set by the outer call. When the inner call finishes and calls `AfterBalanceChange`, it consumes the events since the *inner* mark — correct for itself — but when control returns to the outer call and it calls its own `AfterBalanceChange`, `prevEventsLen` has already been mutated/advanced by the inner call, so the outer call's window computation is wrong: it may re-process events already consumed by the inner call (double-crediting/double-debiting `StateDB` balances) or skip events that belong to it, producing a divergence between the authoritative x/bank balance and the `StateDB` (EVM-visible) balance for the addresses involved.

### Impact Explanation
Because `StateDB.AddBalance`/`SubBalance` values feed directly into what smart contracts observe as ERC20/native token balances during EVM execution (and are eventually committed on `Commit()`), an incorrect balance delta computed from a stale/overwritten `prevEventsLen` window can:
- Double-apply a bank event to `StateDB`, minting phantom EVM-visible balance not backed by an actual bank-module coin, or
- Skip crediting/debiting an account, causing loss of EVM-visible balance relative to the real bank balance.

Either direction breaks the 1:1 accounting invariant between native `x/bank` balances and EVM `StateDB` balances that the `BalanceHandler` exists specifically to preserve, matching the "Critical unauthorized minting/duplication/... accounting corruption of spendable user value" impact class in the allowed-impact gate. The repository already has a dedicated integration test (`TestRecursivePrecompileCallsWithDebugPrecompile`) reproducing "recursive precompile calls" that share a `BalanceHandler` and desync balances, confirming this is a real, previously-identified reachable condition rather than a purely theoretical one. [7](#0-6) 

### Likelihood Explanation
Triggering requires an unprivileged user to deploy/call a contract that performs nested or recursive precompile calls (a caller contract invoking a precompile that itself performs a callback/reentrant precompile call), which is an ordinary, permissionless EVM interaction pattern — no privileged keys or validator collusion needed. The test harness already builds exactly this scenario using a "caller contract" and a "debug precompile," demonstrating the trigger is reachable through the normal contract-call/precompile-call surface described in the "Asset-representation path" and "VM state path" pivots (nested-call/journal/refund handling must keep balances consistent across recursive execution).

### Recommendation
- Ensure every precompile call site always constructs a fresh `BalanceHandler` per invocation (as `runNativeAction` already does via `BalanceHandlerFactory.NewBalanceHandler()`), and eliminate/patch any precompile implementation (such as the `GetBalanceHandler()` pattern in `testutil/testdata/debug/debug.go`) that stores and reuses a single `BalanceHandler` instance across calls.
- Alternatively, make `prevEventsLen` tracking reentrancy-safe by using a stack/counter keyed to call depth instead of a single mutable field, so nested calls cannot clobber an outer call's marker.
- Add/extend regression coverage (building on the existing `evmd/tests/integration/balance_handler/balance_handler_test.go`) asserting that after nested/recursive precompile calls, `StateDB` balances for every touched address exactly match `x/bank`/`x/precisebank` balances, failing the test on any divergence.

### Proof of Concept
The repository's own test demonstrates the trigger path: deploy a caller contract, register a debug precompile that shares a `BalanceHandler`, and invoke a `callback` that recursively re-enters the precompile 10 times within one EVM transaction: [8](#0-7) 

I was not able to fully verify from the index whether every production (non-test) stateful precompile shares a single `BalanceHandler` instance versus allocating one per call (I confirmed `runNativeAction` allocates fresh instances, but could not fully inspect `distribution.go`, `erc20.go`, `gov.go`, `ics20.go`, `slashing.go`, `staking.go` call sites for `GetBalanceHandler()` due to running out of tool iterations). A Devin session with full repository access should verify whether any in-scope production precompile stores a `BalanceHandler` as a struct field reused across calls (mirroring the `testutil/testdata/debug/debug.go` pattern) to confirm exploitability outside of test/debug code.

### Citations

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

**File:** precompiles/common/balance_handler.go (L43-48)
```go
// BeforeBalanceChange is called before any balance changes by precompile methods.
// It records the current number of events in the context to later process balance changes
// using the recorded events.
func (bh *BalanceHandler) BeforeBalanceChange(ctx sdk.Context) {
	bh.prevEventsLen = len(ctx.EventManager().Events())
}
```

**File:** precompiles/common/balance_handler.go (L68-72)
```go
func (bh *BalanceHandler) AfterBalanceChange(ctx sdk.Context, stateDB *statedb.StateDB) error {
	events := ctx.EventManager().Events()

	for _, event := range events[bh.prevEventsLen:] {
		switch event.Type {
```

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

**File:** testutil/testdata/debug/debug.go (L77-78)
```go
	// Start the balance change handler before executing the precompile.
	p.GetBalanceHandler().BeforeBalanceChange(ctx)
```

**File:** testutil/testdata/debug/debug.go (L109-112)
```go
	// Process the native balance changes after the method execution.
	if err := p.GetBalanceHandler().AfterBalanceChange(ctx, stateDB); err != nil {
		return nil, err
	}
```
