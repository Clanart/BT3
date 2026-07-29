## Analog Found

### Title
Shared `BalanceHandler` instance across recursive/nested precompile calls corrupts EVM StateDB balances relative to `x/bank` - (File: `precompiles/common/balance_handler.go`, `testutil/testdata/debug/debug.go`)

### Summary
The external report's bug class is: a critical ordering/state-window value (`toBlock`) is not properly scoped per-operation, so a shared mutable "index into history" gets overwritten across recursive/repeated invocations, breaking an invariant that depends on that index being consistent (sequential rewards → double payout). The Cosmos EVM analog is the `BalanceHandler.prevEventsLen` cursor used by precompiles to translate Cosmos SDK bank events into EVM `StateDB` balance mutations. This cursor is a mutable field on a `BalanceHandler` instance that is set in `BeforeBalanceChange` and read/consumed in `AfterBalanceChange`. When the same `BalanceHandler` instance is shared across nested/recursive precompile invocations within a single EVM call, the inner call's `BeforeBalanceChange` overwrites `prevEventsLen`, corrupting the event window the outer call later reads in `AfterBalanceChange`.

### Finding Description
`BalanceHandler` records a single integer cursor into the event log: [1](#0-0) 

`AfterBalanceChange` then applies bank `CoinSpent`/`CoinReceived`/`FractionalBalanceChange` events from `events[bh.prevEventsLen:]` onto the `StateDB` via `AddBalance`/`SubBalance`: [2](#0-1) 

The generic precompile execution path (`RunNativeAction`) creates a **new** `BalanceHandler` per invocation via a factory, which avoids cross-call cursor corruption: [3](#0-2) 

However, other precompile implementations obtain the handler via `p.GetBalanceHandler()` rather than constructing a fresh one per call, e.g.: [4](#0-3) 

If `GetBalanceHandler()` returns a handler instance that is reused across nested calls to the same precompile within one EVM transaction (e.g., a contract that calls a precompile which, through its own execution or a further contract call, re-enters the same precompile before the outer call's `AfterBalanceChange` has run), the inner call's `BeforeBalanceChange` resets `prevEventsLen` to a later index. When the outer call's `AfterBalanceChange` subsequently executes, it slices `events[bh.prevEventsLen:]` starting from the (now-advanced) inner cursor, silently **skipping** the outer call's own `CoinSpent`/`CoinReceived`/`FractionalBalanceChange` events. This desynchronizes the EVM `StateDB` balances (used for subsequent EVM execution, gas refund logic, and `eth_getBalance`) from the ground-truth `x/bank`/`x/precisebank` balances, since bank-side accounting is authoritative and committed independently of whether the StateDB mirror was updated.

This is a documented scenario in the repository's own test suite, whose comment explicitly states the mechanism: [5](#0-4) 

### Impact Explanation
A StateDB/bank balance desync directly violates the "Asset-representation path" invariant (1:1 accounting between native coins, ERC20 views, and precompile-visible balances). Depending on the direction of the skipped events, this can manifest as:
- EVM-visible balance (`eth_getBalance`, subsequent `CALL`/`transfer` value checks within the same transaction) failing to reflect a debit that already occurred in `x/bank`, allowing a contract's in-transaction logic to believe funds are still available when they were already spent (double-spend within a single transaction), or
- The reverse case (missed credit) causing funds to be irrecoverably invisible to the EVM view while still present in the bank ledger, which can strand contract-mediated balances.

Both cases are within the required Critical impact class: "irreversible accounting corruption of spendable user value across native balances, EVM balances ... precompile-mediated assets," reachable by any unprivileged user deploying a contract that triggers nested/recursive precompile calls (e.g., a caller contract invoking a precompile, whose callback re-enters the same precompile address, as demonstrated by the test's `callback` contract flow).

### Likelihood Explanation
Medium: exploitation requires an unprivileged contract to construct a call pattern that re-enters the same stateful precompile within a single EVM transaction before the outer call's `AfterBalanceChange` executes. This is achievable by any user deploying an arbitrary contract, and the repository's own test (`TestRecursivePrecompileCallsWithDebugPrecompile`) demonstrates that such recursive/nested precompile call patterns are reachable and produce anomalous event/balance-change counts. However, I could not fully verify from available context (1) the exact implementation of `GetBalanceHandler()` for production precompiles (staking, distribution, bank, ICS20, erc20) to confirm whether they use the safer per-call `BalanceHandlerFactory` pattern (as in `RunNativeAction`) or a persistent shared instance (as the debug precompile's `Run` suggests), and (2) whether a fix/mitigation already exists elsewhere in the dispatch path that prevents recursion into the same precompile instance. This uncertainty should be resolved by inspecting `GetBalanceHandler()`'s definition and precompile registration lifecycle (per-call vs. singleton) before treating this as a confirmed exploitable vulnerability in the production precompile set.

### Recommendation
- Scope `BalanceHandler` (and its `prevEventsLen` cursor) strictly to a single call frame: always construct a new instance via `BalanceHandlerFactory.NewBalanceHandler()` at the start of every precompile invocation (as already done in `RunNativeAction`), and audit all precompiles (including the debug precompile and any others using `GetBalanceHandler()`) to ensure none retain/reuse a handler instance across nested or sequential calls.
- Replace the single mutable `prevEventsLen int` with an explicit stack (or snapshot-based diffing keyed to the same journal/snapshot mechanism already used for `MultiStoreSnapshot`/`AddPrecompileFn`) so that nested calls cannot corrupt an outer call's event window.
- Add an invariant check (e.g., end-of-transaction reconciliation) that asserts `StateDB` balances for EVM-touched accounts equal `x/bank`/`x/precisebank` ground truth before commit, failing closed if desynchronized.

### Proof of Concept
The repository's own integration test demonstrates the mechanism (using the `debug` precompile and a caller contract that recursively invokes `callback`), asserting a specific, non-obvious event count that reflects processing of the events window: [6](#0-5) 

A concrete exploit PoC against a production, balance-mutating precompile (e.g., distribution's `withdrawDelegatorRewards`, ICS20 `transfer`, or bank precompile `send`) would need to: (1) deploy a contract that calls precompile P, and from within contract logic (before returning from the outer precompile call) triggers a second, nested call into the same precompile P; (2) observe that the outer call's bank-side balance change event is dropped from the `StateDB` update, while it is still applied in `x/bank`; (3) confirm via `eth_getBalance` vs. bank query that the two diverge after the transaction commits. I was not able to fully construct and verify this PoC against a specific production precompile within the scope of this investigation — this would require confirming whether `GetBalanceHandler()` for those precompiles returns a shared vs. fresh instance, which is unverified from the available context.

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

**File:** evmd/tests/integration/balance_handler/balance_handler_test.go (L23-25)
```go
// BalanceHandlerTestSuite tests the balance handler bug where recursive precompile calls
// share the same BalanceHandler instance, causing prevEventsLen to be overwritten.
// This leads to balance desync between native bank keeper and EVM stateDB.
```

**File:** evmd/tests/integration/balance_handler/balance_handler_test.go (L45-102)
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
```
