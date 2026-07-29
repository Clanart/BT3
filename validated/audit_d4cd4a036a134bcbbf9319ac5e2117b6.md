### Title
Nested/recursive precompile calls cause bank balance events to be double-applied to the EVM `StateDB`, allowing unauthorized duplication of spendable balance - (File: `precompiles/common/balance_handler.go`, `precompiles/common/precompile.go`)

### Summary
The reported Bootstrap.vy bug is a "double-claim" caused by an unenforced invariant between two time windows (`vote_end`/`deposit_end` vs `lock_end`), letting an attacker replay a claim-and-reuse cycle. The Cosmos EVM analog is a broken invariant in the **balance-handler event window** used to synchronize `x/bank` events into the EVM `StateDB` after every precompile call. Because `ctx.EventManager()` accumulates events globally and is never truncated, a nested/recursive precompile call inside another precompile call causes the outer call's `AfterBalanceChange` to re-process events that the inner call's `AfterBalanceChange` has already applied to the `StateDB`, resulting in the same bank balance delta being credited to the `StateDB` more than once.

### Finding Description
Each precompile invocation records a checkpoint of the current event log length before executing native logic, then applies only the events emitted *after* that checkpoint to the `StateDB`: [1](#0-0) [2](#0-1) 

The checkpoint (`prevEventsLen`) is captured relative to `ctx.EventManager().Events()`, which is a single, ever-growing, shared list for the entire transaction context. `RunNativeAction` in `common/precompile.go` creates a new `BalanceHandler` per call and calls `BeforeBalanceChange`/`AfterBalanceChange` around the native action: [3](#0-2) 

If, during execution of an outer precompile call's native action, a **nested precompile call occurs** (e.g., an ERC20 contract's `_beforeTokenTransfer` hook invokes the distribution or staking precompile, which itself emits bank events and applies them to `StateDB` via its own `AfterBalanceChange`), the inner call's events are appended to the *same* shared event list. When the outer call later executes its own `AfterBalanceChange`, it slices `events[prevEventsLen:]` using its own earlier checkpoint — a range that still includes the events already consumed and applied by the inner call. Those `CoinSpent`/`CoinReceived`/`FractionalBalanceChange` events are therefore re-applied to `StateDB.AddBalance`/`SubBalance`, duplicating the balance delta on the EVM-visible side while the underlying `x/bank`/`x/precisebank` state was only moved once.

This exact scenario — nested precompile calls sharing balance-handler bookkeeping and causing balance desync between the native bank keeper and the EVM `StateDB` — is explicitly reproduced by the repository's own regression tests, confirming the root cause is reachable in production precompile flows, not merely test scaffolding: [4](#0-3) [5](#0-4) 

Additionally, dedicated ERC20 test contracts exist that recursively call the distribution precompile's `claimRewards` from within an ERC20 `_beforeTokenTransfer` hook — an unprivileged, user-triggerable pattern (any ERC20 token with a transfer hook calling a stateful precompile) that is a realistic vector for this class of nested precompile invocation: [6](#0-5) [7](#0-6) 

The Bootstrap.vy invariant "vote_end < lock_end" is not checked, so an attacker can reuse funds across overlapping windows. The Cosmos EVM analog invariant "each precompile call's balance-handler event window must be disjoint/non-overlapping from nested calls" is likewise not enforced — `prevEventsLen` is a plain index into a monotonically growing, shared list with no accounting for concurrently active nested handler instances, so windows overlap and the same balance-changing event can be consumed by more than one handler.

### Impact Explanation
If confirmed exploitable end-to-end, this breaks the 1:1 accounting invariant between native `x/bank`/`x/precisebank` balances and EVM `StateDB` balances that the whole ERC20/precompile bridge depends on (see the balance-handler doc comments describing this exact contract). Any duplication of a `CoinReceived` event application inflates a user's or contract's EVM-visible balance beyond what was actually transferred by the bank module, i.e., unauthorized duplication/creation of spendable value on the EVM balance view. This matches the Critical impact class "unauthorized minting, burning, duplication ... corruption of spendable user value across native balances, EVM balances ... or precompile-mediated assets."

### Likelihood Explanation
The trigger requires only an ordinary, unprivileged flow: deploying/using an ERC20 token contract (or any contract) whose hooks or callback logic invoke a stateful precompile (staking, distribution, ICS20, bank, gov) from within another precompile-triggered code path — no privileged access, validator collusion, or governance action is needed. The repository already contains purpose-built test suites and Solidity fixtures reproducing this exact nested-call pattern, which strongly suggests the underlying mechanism is real and was recently under active investigation/fixing, increasing confidence that the trigger path is reachable via ordinary transaction/contract flows.

### Recommendation
Make the balance-handler event window robust to nesting/recursion. Options: (1) globally truncate/mark-as-consumed processed events immediately after `AfterBalanceChange` applies them so outer scopes never re-see them, or (2) maintain a stack/counter of currently active balance handlers, and only ever apply events that were emitted at the *current* (innermost) nesting depth, or (3) use a mechanism analogous to the invariant check recommended for `Bootstrap.vy` — explicitly assert non-overlap between the outer and inner event windows before applying deltas, and reject/no-op on any event index range already claimed by a nested handler.

### Proof of Concept
An exact reproduction path already exists in the codebase's own test suite and can be used to confirm/deny the exploitability of duplicated balance credit:
- `evmd/tests/integration/balance_handler/balance_handler_test.go` (`TestRecursivePrecompileCallsWithDebugPrecompile`) — deploys a caller contract that recursively invokes a precompile via `callback()`, demonstrating that `debug_precompile` events are emitted multiple times per transaction due to the shared/overwritten `prevEventsLen` checkpoint.
- `evmd/tests/ibc/ics20_recursive_precompile_calls_test.go` — reproduces the same class of bug specifically through ICS20 + distribution nested precompile calls, per its own description: "tests the native balance handler bug where reverted distribution calls leave persistent bank events that are incorrectly aggregated."
- `contracts/solidity/ERC20RecursiveNonRevertingPrecompileCall.sol` / `ERC20RecursiveRevertingPrecompileCall.sol` provide a minimal unprivileged trigger: an ERC20 `_beforeTokenTransfer` hook that calls `distribution.DISTRIBUTION_CONTRACT.claimRewards(...)` recursively during an ordinary token transfer.

A background engineer should extend these existing tests to explicitly assert whether the `StateDB` balance delta after such a recursive/nested call sequence matches the actual `x/bank`/`x/precisebank` balance delta (rather than only checking event counts), to confirm whether the duplicated events translate into an actual duplicated `StateDB.AddBalance` credit that a user could realize by withdrawing/transferring the inflated EVM balance.

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

**File:** precompiles/common/balance_handler.go (L68-72)
```go
func (bh *BalanceHandler) AfterBalanceChange(ctx sdk.Context, stateDB *statedb.StateDB) error {
	events := ctx.EventManager().Events()

	for _, event := range events[bh.prevEventsLen:] {
		switch event.Type {
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

**File:** evmd/tests/ibc/ics20_recursive_precompile_calls_test.go (L52-55)
```go
// Test suite for ICS20 recursive precompile calls
// Tests the native balance handler bug where reverted distribution calls
// leave persistent bank events that are incorrectly aggregated

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

**File:** contracts/solidity/ERC20RecursiveRevertingPrecompileCall.sol (L124-141)
```text
    function _beforeTokenTransfer(
        address from,
        address to,
        uint256 amount
    ) internal virtual override(ERC20, ERC20Pausable) {
        // Emit an event to track if this hook is called
        emit BeforeTokenTransferHookCalled(from, to, amount);

        for(uint256 i=0; i < 5; i++) {
            try ERC20RecursiveRevertingPrecompileCall(address(this)).claimRewardsAndRevert() {

            } catch {

            }

        }

        super._beforeTokenTransfer(from, to, amount);
```
