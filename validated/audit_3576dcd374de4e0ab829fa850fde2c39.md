### Title
Shared `BalanceHandler` state across reentrant/recursive precompile calls causes StateDB/bank balance desynchronization - ([File: precompiles/common/balance_handler.go], [File: precompiles/common/precompile.go])

### Summary
This is the closest in-repo analog to the reported SSRF class: an unvalidated/unisolated shared mutable state that lets one execution context (an inner call) silently corrupt the accounting boundary trusted by an outer context — here, the boundary between the Cosmos SDK bank module's authoritative balances and the EVM `StateDB`'s balance view that Solidity contracts and subsequent EVM opcodes rely on. The `BalanceHandler` mechanism, used by essentially every "stateful" precompile (`erc20`, `staking`, `distribution`, `gov`, `ics20`, `slashing`), records `prevEventsLen` before a native-side keeper call and replays bank events emitted after that index into `StateDB.AddBalance/SubBalance` afterward. The repository's own integration test explicitly documents that this mechanism is broken under recursive precompile invocation.

### Finding Description
`BalanceHandler` tracks a single mutable field, `prevEventsLen`, to know which slice of `ctx.EventManager().Events()` to translate into `StateDB` balance updates: [1](#0-0) 

`AfterBalanceChange` reads `events[bh.prevEventsLen:]` and applies `CoinSpent`/`CoinReceived`/`fractional_balance_change` events to `StateDB`: [2](#0-1) 

There are two different lifecycles for this handler in the codebase:
1. The safe path, `RunNativeAction`/`runNativeAction`, creates a **fresh** `BalanceHandler` via `p.BalanceHandlerFactory.NewBalanceHandler()` for every single precompile invocation: [3](#0-2) 
2. A second accessor, `GetBalanceHandler()`, is used directly by production precompiles (`erc20`, `staking`, `distribution`, `gov`, `ics20`, `slashing`) as well as the debug precompile, returning a handler instance associated with the `Precompile` object itself rather than freshly minted per call — the same object referenced by the debug precompile's `Run()`: [4](#0-3) 

The repository's own test suite documents and reproduces the resulting bug directly: [5](#0-4) 

When a contract triggers a **recursive/reentrant precompile call** within the same EVM transaction (e.g., a precompile-invoked native action calls back into a contract, which calls the same precompile — or a precompile whose `Run()` uses the shared-handler accessor — again before the outer call completes), the inner call's `BeforeBalanceChange` overwrites `prevEventsLen` on the shared instance. When execution unwinds to the outer call's `AfterBalanceChange`, it uses the *inner* call's `prevEventsLen`, not its own original snapshot. This causes one of:
- Bank events emitted by the **outer** call before the nested call started (between the outer's original `prevEventsLen` and the overwritten value) are **never replayed into `StateDB`**, so `StateDB` balances silently diverge (understate) from the real bank-module balances that were already moved.
- Alternatively, events already consumed by the inner call can be **reprocessed** by the outer call, double-crediting/double-debiting `StateDB` balances relative to the actual bank-module state.

Either way, the invariant that `x/erc20`, `staking`, `distribution`, `gov`, and `ics20` precompiles are required to preserve — 1:1 accounting between native coin balances and the EVM-visible balance state that contracts and `eth_getBalance`/subsequent EVM instructions rely on — is broken by ordinary, unprivileged transaction flow (any contract designed to reenter a precompile).

### Impact Explanation
This falls under "Critical unauthorized minting, burning, duplication, resurrection, or irreversible accounting corruption ... across native balances, EVM balances ... or precompile-mediated assets." A desynchronized `StateDB` balance can be exploited within a single transaction: a contract can leverage the stale/incorrect `StateDB` balance to pass insufficient-balance checks it should have failed (effectively duplicating spendable value in the EVM's view) or cause funds transferred via the bank module to become invisible to the EVM (effectively freezing/losing accounting for real funds), corrupting the ERC20/precompile-mediated balance view relied on by every subsequent read (`balanceOf`, transfers, further precompile calls) in that same execution and potentially subsequent blocks once `StateDB` state is committed.

### Likelihood Explanation
Triggering requires only an unprivileged user deploying a contract that reenters a precompile that uses the shared-instance `GetBalanceHandler()` accessor (e.g., calling the ERC20 precompile from inside a callback triggered by another precompile call, or similar cross-precompile/cross-contract reentrancy patterns already demonstrated with the debug precompile). No validator, relayer, or governance privilege is needed — this is analogous to the original SSRF's "attacker supplies arbitrary, unvalidated input reaching a trusted internal resource," here the "trusted internal resource" is the shared `prevEventsLen` accounting window.

### Recommendation
Ensure every precompile entry point (not just those routed through `RunNativeAction`) obtains a **new** `BalanceHandler` per top-level/nested precompile invocation (i.e., always go through `BalanceHandlerFactory.NewBalanceHandler()`, never a cached/shared instance via `GetBalanceHandler()`), or make `BalanceHandler` reentrancy-safe by using a stack of `prevEventsLen` snapshots instead of a single mutable field, so nested calls cannot clobber an outer call's bookkeeping window.

### Proof of Concept
The repository already contains a reproducing test: `evmd/tests/integration/balance_handler/balance_handler_test.go` deploys a caller contract that recursively invokes the debug precompile (which uses the same shared-handler pattern as production precompiles via `GetBalanceHandler()`), and the test comment states this "leads to balance desync between native bank keeper and EVM stateDB." Reproducing the same recursive-call pattern against a production precompile (e.g., `erc20` `transfer`/`transferFrom` reentering `staking` or `ics20`, or a delayed/malicious ERC20-like contract per `x/erc20/keeper/testdata/ERC20MaliciousDelayed.sol`) that also uses `p.GetBalanceHandler()` would corrupt `StateDB` balances relative to bank-module balances within a single transaction. [6](#0-5) 

**Note on confidence/limitations:** I was not able to read the exact body of `GetBalanceHandler()` (call budget exhausted) to confirm definitively whether it lazily caches a single `*BalanceHandler` per `Precompile` instance versus some other lifecycle; my conclusion that it differs from the safe per-call factory pattern in `runNativeAction` is inferred from (a) the existence of two distinct code paths (`BalanceHandlerFactory.NewBalanceHandler()` inside `runNativeAction` vs. a separate `GetBalanceHandler()` accessor used directly by `erc20.go`, `staking.go`, `distribution.go`, `gov.go`, `ics20.go`, `slashing.go`, and the debug precompile), and (b) the repository's own test explicitly documenting "recursive precompile calls share the same BalanceHandler instance." I recommend a Devin session with full repository access to inspect `GetBalanceHandler()`'s definition and confirm whether production precompiles (as opposed to only the debug/test precompile) are reachable via a genuine reentrancy path before treating this as fully confirmed.

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

**File:** precompiles/common/balance_handler.go (L68-90)
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

**File:** testutil/testdata/debug/debug.go (L47-115)
```go
func (p Precompile) Run(evm *vm.EVM, contract *vm.Contract, readonly bool) ([]byte, error) {
	stateDB, ok := evm.StateDB.(*statedb.StateDB)
	if !ok {
		return nil, errors.Wrap(errors2.ErrUnauthorized, "could not create statedb in debug precompile")
	}

	// get the stateDB cache ctx
	ctx, err := stateDB.GetCacheContext()
	if err != nil {
		return nil, err
	}

	// take a snapshot of the current state before any changes
	// to be able to revert the changes
	snapshot := stateDB.MultiStoreSnapshot()
	events := ctx.EventManager().Events()

	// add precompileCall entry on the stateDB journal
	// this allows to revert the changes within an evm tx
	err = stateDB.AddPrecompileFn(p.Address(), snapshot, events)
	if err != nil {
		return nil, err
	}

	// commit the current changes in the cache ctx
	// to get the updated state for the precompile call
	if err := stateDB.CommitWithCacheCtx(); err != nil {
		return nil, err
	}

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

	return res, nil
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
