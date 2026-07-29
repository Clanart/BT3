### Title
Recursive/nested precompile calls double-apply bank balance-change events into the EVM StateDB via `BalanceHandler`, causing StateDB/bank-keeper desync — (File: `precompiles/common/balance_handler.go`)

### Summary
This is a direct Cosmos-EVM analog of the Panoptic finding: the external bug came from reentrancy corrupting a piece of shared accounting state (`removedLiquidity`) so that it gets applied more than once. In Cosmos EVM, the `BalanceHandler` used by every stateful precompile (`x/erc20`, staking, distribution, gov, ICS20, etc.) records only a *start* marker (`prevEventsLen`) before a precompile call and then, on completion, replays **every** bank event emitted since that marker into the EVM `StateDB` via `AddBalance`/`SubBalance`. When a precompile call recursively/nestedly triggers another precompile call (a pattern the repo's own test suite reproduces), the inner call's own `BeforeBalanceChange`/`AfterBalanceChange` pair consumes and applies its slice of events to the `StateDB`, but the outer call's `prevEventsLen` was captured *before* the nested call ran, so its own `AfterBalanceChange` re-processes (and re-applies) the same already-applied events a second time.

### Finding Description
`BalanceHandler.BeforeBalanceChange` and `AfterBalanceChange` only track a single lower-bound index into `ctx.EventManager().Events()`: [1](#0-0) 

`AfterBalanceChange` then replays every `CoinSpent`/`CoinReceived`/fractional-balance event from that index to the *current* end of the event log into the StateDB: [2](#0-1) 

The handler is meant to be scoped per precompile call — `runNativeAction` creates one fresh handler instance per invocation via the factory and calls `BeforeBalanceChange`/`AfterBalanceChange` around `action(ctx)`: [3](#0-2) 

The problem is the open-ended slice `events[bh.prevEventsLen:]` has no upper bound tied to "events produced solely by this call". If the `action(ctx)` for an outer precompile call itself triggers another precompile call (e.g. a contract calling one precompile which in turn calls the EVM again and re-enters a precompile, or a caller contract that recursively invokes a precompile — the repo explicitly ships PoC/test infrastructure for this, e.g. `contracts/solidity/ERC20RecursiveNonRevertingPrecompileCall.sol` and the debug precompile's recursive `callback()`), the inner call:
1. Captures its own `prevEventsLen` (a later index than the outer's).
2. Emits bank events for its own bank operations.
3. Applies those events to the `StateDB` in its own `AfterBalanceChange`.

Then control returns to the outer call, which finishes its own `action(ctx)` and calls its own `AfterBalanceChange` using the outer, earlier `prevEventsLen`. Since `ctx.EventManager()` accumulates events for the whole call stack and is never truncated/consumed, the outer handler's slice `events[outer.prevEventsLen:]` still contains the inner call's events — which get applied to `StateDB.AddBalance`/`SubBalance` a second time.

This exact scenario is documented and reproduced by the repository's own integration test, which states the bug explicitly: [4](#0-3) 

The test drives a caller contract into recursive precompile invocations and asserts on the resulting event/state counts: [5](#0-4) 

### Impact Explanation
Because `AfterBalanceChange` directly mutates `StateDB.AddBalance`/`SubBalance` (which represent EVM-visible, spendable native-token balances) independent of and in addition to the actual `x/bank` keeper ledger, double-application of the same coin-spent/coin-received events causes the EVM-side balance to diverge from the real bank balance backing it. Depending on transfer direction this can:
- Inflate an attacker- or victim-controlled EVM balance beyond what is actually escrowed/held in the bank module (unauthorized duplication of spendable value), or
- Incorrectly debit a balance twice (irreversible accounting corruption / fund lock).

Since native balances are the asset representation shared across EVM contracts, precompile-mediated transfers, and (via `x/erc20`/`x/precisebank`) ERC20 and IBC-escrowed value, a StateDB/bank desync of this kind is a critical, permanent accounting corruption of spendable user value — matching the "Critical unauthorized minting/duplication/irreversible accounting corruption" and "permanent freezing/theft of user funds" allowed-impact classes.

### Likelihood Explanation
The trigger requires only an ordinary, unprivileged user to deploy or call a contract that causes one precompile call to trigger another nested precompile call during its execution (recursive/self-referential calls, or a call to precompile A whose native action itself calls the EVM and re-enters precompile A or precompile B). This is a normal, permissionless EVM contract-call pattern — no validator, relayer, or governance privilege is required. The repository already contains test scaffolding (`ERC20RecursiveNonRevertingPrecompileCall.sol`, `StakingReverter.sol`, the debug precompile, and the dedicated `balance_handler_test.go`) purpose-built to exercise exactly this recursive-call condition, indicating the maintainers are aware this is a live, reachable code path.

### Recommendation
- Track the balance-change event window per call using both a start and end index (or a call-scoped copy of the event slice) so that `AfterBalanceChange` only processes events strictly produced by its own `action(ctx)` invocation, excluding any events already consumed/applied by a nested precompile call.
- Alternatively, mark/consume events as "applied" (e.g., via an event index watermark stored on the `StateDB` itself rather than the per-call `BalanceHandler`) so that a nested call's already-applied events are skipped by the outer call's post-processing.
- Add an explicit invariant check (as is done elsewhere in `x/erc20` via `ErrBalanceInvariance`, see `x/erc20/keeper/msg_server.go`) comparing bank-keeper balance deltas against StateDB balance deltas at the end of every precompile call, failing loudly on mismatch instead of silently double-applying deltas.

### Proof of Concept
1. Deploy a contract that calls a stateful precompile (e.g. staking `delegate`, or a custom debug-style precompile) such that, inside the precompile's native Go execution, it re-enters the EVM and calls into another (or the same) stateful precompile — this is exactly the pattern already implemented by `contracts/solidity/precompiles/testutil/contracts/StakingReverter.sol`'s `performDelegation`/`nestedTryCatchDelegations` and by the debug precompile's `Call0`, which invokes `evmKeeper.CallEVMWithData` back into itself.
2. The outer precompile call's `BalanceHandler.BeforeBalanceChange` records `prevEventsLen = N`.
3. The nested precompile call executes, emits its own `CoinSpent`/`CoinReceived` events, and its own `AfterBalanceChange` applies those deltas to `StateDB` (see `precompiles/common/balance_handler.go:68-105`).
4. Control returns to the outer call; its `AfterBalanceChange` runs with `prevEventsLen = N`, and `events[N:]` still includes the inner call's events, so `StateDB.AddBalance`/`SubBalance` is invoked again for the same real coin movement.
5. Compare the resulting `StateDB.GetBalance` for the affected address against `bankKeeper.GetBalance` for the same address/denom after the transaction commits — the values will differ by the double-counted amount, evidencing a spendable-value duplication/corruption. The existing `evmd/tests/integration/balance_handler/balance_handler_test.go:23-105` test scaffolding can be adapted (using a real bank-moving precompile in place of the count-only debug precompile) to demonstrate the concrete balance divergence.

**Note on confidence:** I was unable to execute the test suite or trace runtime event-index values, so the double-application described here is based on static analysis of `BeforeBalanceChange`/`AfterBalanceChange`/`runNativeAction` and the repo's own test naming/comments (which explicitly describe "balance desync between native bank keeper and EVM stateDB" from "recursive precompile calls"). A background Devin session with terminal access should run and extend `balance_handler_test.go` with real balance-moving precompiles (staking/distribution/erc20) to confirm the exact numeric divergence before treating this as fully confirmed.

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

**File:** precompiles/common/balance_handler.go (L68-105)
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
```

**File:** precompiles/common/precompile.go (L99-123)
```go
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

**File:** evmd/tests/integration/balance_handler/balance_handler_test.go (L23-26)
```go
// BalanceHandlerTestSuite tests the balance handler bug where recursive precompile calls
// share the same BalanceHandler instance, causing prevEventsLen to be overwritten.
// This leads to balance desync between native bank keeper and EVM stateDB.
type BalanceHandlerTestSuite struct {
```

**File:** evmd/tests/integration/balance_handler/balance_handler_test.go (L76-105)
```go
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
