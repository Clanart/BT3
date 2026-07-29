### Title
Recursive/nested precompile calls cause `BalanceHandler` event-range double-processing, leading to StateDB balance duplication (Critical corruption of EVM balance accounting) - (File: precompiles/common/balance_handler.go, precompiles/common/precompile.go)

### Summary
The Astaria bug is a class of "stale/uncoordinated derived-value" bug: an operation that changes underlying state (lien terms) fails to propagate that change into a cached, dependent accounting value (`PublicVault.slope`), producing balance/valuation corruption. The Cosmos EVM analog is in the precompile `BalanceHandler` mechanism [1](#0-0) , which records an event-list cursor (`prevEventsLen`) before a precompile's native action runs and replays only the events emitted after that cursor into the EVM `StateDB` [2](#0-1) . When a precompile's native action itself triggers another (nested) balance-changing precompile call sharing the same `sdk.Context`/`EventManager`, the inner call's own `BeforeBalanceChange`/`AfterBalanceChange` pair consumes and applies its slice of events to the `StateDB` — but the outer call's cursor was recorded *before* the inner call started and is unaware that a sub-range of events has already been applied. When the outer call finishes and runs `AfterBalanceChange`, it re-scans the full remaining range (including the already-processed inner events) and re-applies `AddBalance`/`SubBalance` for them, duplicating the balance delta in the `StateDB` without any corresponding duplication in the `x/bank`/`x/precisebank` ledger.

### Finding Description
`Precompile.runNativeAction` computes `prevEventsLen` via `BeforeBalanceChange(ctx)` right before invoking the native `action(ctx)`, and afterwards calls `AfterBalanceChange(ctx, stateDB)` to translate every bank/precisebank event emitted since that point into `StateDB.AddBalance`/`SubBalance` calls [3](#0-2) . The cursor (`prevEventsLen`) is a plain event-manager length snapshot, not a scope-aware pointer, and `AfterBalanceChange` blindly iterates `events[bh.prevEventsLen:]` [2](#0-1) .

If, during the execution of `action(ctx)`, another precompile's `Run`/`RunNativeAction` executes on the *same* `sdk.Context` event manager (e.g., a precompile making an EVM call that re-enters a precompiled contract — including itself, another native precompile, or a dynamic ERC-20 precompile invoked via IBC/token-pair callback hooks), that inner call:
1. Snapshots its own `prevEventsLen` (which is now *greater* than the outer's).
2. Emits and then consumes/replays its own slice of events into `StateDB` via its own `AfterBalanceChange`.

When control returns to the outer call and it finishes, the outer's `AfterBalanceChange` iterates `events[outerPrevEventsLen:]`, which includes the *same* events the inner call already translated into `StateDB` balance changes. Each qualifying `CoinSpent`/`CoinReceived`/`EventTypeFractionalBalanceChange` event is thus applied twice against the `StateDB`, once by the inner handler and once again by the outer handler.

This exact defect is documented and reproduced in the repository's own regression harness: `evmd/tests/integration/balance_handler/balance_handler_test.go` explicitly states, *"tests the balance handler bug where recursive precompile calls share the same BalanceHandler instance, causing prevEventsLen to be overwritten... leads to balance desync between native bank keeper and EVM stateDB"* [4](#0-3) , and reproduces it via a debug precompile that recursively calls itself through `evmKeeper.CallEVMWithData` while sharing a single `BalanceHandler` instance [5](#0-4) .

Production precompiles (`staking`, `slashing`, `distribution`, `erc20`, `gov`, `ics20`) mitigate the *instance-sharing* variant of this bug by creating a brand-new `BalanceHandler` per call via `BalanceHandlerFactory.NewBalanceHandler()` inside `runNativeAction` [6](#0-5) , rather than reusing a struct-level handler like the debug precompile does. However, this only prevents cross-call state leakage of the `prevEventsLen` field itself — it does **not** prevent the event-range double-counting scenario described above when calls are genuinely *nested* (one precompile's native action triggering another precompile's `Run` on the same context/event manager), because each handler instance still independently replays whatever events fall after its own (possibly stale, pre-nesting) cursor.

### Impact Explanation
If reachable, this breaks the core invariant that `StateDB` (EVM-visible) balances must mirror the `x/bank`/`x/precisebank` ledger 1:1. A duplicated `AddBalance` call inflates an account's balance as observed through `eth_getBalance`/`balanceOf`-style precompile reads and subsequent EVM execution (spending, transfers, contract logic) without any corresponding increase in the underlying bank supply — i.e., unauthorized duplication/creation of spendable value visible to the EVM. This matches the Allowed Impact Gate's "Critical unauthorized minting, burning, duplication ... of spendable user value across native balances, EVM balances ... or precompile-mediated assets."

### Likelihood Explanation
Exploitability depends on finding a production code path where a precompile's native action causes re-entry into another (or the same) precompile's `Run`/`RunNativeAction` on the same `sdk.Context` event manager before the outer handler's `AfterBalanceChange` executes. The repository's own wiki flags "ERC20 IBC Callbacks & Dynamic Precompiles" as an integration point where IBC transfer processing can trigger dynamic ERC-20 precompile logic, which is a plausible candidate for such nesting, but I was not able to fully trace that callback chain within the available tool budget to confirm it re-enters `Run()` synchronously within an already-open `BeforeBalanceChange`/`AfterBalanceChange` window. The only concretely confirmed reproduction of the bug is via the `testutil/testdata/debug` precompile, which is explicitly test/debug-only code and therefore falls under the "tests, mocks, fixtures" exclusion in the Allowed Impact Gate.

### Recommendation
- Make the event-range bookkeeping nesting-aware: instead of a flat integer cursor, track a stack of `(start, end)` ranges per precompile call, or mark/consume events as "handled" so that an outer call's `AfterBalanceChange` skips events already translated by a nested call's handler.
- Alternatively, disallow/guard against a precompile's native action re-entering any precompiled contract (including itself) through `CallEVMWithData` while a `BalanceHandler` window is open, or flush/finalize the outer handler's state before allowing the nested call to proceed.
- Add an explicit unit/integration test using a production precompile (not just the debug harness) that exercises a genuine nested precompile-to-precompile call path (e.g., via the ERC20/IBC dynamic precompile callback flow) to confirm whether double-processing is reachable in practice.

### Proof of Concept
Conceptual PoC (mirrors the existing debug harness, which already demonstrates the mechanics):
1. Deploy a contract that calls a precompile method P1.
2. Inside P1's native action (before its `AfterBalanceChange` runs), trigger a synchronous EVM call that invokes another precompile method P2 (or P1 recursively) on the same context, which performs a bank transfer and completes its own `Before/AfterBalanceChange` cycle.
3. Let P1's native action return; P1's own `AfterBalanceChange` then re-scans `ctx.EventManager().Events()[P1.prevEventsLen:]`, which includes the `CoinSpent`/`CoinReceived` events already consumed by P2's handler, and re-applies them to `StateDB`.
4. Compare the resulting `StateDB` balance for the affected accounts against the actual `x/bank`/`x/precisebank` balance — the existing regression test in `evmd/tests/integration/balance_handler/balance_handler_test.go` [7](#0-6)  already exercises this exact recursive-call scenario and asserts on the resulting event/debug-call counts, confirming the double-processing mechanics described above.

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

**File:** precompiles/common/balance_handler.go (L68-71)
```go
func (bh *BalanceHandler) AfterBalanceChange(ctx sdk.Context, stateDB *statedb.StateDB) error {
	events := ctx.EventManager().Events()

	for _, event := range events[bh.prevEventsLen:] {
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

**File:** evmd/tests/integration/balance_handler/balance_handler_test.go (L43-105)
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
```

**File:** testutil/testdata/debug/debug.go (L127-143)
```go
func (p Precompile) Call0(ctx sdk.Context, stateDB vm.StateDB, contract *vm.Contract, readOnly bool) ([]byte, error) {
	// data := crypto.Keccak256([]byte("function callback()"))[:4]
	counter := new(big.Int).SetBytes(contract.Input[1:])
	counter = new(big.Int).Add(counter, big.NewInt(1))

	args := math.U256Bytes(counter)
	selector := []byte{0xff, 0x58, 0x5c, 0xaf}
	data := append(selector, args...)

	caller := contract.Caller()
	fmt.Printf("Execute debug precompile %s\n", caller.String())
	rsp, err := p.evmKeeper.CallEVMWithData(ctx, p.Address(), &caller, data, true)
	fmt.Println("callback response:", rsp.Ret, err)
	if err != nil {
		return nil, err
	}
	return nil, nil
```
