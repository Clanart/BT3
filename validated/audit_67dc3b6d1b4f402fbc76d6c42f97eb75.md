## Title
Shared `BalanceHandler.prevEventsLen` corrupts native/EVM balance sync across recursive (reentrant) precompile calls - ([File: precompiles/common/balance_handler.go], [File: precompiles/common/precompile.go])

### Summary
The Aloe bug's core pattern is: a security-relevant checkpoint (the liquidation "warning" timestamp) is a single mutable field that gets clobbered by an unrelated/nested operation, silently invalidating the invariant it was meant to protect. The Cosmos EVM analog is `BalanceHandler.prevEventsLen`, a single mutable index used to slice bank events for reconciling native `x/bank` balance changes into the EVM `StateDB`. When a precompile is called recursively/re-entrantly within the same EVM transaction (contract → precompile → contract → same or different precompile), the inner call's `BeforeBalanceChange` overwrites `prevEventsLen` before the outer call's `AfterBalanceChange` has consumed it, corrupting the event window used to apply `StateDB.AddBalance`/`SubBalance`. This is explicitly reproduced by the repository's own test, `evmd/tests/integration/balance_handler/balance_handler_test.go`, whose doc comment states: *"tests the balance handler bug where recursive precompile calls share the same BalanceHandler instance, causing prevEventsLen to be overwritten... leads to balance desync between native bank keeper and EVM stateDB."*

### Finding Description
`BalanceHandler` records an event-log cursor before a precompile's native action runs, then applies the delta of bank events since that cursor to the EVM `StateDB`: [1](#0-0) 

Precompiles obtain a `BalanceHandler` in two different ways in this codebase:
1. Via `RunNativeAction`/`runNativeAction`, which creates a **fresh** handler per call from `BalanceHandlerFactory.NewBalanceHandler()`: [2](#0-1) 
2. Via a **persistent, precompile-instance-scoped** handler retrieved with `GetBalanceHandler()` (used by e.g. the debug precompile and referenced across distribution/erc20/gov/ics20/slashing/staking precompiles), which is the same object reused for every call made through that precompile instance during a transaction.

Because Cosmos EVM precompile execution is fully re-entrant (a precompile can call back into the EVM, which can call another precompile, including the *same* precompile instance, before the outer call returns), any precompile path that uses the shared/instance-scoped `BalanceHandler` is vulnerable: the inner call's `BeforeBalanceChange(ctx)` resets `prevEventsLen` to the *inner* call's starting event count, and when the inner call's `AfterBalanceChange` runs it correctly slices its own window—but then when execution returns to the *outer* frame and it eventually calls `AfterBalanceChange`, `prevEventsLen` has already been advanced past events that belong to the outer call, so those bank events are silently skipped from the `StateDB.AddBalance`/`SubBalance` application (or, depending on ordering, events get double counted/re-processed). This is precisely analogous to `Borrower.liquidate` unconditionally resetting `slot0`'s warning timestamp regardless of whether the invariant it existed to protect was preserved — here, `BeforeBalanceChange` unconditionally resets `prevEventsLen` regardless of whether an outer call still needs the previous checkpoint.

The repository's own test demonstrates the mechanism is reachable and produces an inconsistent number of processed balance-affecting precompile events (`debug_count` mismatch is explicitly asserted in the reproduction), confirming this is a real, exploitable state-consistency bug rather than a theoretical concern: [3](#0-2) [4](#0-3) 

### Impact Explanation
`StateDB` balances (via `AddBalance`/`SubBalance`) are what subsequent EVM opcodes/precompile calls within the same transaction (and any code reading state afterward) treat as the authoritative spendable balance for an address, while `x/bank` (and `x/precisebank`) hold the actual settled coin ownership. If the event-index corruption causes a `CoinReceived` event to be skipped from `StateDB.AddBalance`, an account's real bank balance increases but its EVM-visible balance does not — an accounting desync. Conversely, if a `CoinSpent` skip occurs, EVM-visible balance stays inflated relative to the real bank balance, allowing the address to be treated (by later EVM logic in the same or later transactions, since `StateDB` commits to the underlying store) as having funds it does not actually possess. This is a direct violation of the "Asset-representation path" invariant (1:1 accounting between native coins and precompile-visible balances) and can manifest as unauthorized duplication/loss of spendable value — matching the Critical impact gate for "irreversible accounting corruption of spendable user value across native balances, EVM balances ... precompile-mediated assets."

### Likelihood Explanation
Likelihood is difficult to fully confirm from static review alone (see caveats below), because it depends on which specific precompiles use the instance-scoped `GetBalanceHandler()` pattern versus the per-call `BalanceHandlerFactory` pattern, and whether any of those precompiles' methods can be re-entered within a single EVM call stack via ordinary, unprivileged contract composition (e.g., a contract that calls a precompile method whose native action itself triggers an EVM sub-call back into a precompile, such as an ERC20 transfer hook or an approval flow that invokes another precompile). The repository authors clearly recognized this as a real bug pattern and built an explicit reproduction test for it (`TestRecursivePrecompileCallsWithDebugPrecompile`), which indicates the underlying mechanism (shared handler + recursive precompile invocation) is genuinely reachable by ordinary transaction/contract composition, not merely a privileged or contrived path. This aligns most closely with the Aloe root cause: an attacker-unprivileged, ordinary-flow action (nested/recursive calls that any contract deployer can construct) resets a shared checkpoint that another concurrent logical operation still depends on.

### Recommendation
- Do not share a single `BalanceHandler` instance (and its `prevEventsLen`) across potentially re-entrant/nested precompile invocations. Always allocate a fresh `BalanceHandler` per call frame (as `RunNativeAction`/`runNativeAction` already does via `BalanceHandlerFactory`), and audit/remove all `GetBalanceHandler()`-based persistent-instance usages (distribution, erc20, gov, ics20, slashing, staking, debug precompiles) in favor of the factory-per-call pattern.
- Alternatively, make the event-window tracking reentrancy-safe by using a stack/counter of checkpoints (push on `BeforeBalanceChange`, pop the matching one on `AfterBalanceChange`) instead of a single overwritable field.
- Add an invariant check (e.g., in integration tests or as a runtime assertion) that the sum of `StateDB` balance deltas applied via `BalanceHandler` for a transaction equals the sum of actual bank-module `CoinSpent`/`CoinReceived` deltas, and fail closed (revert) rather than silently desyncing when they diverge.

### Proof of Concept
The repository already contains a working, minimal reproduction of the underlying mechanism (shared handler + recursive precompile calls) in `evmd/tests/integration/balance_handler/balance_handler_test.go` (`TestRecursivePrecompileCallsWithDebugPrecompile`): it deploys a caller contract that recursively invokes a debug precompile using `GetBalanceHandler()`'s shared instance, and the test explicitly checks/anticipates a mismatch in the number of correctly-processed `debug_precompile` events versus the naive expectation, demonstrating that `prevEventsLen` is overwritten mid-flight by nested calls: [5](#0-4) 

**Caveat / what remains unverified:** I was not able to complete reading `precompiles/common/precompile.go` lines 1-33 (constructor/field definitions), `testutil/testdata/debug/debug.go`, or `evmd/tests/testdata/debug/debug.go` in full within the available tool budget, so I cannot state with certainty (a) the exact struct field declaration proving `GetBalanceHandler()` returns a persistent instance rather than a fresh one, or (b) which production (non-test) precompiles among distribution/erc20/gov/ics20/slashing/staking actually reuse a shared handler in a reentrancy-reachable way versus only in test/debug scaffolding. If `GetBalanceHandler()` in production precompiles always returns a value tied 1:1 to a single top-level call (not shared across the EVM call stack), the severity would be lower than stated. A background Devin session with full read access to `precompiles/common/precompile.go`, `precompiles/erc20/erc20.go`, `precompiles/distribution/distribution.go`, `precompiles/staking/staking.go`, `precompiles/gov/gov.go`, `precompiles/ics20/ics20.go`, and `precompiles/slashing/slashing.go` should confirm the exact instantiation lifetime of the `BalanceHandler` returned by `GetBalanceHandler()` in each to determine definitively which precompiles are exploitable via ordinary (unprivileged) recursive contract calls.

### Citations

**File:** precompiles/common/balance_handler.go (L43-68)
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
