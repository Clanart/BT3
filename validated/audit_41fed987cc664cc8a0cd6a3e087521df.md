### Title
Balance desynchronization between StateDB and bank keeper on nested/reentrant precompile calls due to unbounded event replay in `BalanceHandler` - (File: `precompiles/common/balance_handler.go`)

### Summary
The external report's core bug class is "missing checkpoint/snapshot boundary causes state calculated for one period to bleed into and corrupt another period's accounting." The Cosmos EVM analog is in the shared precompile execution path (`precompiles/common/precompile.go` `runNativeAction`, backed by `precompiles/common/balance_handler.go`), which is used by every stateful precompile (staking, distribution, gov, slashing, ics20, erc20). `BalanceHandler` records only a single scalar checkpoint (`prevEventsLen`) and, on completion, replays **every** bank event emitted since that checkpoint into the EVM `StateDB`. If a nested/recursive precompile invocation occurs inside the outer call's execution window (a contract re-entering a precompile before the outer call returns), the inner call's `AfterBalanceChange` already applies its slice of events to `StateDB`, but the outer call's later `AfterBalanceChange` re-scans from its own (earlier) `prevEventsLen` to the end of the event log — which now also includes the inner call's already-applied events — and re-applies them a second time.

### Finding Description
- `BalanceHandler.BeforeBalanceChange` just stores `len(ctx.EventManager().Events())` as a checkpoint: [1](#0-0) 
- `BalanceHandler.AfterBalanceChange` iterates `events[bh.prevEventsLen:]` — i.e., from the checkpoint to the *current* end of the (monotonically growing, shared) event log — and calls `stateDB.AddBalance` / `stateDB.SubBalance` for every `coin_spent`/`coin_received`/fractional-balance event found in that entire range: [2](#0-1) 
- The base `Precompile.runNativeAction`, shared by every stateful precompile, creates a *new* `BalanceHandler` per call from the factory and brackets the call's `action(ctx)` with `BeforeBalanceChange`/`AfterBalanceChange`: [3](#0-2) 
- Because the underlying `sdk.Context`'s `EventManager` is shared/threaded through nested calls (it is not scoped per call), if `action(ctx)` triggers a nested EVM call that re-enters another (or the same) stateful precompile, the inner call gets its own `BalanceHandler`, applies its own event slice to `StateDB`, and returns. Execution then resumes in the outer call, whose `AfterBalanceChange` re-scans from its own earlier `prevEventsLen` — a range that still contains the inner call's events — and re-applies the same `AddBalance`/`SubBalance` operations to `StateDB` a second time.
- The repository's own integration test explicitly documents and reproduces this exact class of bug (using a debug precompile harness that performs a recursive/self-reentrant EVM call): [4](#0-3) , with the reentrant call chain constructed via `p.evmKeeper.CallEVMWithData` inside the precompile's execution: [5](#0-4) 

This is the direct analog of the "missing snapshot" theme: the handler has no concept of a call-scoped upper bound (only a lower bound `prevEventsLen`), so nested-call state is not isolated/checkpointed away from the outer call's later reconciliation, corrupting the invariant that `StateDB` EVM-visible balances mirror the native bank ledger 1:1.

### Impact Explanation
If any production precompile flow allows a nested/reentrant precompile invocation within the same outer call (e.g., a contract-based delegator/recipient whose fallback logic triggers another qualifying precompile call before the outer call unwinds), the duplicated `AddBalance`/`SubBalance` replay causes the EVM-visible balance (`StateDB`) to diverge from the actual bank-keeper-settled balance. This is unauthorized duplication/corruption of spendable value in the EVM balance view without corresponding real coins — a Critical accounting-integrity violation matching the "unauthorized minting/duplication/irreversible accounting corruption of spendable user value across native balances, EVM balances" impact category.

### Likelihood Explanation
The vulnerable code path (`BalanceHandler`/`runNativeAction`) is the common, production-shared base used by every stateful precompile (staking, distribution, gov, slashing, ics20, erc20), confirmed by its use across `precompiles/staking/staking.go`, `precompiles/distribution/distribution.go`, `precompiles/erc20/erc20.go`, `precompiles/gov/gov.go`, `precompiles/ics20/ics20.go`, and `precompiles/slashing/slashing.go`. The concrete, repository-provided proof of concept uses a test-only debug precompile to trigger the nested-call condition deterministically; whether an unprivileged attacker can reach an equivalent nested-call condition through a *production* precompile method (i.e., one whose own execution issues a further EVM call that can re-enter a stateful precompile before the outer call's `AfterBalanceChange` runs) could not be confirmed from the code explored in this session — this is the key open question that determines whether this is exploitable in production or only via test/debug tooling.

### Recommendation
Bound `AfterBalanceChange`'s event scan to only the events produced during that specific call's own execution window (e.g., record both a start and end index/length at entry and exit of `action(ctx)`, before any nested call could append further events, or track a call-depth-aware/stack-based checkpoint rather than a single scalar), so nested calls' events cannot be replayed by an enclosing call. Alternatively, mark already-processed events (e.g. via a processed-index high-water-mark stored on the shared context) so each event is applied to `StateDB` exactly once regardless of call nesting depth.

### Proof of Concept
The existing repository test demonstrates the underlying event-replay flaw end-to-end: [6](#0-5)  deploys a caller contract that invokes the debug precompile's `callback`, which internally issues a further `CallEVMWithData` re-entrant call [5](#0-4) , and the test explicitly documents that this exercises "the balance handler bug where recursive precompile calls ... [cause] balance desync between native bank keeper and EVM stateDB." Reproducing this in a genuinely production precompile flow (rather than the debug harness) requires identifying a stateful precompile method whose native execution can itself trigger a nested EVM call back into a stateful precompile before its own `AfterBalanceChange` runs — this was not fully verified within the scope of this session and should be validated before treating this as confirmed production-exploitable.

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

**File:** testutil/testdata/debug/debug.go (L127-144)
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
}
```
