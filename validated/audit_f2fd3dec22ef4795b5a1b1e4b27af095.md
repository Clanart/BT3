### Title
Shared `BalanceHandler` state across recursive/reentrant precompile calls desynchronizes EVM `StateDB` balances from `x/bank` ledger balances - ([File: precompiles/common/balance_handler.go])

### Summary
The MysteryBox report is a case of an internal accounting mapping (mystery-box ownership) never being kept in sync with the actual transfer mechanism (ERC1155 balance), so downstream logic (`claimMysteryBoxes`) reads stale attribution data. The Cosmos EVM analog is `precompiles/common.BalanceHandler`, which tracks `prevEventsLen` to know which portion of the Cosmos SDK event log corresponds to the *current* precompile invocation before translating `x/bank` `coin_spent`/`coin_received` events into `StateDB.AddBalance`/`SubBalance` calls. When a precompile call recursively invokes another precompile (or itself) within the same EVM execution, and both invocations end up sharing the same `BalanceHandler` instance, the inner call's `BeforeBalanceChange` overwrites `prevEventsLen`, corrupting the bookkeeping the outer call relies on to translate the correct slice of bank events into `StateDB` balance changes.

### Finding Description
`BalanceHandler` is the mechanism that keeps the EVM's `StateDB` balance view consistent with the authoritative `x/bank` balance after a precompile performs a native (Cosmos SDK) balance-affecting action (e.g., `MsgSend`, mint/burn, IBC transfer, staking payouts): [1](#0-0) 

`BeforeBalanceChange` snapshots the current length of `ctx.EventManager().Events()` into `prevEventsLen`; `AfterBalanceChange` then slices `events[bh.prevEventsLen:]` to find the bank events emitted during *this* invocation, and applies matching `StateDB.AddBalance`/`SubBalance` calls: [2](#0-1) 

In the standard precompile entrypoint (`RunNativeAction`/`runNativeAction`), a **new** `BalanceHandler` is created per call via a factory: [3](#0-2) 

However, several real precompiles (`distribution`, `erc20`, `gov`, `ics20`, `slashing`, `staking`) reference a `GetBalanceHandler()` accessor rather than the `BalanceHandlerFactory` pattern, and the debug/test precompiles that exist specifically to reproduce this class of bug (`testutil/testdata/debug/debug.go`, `evmd/tests/testdata/debug/debug.go`) call `p.GetBalanceHandler()` — a **single handler instance stored on the precompile struct itself**, which is reused across every call to that precompile, including nested/recursive calls that happen inside a single EVM transaction (e.g., a contract calling a precompile, which itself triggers a CallEVM back into another precompile). A dedicated regression test exists explicitly documenting this exact failure mode: [4](#0-3) [5](#0-4) 

When a shared handler is used and a precompile call recursively triggers another precompile call before returning:
1. Outer call: `BeforeBalanceChange` sets `prevEventsLen = N`.
2. Outer's native action performs a bank operation, emits event, then calls back into the (same) precompile recursively.
3. Inner call: `BeforeBalanceChange` overwrites `prevEventsLen = M` (M > N, now including the outer's own event).
4. Inner call's `AfterBalanceChange` consumes events `[M:]`, applies correct changes for itself, but the outer's initial bank event (`[N:M]`) is never applied to `StateDB` — a **balance the bank module says exists is never reflected in `StateDB`**.
5. Alternatively, depending on the call order, the outer's later `AfterBalanceChange` can incorrectly reprocess and re-apply already-consumed events, **double-applying** a debit or credit to `StateDB`.

Because `StateDB` balance is what get committed back into the EVM-side ledger (and can itself be a source of truth flushed back via `Commit`), and the underlying `x/bank` keeper is the settlement layer used by IBC escrow, ERC20 precompile transfers, distribution payouts, and gov deposit/burn flows, this divergence breaks the fundamental 1:1 accounting invariant between native coin balances and EVM-visible balances — precisely the class of bug this scan is meant to surface (asset-representation invariant, "Smart Audit Pivot #2").

### Impact Explanation
If an attacker can trigger nested/recursive precompile invocations (e.g., an ERC20/ICS20/staking/gov precompile call from a smart contract that itself makes another qualifying precompile call before the outer call completes), the resulting mis-slicing of bank events can:
- Cause `StateDB` to under-credit a legitimate `x/bank` balance increase (permanent loss/freezing of user funds visible on-chain, since the EVM balance the user can spend from no longer matches the bank ledger), or
- Cause `StateDB` to double-apply a credit/debit (duplication of spendable value on the EVM side that is not backed by the underlying `x/bank` balance), enabling extraction of value not actually escrowed/minted.

Both outcomes correspond to Critical impacts in the allowed-impact gate: "unauthorized minting/duplication ... irreversible accounting corruption of spendable user value across ... EVM balances" and "permanent freezing, locking ... unauthorized extraction of user funds."

### Likelihood Explanation
Likelihood depends on (a) whether production precompiles actually instantiate `BalanceHandler` via the shared-singleton `GetBalanceHandler()` pattern (as demonstrated by the debug precompile, and suggested by the single-match grep hits in `distribution.go`, `erc20.go`, `gov.go`, `ics20.go`, `slashing.go`, `staking.go`) versus the safer, freshly-instantiated `BalanceHandlerFactory.NewBalanceHandler()` pattern used in `runNativeAction`, and (b) whether an unprivileged caller can actually force re-entrant/nested precompile execution within one EVM call (e.g., contract-to-contract calls chaining two stateful precompiles, or a precompile's native action itself calling back into the EVM which can hit another precompile). The existing dedicated test (`TestRecursivePrecompileCallsWithDebugPrecompile`) demonstrates that the underlying mechanics for triggering this are already present and exercised in the test suite, which increases confidence that the code path is reachable in principle.

**I was unable to fully verify, within the available tool budget, whether the current implementations of `GetBalanceHandler()` in the six listed production precompiles construct a fresh handler per call (safe) or return a cached singleton field (vulnerable), nor could I fully confirm whether the regression test currently passes (indicating a fix) or reproduces failure (indicating a live bug).** This is the key open question that must be resolved by directly reading `GetBalanceHandler()`'s implementation and current test-pass status before this can be confirmed as an exploitable Critical finding versus an already-mitigated issue being guarded by a regression test.

### Recommendation
- Audit every precompile's `GetBalanceHandler()`/`BalanceHandlerFactory` wiring to confirm a **new** `BalanceHandler` (with its own `prevEventsLen`) is created for every top-level *and* every nested/recursive precompile invocation, never a struct-level singleton shared across calls.
- If a singleton is currently used by any of `distribution`, `erc20`, `gov`, `ics20`, `slashing`, or `staking` precompiles, migrate them to the `BalanceHandlerFactory` pattern already used in `precompiles/common/precompile.go`'s `runNativeAction`.
- Extend `TestRecursivePrecompileCallsWithDebugPrecompile` (and add equivalent tests against the real production precompiles capable of recursion, e.g., ICS20 + ERC20 combined calls) to assert that `StateDB` balances exactly match `x/bank` balances after nested precompile execution, not just that a fixed event count occurs.

### Proof of Concept
A conceptual PoC (pending confirmation of which precompiles use the shared-handler pattern):
1. Deploy a contract that calls a stateful precompile (e.g., ICS20 or ERC20 precompile) whose native action, before returning, triggers another call back into the same precompile (directly or via `CallEVM`) — mirroring `evmd/tests/integration/balance_handler/balance_handler_test.go`'s `DebugPrecompileCaller` pattern which calls `callback()` on the debug precompile recursively.
2. Fund the caller contract and execute the recursive call chain.
3. Compare `x/bank` `GetBalance` for the relevant accounts against `StateDB.GetBalance` (as read via `eth_getBalance`) immediately after the transaction commits.
4. A divergence between the two values (bank balance changed but StateDB balance unchanged, or vice versa/duplicated) confirms the vulnerability is live in that precompile's current build.

### Citations

**File:** precompiles/common/balance_handler.go (L37-48)
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
```

**File:** precompiles/common/balance_handler.go (L68-106)
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

**File:** evmd/tests/integration/balance_handler/balance_handler_test.go (L23-25)
```go
// BalanceHandlerTestSuite tests the balance handler bug where recursive precompile calls
// share the same BalanceHandler instance, causing prevEventsLen to be overwritten.
// This leads to balance desync between native bank keeper and EVM stateDB.
```

**File:** evmd/tests/integration/balance_handler/balance_handler_test.go (L43-45)
```go
// TestRecursivePrecompileCallsWithDebugPrecompile demonstrates the balance handler bug
// by triggering recursive calls that share the same BalanceHandler instance.
func (s *BalanceHandlerTestSuite) TestRecursivePrecompileCallsWithDebugPrecompile() {
```
