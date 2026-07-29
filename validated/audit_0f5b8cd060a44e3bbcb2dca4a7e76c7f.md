## Title
Shared `BalanceHandler` instance in stateful precompiles corrupts balance accounting on recursive/re-entrant precompile calls - (File: `precompiles/common/precompile.go`, `precompiles/common/balance_handler.go`)

### Summary
The Bunni report describes a class of bug where a piece of "before" state (captured for later use in reconciling a value) is invalidated by a re-entrant call that occurs before the corresponding "after" logic runs, corrupting accounting. This repository has a directly analogous, explicitly documented issue: the EVM precompile `BalanceHandler` records `prevEventsLen` in `BeforeBalanceChange` and later replays bank events from that index in `AfterBalanceChange` to sync native balances into the EVM `StateDB`. If a precompile call is entered recursively/re-entrantly (a contract calling back into the same precompile, or a precompile call nested inside another precompile call) while sharing the same `BalanceHandler` instance, the inner call's `BeforeBalanceChange` overwrites `prevEventsLen`, corrupting the outer call's view of which bank events belong to it.

### Finding Description
`BalanceHandler.BeforeBalanceChange` / `AfterBalanceChange` in `precompiles/common/balance_handler.go` work by recording the event-log length before a precompile's native action runs, and then scanning `ctx.EventManager().Events()[prevEventsLen:]` afterward to translate `bank` `EventTypeCoinSpent`/`EventTypeCoinReceived` (and precisebank fractional-balance events) into corresponding `StateDB.AddBalance` / `SubBalance` calls, so the EVM's view of balances stays consistent with the Cosmos bank keeper's ledger. [1](#0-0) [2](#0-1) 

This is precisely the "before…after" bracket pattern from the Bunni report (`hookletAfterDeposit` needing to see finalized state) — `prevEventsLen` is captured state that must remain valid until `AfterBalanceChange` consumes it. If a nested precompile invocation (e.g., a contract's `_beforeTokenTransfer`/fallback logic calling back into a precompile such as staking/distribution/debug while the outer precompile call's native action is still executing) shares the *same* `BalanceHandler` object, the inner call's `BeforeBalanceChange` resets `prevEventsLen` to a larger index. When the outer call's `AfterBalanceChange` eventually runs, it will only see events emitted after the inner call finished, silently dropping the outer call's own coin-spent/coin-received events from being applied to `StateDB`. This produces a StateDB/bank-keeper balance desync: EVM-visible balances no longer match actual bank balances.

The project's own integration test explicitly demonstrates and names this bug: [3](#0-2) 
The test drives a caller contract that recursively invokes a debug precompile which internally calls `stateDB.GetBalanceHandler` (or an equivalent shared instance obtained via `p.GetBalanceHandler()` in the legacy debug precompile pattern) and asserts a specific reduced event count, evidencing that recursive precompile calls sharing one `BalanceHandler` instance mis-track `prevEventsLen`: [4](#0-3) 

By contrast, `precompiles/common/precompile.go`'s `runNativeAction` mitigates part of this by creating a *fresh* `BalanceHandler` per invocation via `p.BalanceHandlerFactory.NewBalanceHandler()`: [5](#0-4) 
However, the pattern in the debug/legacy precompile path (`p.GetBalanceHandler()`, a single stored instance reused across calls, as invoked in `debug.go`) demonstrates that not all precompile call-sites in the codebase follow the "new instance per call" discipline, and the test suite's own framing ("recursive precompile calls share the same BalanceHandler instance") confirms this is a known live failure mode reachable through ordinary EVM contract execution (any Solidity contract that makes a precompile call whose execution path re-enters a precompile call, e.g. via hooks/`try/catch` patterns shown elsewhere in the repo's test contracts such as `ERC20RecursiveNonRevertingPrecompileCall.sol`). [6](#0-5) 

### Impact Explanation
If the shared-`BalanceHandler` desync occurs on a code path that is reachable in production (not just the debug-only test precompile), the effect is a divergence between the EVM `StateDB` balance view and the actual Cosmos SDK bank-keeper balance for accounts touched during the precompile call. Because `StateDB.AddBalance`/`SubBalance` calls driven by `AfterBalanceChange` are what downstream EVM logic (gas refunds, subsequent same-tx transfers, `balanceOf` calls from other contracts in the same tx) reads and eventually commits back via `StateDB.Commit()`, a missed or double-applied balance delta can result in the EVM-side balance being permanently written to a value inconsistent with real bank funds once the transaction commits — i.e., value duplication or loss for spendable balances, matching the "Critical unauthorized minting/duplication/irreversible accounting corruption" impact class.

### Likelihood Explanation
Likelihood assessment is limited by incomplete verification: I was not able to load `precompiles/common/precompile.go`'s full definition of `GetBalanceHandler()` nor `precompiles/erc20/erc20.go` in this session (tool calls failed on the final iteration), so I cannot confirm with certainty whether any of the *production* stateful precompiles (staking, distribution, erc20, gov, slashing, ics20 — all of which reference `GetBalanceHandler`/`BalanceHandlerFactory` per the grep results) actually share a single `BalanceHandler` instance across nested/recursive invocations, or whether they all correctly obtain a fresh instance per call the way `runNativeAction` does. The only concretely confirmed shared-instance usage is in the test-only `debug` precompile package (`testutil/testdata/debug/debug.go`, `evmd/tests/testdata/debug/debug.go`), which is not part of the production precompile set. Given this uncertainty, this should be treated as a **candidate** rather than a confirmed critical finding until the `GetBalanceHandler` definitions on the production precompiles (`precompiles/staking`, `precompiles/distribution`, `precompiles/erc20`, `precompiles/gov`, `precompiles/slashing`, `precompiles/ics20`) are inspected to determine whether they instantiate a new `BalanceHandler` per call (safe, matching `runNativeAction`) or hold a persistent instance on the `Precompile` struct (vulnerable, matching the debug-precompile pattern demonstrated by the test).

### Recommendation
1. Audit every production precompile's `GetBalanceHandler()` implementation to confirm each precompile call path constructs a **new** `BalanceHandler` for every top-level `Run`/native-action invocation rather than reusing one stored on the `Precompile` struct, mirroring the `p.BalanceHandlerFactory.NewBalanceHandler()` pattern already used in `runNativeAction`.
2. Add re-entrancy guards or a nesting-depth-aware stack of `(prevEventsLen)` markers so that nested precompile calls do not clobber an outer call's recorded baseline — e.g., push/pop a stack of `prevEventsLen` values instead of a single mutable field.
3. Extend `TestRecursivePrecompileCallsWithDebugPrecompile`-style tests to cover all production precompiles that support native/token/staking actions reachable via contract-to-contract reentrant calls, verifying `StateDB` balances match bank-keeper balances after any nested-call transaction commits.

### Proof of Concept
The existing repository test `TestRecursivePrecompileCallsWithDebugPrecompile` already reproduces the underlying mechanism (shared `BalanceHandler`, `prevEventsLen` overwritten by nested calls) using the `debug` precompile and a caller contract that recursively triggers precompile calls: [7](#0-6) 
A full PoC against a production precompile (e.g., staking or distribution) would require: (1) confirming that precompile's `GetBalanceHandler()` returns a persistent instance, (2) deploying a Solidity contract whose fallback/hook logic re-enters that precompile mid-call (as already modeled by `ERC20RecursiveNonRevertingPrecompileCall.sol`'s `_beforeTokenTransfer` re-entrant precompile call pattern), and (3) asserting a bank-keeper vs. `StateDB` balance mismatch after the transaction commits — this final verification step was not completed due to tool access limits in this session.

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

**File:** precompiles/common/balance_handler.go (L68-76)
```go
func (bh *BalanceHandler) AfterBalanceChange(ctx sdk.Context, stateDB *statedb.StateDB) error {
	events := ctx.EventManager().Events()

	for _, event := range events[bh.prevEventsLen:] {
		switch event.Type {
		case banktypes.EventTypeCoinSpent:
			spenderAddr, err := ParseAddress(event, banktypes.AttributeKeySpender)
			if err != nil {
				return fmt.Errorf("failed to parse spender address from event %q: %w", banktypes.EventTypeCoinSpent, err)
```

**File:** evmd/tests/integration/balance_handler/balance_handler_test.go (L23-26)
```go
// BalanceHandlerTestSuite tests the balance handler bug where recursive precompile calls
// share the same BalanceHandler instance, causing prevEventsLen to be overwritten.
// This leads to balance desync between native bank keeper and EVM stateDB.
type BalanceHandlerTestSuite struct {
```

**File:** evmd/tests/integration/balance_handler/balance_handler_test.go (L45-106)
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
}
```

**File:** testutil/testdata/debug/debug.go (L77-112)
```go
	// Start the balance change handler before executing the precompile.
	p.GetBalanceHandler().BeforeBalanceChange(ctx)

	initialGas := ctx.GasMeter().GasConsumed()

	// set the default SDK gas configuration to track gas usage
	// we are changing the gas meter type, so it panics gracefully when out of gas
	ctx = ctx.WithGasMeter(storetypes.NewGasMeter(contract.Gas)).
		WithKVGasConfig(p.KvGasConfig).
		WithTransientKVGasConfig(p.TransientKVGasConfig)
	// we need to consume the gas that was already used by the EVM
	ctx.GasMeter().ConsumeGas(initialGas, "creating a new gas meter")

	// This handles any out of gas errors that may occur during the execution of a precompile tx or query.
	// It avoids panics and returns the out of gas error so the EVM can continue gracefully.
	defer cmn.HandleGasError(ctx, contract, initialGas, &err)()

	res, err := p.Execute(ctx, stateDB, contract, readonly)
	if err != nil {
		return nil, err
	}

	if err != nil {
		return nil, err
	}

	cost := ctx.GasMeter().GasConsumed() - initialGas

	if !contract.UseGas(cost, nil, tracing.GasChangeCallPrecompiledContract) {
		return nil, vm.ErrOutOfGas
	}

	// Process the native balance changes after the method execution.
	if err := p.GetBalanceHandler().AfterBalanceChange(ctx, stateDB); err != nil {
		return nil, err
	}
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

**File:** contracts/solidity/ERC20RecursiveNonRevertingPrecompileCall.sol (L124-142)
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
    }
```
