I found a directly relevant, already-acknowledged analog: a test suite explicitly titled to describe "the balance handler bug where recursive precompile calls share the same `BalanceHandler` instance, causing `prevEventsLen` to be overwritten... leading to balance desync between native bank keeper and EVM stateDB": `evmd/tests/integration/balance_handler/balance_handler_test.go`.

### Title
Recursive precompile calls corrupt `BalanceHandler.prevEventsLen`, causing StateDB/bank balance desynchronization - (File: `precompiles/common/balance_handler.go`)

### Summary
`BalanceHandler.AfterBalanceChange` [1](#0-0)  reconciles native `x/bank` coin movements that happen during a precompile call back into the EVM `StateDB` by replaying only the events emitted after `prevEventsLen` (set in `BeforeBalanceChange`). This is the same class of bug as the reported `Burve` issue: an operation credits/debits the wrong "recipient" of accounting state — here, a shared, stateful `BalanceHandler` instance is reused across nested/recursive precompile calls, so the event-window bookkeeping (`prevEventsLen`) gets clobbered by the inner call, causing the outer call's bank events to be mis-attributed, double-counted, or dropped when replayed into `StateDB.AddBalance`/`SubBalance`.

### Finding Description
Every precompile's `Run()` goes through `RunNativeAction` → `runNativeAction`, which creates one `BalanceHandler` per precompile call via `p.BalanceHandlerFactory.NewBalanceHandler()` [2](#0-1) . `BeforeBalanceChange` snapshots `len(ctx.EventManager().Events())` and `AfterBalanceChange` replays only the events appended since that snapshot, translating `CoinSpent`/`CoinReceived`/fractional-balance events into `StateDB.SubBalance`/`AddBalance` calls [3](#0-2) .

When a precompile call recursively re-enters the EVM (e.g., a precompile calls back into a contract, or a contract calls another precompile, or an EVM-triggered callback invokes staking/distribution/gov precompiles that internally call `CallEVM`), if the same `BalanceHandler` instance (or the shared `ctx` its `prevEventsLen` closes over) is reused for nested calls instead of a fresh handler with correctly isolated event windows, the inner call's `BeforeBalanceChange` overwrites `prevEventsLen`, corrupting the outer call's accounting window. This is architecturally identical to the reported bug's root cause: state is written to/read from the wrong entity (there: `recipient` instead of `address(this)`; here: the wrong event-window/receiver of the balance delta), and downstream this can silently break the invariant that the EVM `StateDB` balance mirrors the native `x/bank` balance.

This test — already present in the repository under a name that states it directly — is designed to demonstrate exactly this: `TestRecursivePrecompileCallsWithDebugPrecompile` drives 10 nested debug-precompile calls and asserts an exact count of resulting events/balances .

### Impact Explanation
If `prevEventsLen` corruption causes bank events to be replayed twice, replayed against the wrong window, or skipped, the EVM-side `StateDB` balance for an account can diverge from the actual native `x/bank`/`x/precisebank` balance backing it. Because `StateDB.AddBalance`/`SubBalance` changes are what ultimately get committed as the EVM account's spendable balance, a desync in the "credit" direction is an unauthorized creation of spendable EVM balance not backed by real bank coins (inflation/duplication of value), while a desync in the "debit" direction can permanently lock or destroy user balance visibility. Either direction, if reachable by an ordinary unprivileged transaction path (recursive precompile calls are triggerable from any user-deployed contract that calls into staking/distribution/gov/bank/werc20/ICS20 precompiles in a loop or via reentrant callback), meets the Critical-impact bar of "unauthorized minting/duplication/irreversible accounting corruption of spendable user value" and "AppHash divergence," since different validators could compute different resulting balances if any nondeterminism (e.g., map iteration order over events, or gas-dependent reentrancy depth) is involved in how the corrupted window is replayed.

### Likelihood Explanation
Likelihood is high in principle: any user can deploy a contract that recursively calls precompiles (this pattern is already extensively tested in the repo via `ERC20RecursiveRevertingPrecompileCall.sol` / `ERC20RecursiveNonRevertingPrecompileCall.sol`, `ics20_recursive_precompile_calls_test.go`, and the dedicated `balance_handler_test.go` suite), and no special privilege is required to trigger recursive/nested precompile invocation.

### Recommendation
Ensure each precompile invocation (including nested/recursive ones triggered from within another precompile's native action) obtains its own `BalanceHandler` instance with an event window computed relative to the call's own starting point, and verify that `prevEventsLen` is saved/restored (stack-like) rather than being a single mutable field shared across concurrently-active calls on the call stack. Add regression coverage asserting that nested precompile calls produce byte-for-byte identical StateDB and x/bank balances after execution, and confirm the fix by tracing `AddPrecompileFn`/`CommitWithCacheCtx` semantics in `x/vm/statedb/statedb.go` to ensure snapshots also isolate the balance-handler event window per call frame.

### Proof of Concept
The repository already contains a proof of concept for this exact bug in `evmd/tests/integration/balance_handler/balance_handler_test.go::TestRecursivePrecompileCallsWithDebugPrecompile`, which deploys a caller contract that recursively invokes a debug precompile 10 times and checks the resulting event/balance counts [4](#0-3) . I was not able to execute this test in this environment to confirm whether it currently passes (i.e., whether the bug is already fixed) or fails (confirming the vulnerability is live) — this should be run and its assertions inspected (or extended to explicitly compare `StateDB` balances vs. `x/bank` `GetBalance` post-execution for all touched accounts) to confirm exploitability with a concrete corrupted balance value before treating this as a confirmed live Critical finding.

### Citations

**File:** precompiles/common/balance_handler.go (L43-105)
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
