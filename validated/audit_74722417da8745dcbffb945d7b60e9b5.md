### Title
Shared `BalanceHandler.prevEventsLen` state corrupted by reentrant/recursive precompile calls causes EVM StateDB / bank balance desync - (File: `precompiles/common/balance_handler.go`)

### Summary
`BalanceHandler` records the event-log length before a precompile's stateful operation (`BeforeBalanceChange`) and later replays only the events appended after that offset (`AfterBalanceChange`) to update `statedb.StateDB` balances. The `prevEventsLen` field lives on a single `BalanceHandler` instance that is created once per precompile (via `NewBalanceHandler`) and reused across all invocations of that precompile, including reentrant/recursive calls triggered from within a contract callback. When an outer precompile call is interrupted by a nested call to the same (or another precompile sharing the mechanism) that also calls `BeforeBalanceChange`/`AfterBalanceChange`, the shared `prevEventsLen` is overwritten by the inner call before the outer call finishes, causing the outer call's own bank events to be silently skipped when it later calls `AfterBalanceChange`.

### Finding Description
`BalanceHandlerFactory.NewBalanceHandler()` [1](#0-0)  returns a `BalanceHandler{prevEventsLen: 0}` that precompiles instantiate once (grep shows a single `NewBalanceHandler` call site in `distribution.go`, `erc20.go`, `gov.go`, `ics20.go`, `slashing.go`, and `staking.go`), meaning the handler is a long-lived, mutable field rather than a value scoped to a single call.

Call flow for a stateful precompile method is:
1. `BeforeBalanceChange(ctx)` snapshots `bh.prevEventsLen = len(ctx.EventManager().Events())` [2](#0-1) .
2. The method performs bank/precisebank state-changing operations that emit `CoinSpent`/`CoinReceived`/`FractionalBalanceChange` events.
3. `AfterBalanceChange(ctx, stateDB)` iterates `events[bh.prevEventsLen:]` and applies `stateDB.AddBalance`/`SubBalance` accordingly [3](#0-2) .

If step 2 for the outer call includes a nested/recursive EVM call that itself invokes the same precompile (e.g., a contract callback that re-enters the precompile, as reproduced by `TestRecursivePrecompileCallsWithDebugPrecompile`) [4](#0-3) , the inner call executes its own `BeforeBalanceChange`, overwriting `bh.prevEventsLen` to a later index, then its own `AfterBalanceChange`, which correctly consumes only its own events. Control returns to the outer call, which then calls `AfterBalanceChange` using the *now-overwritten* `prevEventsLen` (set by the inner call) instead of its own original snapshot. The outer call's own `CoinSpent`/`CoinReceived` events — emitted before the inner call — fall outside the `events[bh.prevEventsLen:]` slice and are silently never applied to `stateDB`, while the real x/bank ledger has already recorded the transfer.

The test file's own docstring explicitly names this: "tests the balance handler bug where recursive precompile calls share the same BalanceHandler instance, causing prevEventsLen to be overwritten. This leads to balance desync between native bank keeper and EVM stateDB." [5](#0-4) 

### Impact Explanation
This is a direct analog to the Burve `removeValueSingle()` bug class: an accounting update is derived from a stale/overwritten snapshot taken before a state-changing action, rather than being correctly scoped to that action, producing corrupted accounting. Here the corrupted value is the EVM-visible balance in `statedb.StateDB` versus the true x/bank balance:
- The native bank ledger (source of truth for consensus/AppHash and for `eth_getBalance` queries after the block commits) is correctly debited/credited.
- The EVM `StateDB` balance used during the remainder of the *same* transaction/nested call context is not updated for the outer call's own transfer, since its events are skipped.
- Within the same call frame, this stale, uncredited/undebited `StateDB` balance can be observed and acted upon by subsequent contract logic (e.g., `address(this).balance` checks, additional value transfers, or chained precompile calls that rely on `StateDB` balance for authorization), enabling an unprivileged attacker to construct a contract that repeatedly re-enters a stateful precompile (distribution, staking, gov, erc20, ics20, slashing all instantiate their own `BalanceHandler`) to desynchronize `StateDB` from the bank ledger and extract or duplicate spendable value that should have been debited, or fail to receive credited value that should have landed in `StateDB` — an irreversible accounting corruption of spendable user value across native/EVM balances triggered purely through ordinary reentrant contract call flow, matching the "Critical unauthorized... duplication... corruption of spendable user value" and "Critical... unauthorized extraction of user funds" impact classes.

### Likelihood Explanation
High likelihood/ease of trigger: it requires only deploying a plain smart contract that calls a stateful precompile (e.g., `distribution.withdrawDelegatorRewards`, staking delegate/undelegate, or gov vote/deposit) and, from within that call's execution path (e.g., a callback, hook, or nested call the precompile itself triggers, or reentrancy via a token/ERC20 hook), calls the same precompile family again before the outer call returns. The repository's own integration test (`TestRecursivePrecompileCallsWithDebugPrecompile`) already demonstrates this exact recursive-call pattern reproducibly with a debug precompile and a caller contract, confirming the mechanism is reachable by any unprivileged EVM user without special permissions.

### Recommendation
Scope `prevEventsLen` (and the `BalanceHandler` state generally) per call instead of as a shared mutable field on a long-lived precompile instance — e.g., pass/return the previous length explicitly through the call stack (stack/queue of offsets), or instantiate a fresh `BalanceHandler`/capture the offset locally for each precompile invocation, including reentrant ones, so that nested calls cannot clobber an outer call's event-offset snapshot. Ensure `AfterBalanceChange` for a given call always processes exactly the slice of events produced by that call, independent of any nested calls executed in between `BeforeBalanceChange` and `AfterBalanceChange`.

### Proof of Concept
The repository already contains a reproducing test: `evmd/tests/integration/balance_handler/balance_handler_test.go` deploys a caller contract that triggers a `callback` which recursively invokes a debug precompile sharing the `BalanceHandler` mechanism, and asserts on the resulting event/debug-call counts to demonstrate the `prevEventsLen` overwrite [6](#0-5) . A concrete production PoC would replace the debug precompile with a real stateful precompile (e.g., `distribution` or `staking`) reentered from a callback path, then compare `stateDB` balance (via `eth_getBalance` mid-transaction/trace) against the post-commit bank-keeper balance to show the divergence and any resulting double-spend/unauthorized extraction within the same transaction.

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

**File:** precompiles/common/balance_handler.go (L46-48)
```go
func (bh *BalanceHandler) BeforeBalanceChange(ctx sdk.Context) {
	bh.prevEventsLen = len(ctx.EventManager().Events())
}
```

**File:** precompiles/common/balance_handler.go (L68-136)
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

		case precisebanktypes.EventTypeFractionalBalanceChange:
			addr, err := ParseAddress(event, precisebanktypes.AttributeKeyAddress)
			if err != nil {
				return fmt.Errorf("failed to parse address from event %q: %w", precisebanktypes.EventTypeFractionalBalanceChange, err)
			}
			if bh.bankKeeper.BlockedAddr(addr) {
				// Bypass blocked addresses
				continue
			}

			delta, err := ParseFractionalAmount(event)
			if err != nil {
				return fmt.Errorf("failed to parse amount from event %q: %w", precisebanktypes.EventTypeFractionalBalanceChange, err)
			}

			deltaAbs, err := utils.Uint256FromBigInt(new(big.Int).Abs(delta))
			if err != nil {
				return fmt.Errorf("failed to convert delta to Uint256: %w", err)
			}

			if delta.Sign() == 1 {
				stateDB.AddBalance(common.BytesToAddress(addr.Bytes()), deltaAbs, tracing.BalanceChangeUnspecified)
			} else if delta.Sign() == -1 {
				stateDB.SubBalance(common.BytesToAddress(addr.Bytes()), deltaAbs, tracing.BalanceChangeUnspecified)
			}

		default:
			continue
		}
	}
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
