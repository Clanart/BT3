## Analog Identified: Double-processing of nested precompile bank events causes duplicate StateDB balance mutations

### Title
Recursive/nested precompile calls cause `BalanceHandler.AfterBalanceChange` to double-process bank events, leading to duplicated StateDB balance credits/debits - (File: `precompiles/common/balance_handler.go`, `precompiles/common/precompile.go`)

### Summary
The external report's root cause pattern is "a value is captured once and later compared/reused as if it reflected a distinct, freshly-read state, silently disabling the intended check/consistency guarantee." The Cosmos EVM analog is `BalanceHandler.prevEventsLen`, an event-index checkpoint captured at `BeforeBalanceChange` time and used at `AfterBalanceChange` time to slice "new" bank events out of the shared `ctx.EventManager()`. When a precompile call recursively/nestedly triggers another precompile call (e.g. staking/distribution/bank precompile actions that internally invoke `CallEVMWithData` or otherwise re-enter the EVM and hit another precompile), the outer and inner calls both read from the *same* underlying event manager, but each call only knows the event-count offset present when it started. This can cause the outer call's event slice to include events already consumed and applied to the StateDB by the inner call.

### Finding Description
`RunNativeAction`/`runNativeAction` in `precompiles/common/precompile.go` creates a fresh `BalanceHandler` per precompile invocation via `p.BalanceHandlerFactory.NewBalanceHandler()`: [1](#0-0) 

Each `BalanceHandler` independently snapshots `prevEventsLen = len(ctx.EventManager().Events())` in `BeforeBalanceChange`, then in `AfterBalanceChange` slices `events[bh.prevEventsLen:]` to detect "new" `CoinSpent`/`CoinReceived`/fractional-balance events and applies them to `stateDB` via `AddBalance`/`SubBalance`: [2](#0-1) [3](#0-2) 

The event manager instance is shared across nested calls (Cosmos SDK cache contexts normally retain the parent's `EventManager` unless explicitly swapped), so if precompile execution A calls into precompile execution B (nested), B's `BeforeBalanceChange`/`AfterBalanceChange` pair will consume and apply B's own bank events to the StateDB using B's own `prevEventsLen`. When A's own `AfterBalanceChange` subsequently runs, A's `prevEventsLen` was captured *before* B executed, so A's slice `events[A.prevEventsLen:]` still contains B's events — which have already been applied to the StateDB by B. This results in the same bank-event-driven balance delta being applied twice to `stateDB` (once by the inner handler, once again by the outer handler), producing a StateDB balance divergent from (and greater/less than) the actual bank-module balance.

The project's own test explicitly documents this exact class of bug for the recursive-precompile-call scenario: [4](#0-3) 

Notably, this handling is now the default `BalanceHandlerFactory` design used by all production precompiles (staking, distribution, gov, ics20, slashing, erc20): [5](#0-4) [6](#0-5) 

I was unable to fully verify a concrete, reachable end-to-end recursive call chain between two *production* precompiles (e.g., staking → bank, or distribution's reward-claim triggering a nested precompile call) within the scope of this investigation; the only concrete PoC available in the codebase uses a test-only "debug" precompile (`evmd/tests/testdata/debug/debug.go`) to trigger the recursive event-index collision. I also could not confirm whether any guard exists elsewhere (e.g., in the EVM call-depth/precompile dispatch layer) that prevents legitimate nested precompile-to-precompile calls from occurring in production flows, which is required to determine whether this is reachable by an unprivileged user without a custom malicious precompile.

### Impact Explanation
If reachable via a legitimate call path (e.g. a precompile method whose Cosmos-side logic triggers a bank transfer that is itself intercepted/re-entered as another precompile call, or via `CallEVMWithData`/`evmKeeper` internal re-entrancy used by precompiles like staking/distribution `...WithTransfer` helpers), this would let an unprivileged EVM caller cause the `StateDB` (and hence subsequent EVM-visible balances, and ultimately committed balances after `stateDB.Commit()`) to double-count a bank balance change. This is unauthorized duplication of spendable value — a Critical-class accounting corruption per the impact gate (native/EVM balance divergence from bank module truth), potentially allowing balance inflation for an attacker-controlled address if the duplicated delta is an `AddBalance` on the attacker's own address.

### Likelihood Explanation
Likelihood is **unconfirmed/Medium-Low** given available evidence: exploitation requires an unprivileged user to trigger a genuine nested precompile-to-precompile call using only production precompiles (staking, distribution, bank, gov, ics20, slashing, erc20). I found no confirmed production code path in which one production precompile method synchronously invokes another registered precompile through the EVM in a way that re-enters `runNativeAction` before the outer call's `AfterBalanceChange` executes. The only demonstrated reproduction in the repository uses a custom test precompile registered specifically to trigger recursion. Without confirming a real nested-precompile call path (or a user-deployed malicious contract forcing a callback into a precompile mid-precompile-execution), this should be treated as a plausible but not fully proven Critical finding in this repository's current production surface.

### Recommendation
- Make `BeforeBalanceChange`/`AfterBalanceChange` reentrancy-safe: rather than a single monotonically-increasing offset per handler instance, track and consume the exact event range with a stack/marker that accounts for nested handler consumption (e.g., have `AfterBalanceChange` advance a shared cursor stored on the context or via journal entries rather than a per-instance snapshot), or explicitly disallow/detect nested `RunNativeAction` invocations and process events using a single shared handler stack that skips ranges already consumed by an inner call.
- Add an integration test that exercises the recursive scenario using two distinct *production* precompiles (not just the debug precompile) to confirm/rule out reachability, and assert StateDB balances match bank-keeper balances after execution.

### Proof of Concept
The existing `TestRecursivePrecompileCallsWithDebugPrecompile` test demonstrates the mechanics of nested precompile calls sharing the same event manager and each running independent `BeforeBalanceChange`/`AfterBalanceChange` pairs: [7](#0-6) 
This confirms the recursive event-processing mechanism exists and is exercised by the test harness; extending this scenario with actual `CoinSpent`/`CoinReceived` bank events emitted by both the outer and inner precompile calls (rather than a custom no-op `debug_precompile` event) would be needed to directly observe the doubled `stateDB.AddBalance`/`SubBalance` calls and resulting balance divergence from the bank module — this final confirmation step was not completed due to tool-call limits.

### Citations

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

**File:** precompiles/common/balance_handler.go (L46-48)
```go
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

**File:** evmd/tests/integration/balance_handler/balance_handler_test.go (L23-25)
```go
// BalanceHandlerTestSuite tests the balance handler bug where recursive precompile calls
// share the same BalanceHandler instance, causing prevEventsLen to be overwritten.
// This leads to balance desync between native bank keeper and EVM stateDB.
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

**File:** precompiles/staking/staking.go (L60-66)
```go
	return &Precompile{
		Precompile: cmn.Precompile{
			KvGasConfig:           storetypes.KVGasConfig(),
			TransientKVGasConfig:  storetypes.TransientGasConfig(),
			ContractAddress:       common.HexToAddress(evmtypes.StakingPrecompileAddress),
			BalanceHandlerFactory: cmn.NewBalanceHandlerFactory(bankKeeper),
		},
```

**File:** precompiles/distribution/distribution.go (L61-67)
```go
	return &Precompile{
		Precompile: cmn.Precompile{
			KvGasConfig:           storetypes.KVGasConfig(),
			TransientKVGasConfig:  storetypes.TransientGasConfig(),
			ContractAddress:       common.HexToAddress(evmtypes.DistributionPrecompileAddress),
			BalanceHandlerFactory: cmn.NewBalanceHandlerFactory(bankKeeper),
		},
```
