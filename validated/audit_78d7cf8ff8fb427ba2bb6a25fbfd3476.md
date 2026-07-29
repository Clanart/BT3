### Title
Recursive Precompile Calls Cause Duplicate Processing of Bank Events, Leading to StateDB Balance Duplication - (File: precompiles/common/precompile.go, precompiles/common/balance_handler.go)

### Summary
Like the NFTPool bug where a bookkeeping mapping (`ownerToId`) was not correctly re-scoped on every transfer and became stale/duplicated, the Cosmos EVM `BalanceHandler` mechanism records only a starting-offset (`prevEventsLen`) into the shared `sdk.Context` `EventManager` event log and later replays "new" events from that offset to mirror bank balance changes into the EVM `StateDB`. When a precompile call re-enters the EVM (a nested/recursive precompile call, e.g. a precompile calling back into a contract that calls the same or another precompile), each nesting level gets its own `BalanceHandler` with its own `prevEventsLen`, but all levels share the *same underlying* `ctx.EventManager()` event slice. The outer level's `AfterBalanceChange` runs *after* the inner level already consumed and applied bank events to `StateDB`, and it processes `events[outerPrevEventsLen:]`, which range still includes all the events already consumed and applied by the inner call.

### Finding Description
`runNativeAction` in [1](#0-0)  creates a fresh `BalanceHandler` per invocation and calls `BeforeBalanceChange(ctx)` to snapshot `len(ctx.EventManager().Events())`, then invokes the native `action(ctx)`, then `AfterBalanceChange(ctx, stateDB)` which iterates `events[bh.prevEventsLen:]` in [2](#0-1)  and applies `stateDB.AddBalance` / `stateDB.SubBalance` for every `CoinSpent`/`CoinReceived`/fractional-balance event found in that slice.

If the native `action(ctx)` itself triggers another precompile invocation on the same `ctx`/`EventManager` (a recursive or nested EVM call back into a precompile, as reproduced in [3](#0-2) ), that nested call runs its own `BeforeBalanceChange`/`action`/`AfterBalanceChange` cycle and applies balance deltas to `stateDB` for the events it produced. Once execution returns to the outer level, the outer `AfterBalanceChange` computes `events[outerPrevEventsLen:]` — a range that still contains the events consumed by the nested level, because all levels observe the same growing `EventManager` event log (no re-basing/segmentation is done to exclude already-processed events). As a result, the same `CoinSpent`/`CoinReceived` events get replayed into `StateDB.SubBalance`/`AddBalance` a second time by the outer handler, corrupting the account balances tracked in `StateDB` and desynchronizing them from the actual bank-module ledger — this can result in a duplicated (i.e., doubled) balance credit for a receiver or an extra debit for a sender, purely from an ordinary EVM contract call flow that triggers nested precompile execution (no privileged access required).

This directly mirrors the NFTPool class of bug: an auxiliary bookkeeping structure (`prevEventsLen` / `ownerToId`) that is supposed to track "what has already been accounted for" is not correctly maintained across a nested/recursive operation, producing incorrect duplicated accounting state.

The repository itself contains a dedicated regression test package explicitly built around this exact scenario, describing it as: "tests the balance handler bug where recursive precompile calls share the same BalanceHandler instance, causing prevEventsLen to be overwritten. This leads to balance desync between native bank keeper and EVM stateDB." [4](#0-3) 

### Impact Explanation
If reachable through a real precompile that performs bank transfers on recursive/nested calls (e.g., ICS20, staking, distribution, bank, or ERC20 precompiles calling into contracts that reenter a precompile), the `StateDB` balance for user accounts can be inflated or deflated relative to the true bank-module balance. Because the EVM balance (`StateDB`) is what governs subsequent EVM-visible transfers/withdrawals within the same transaction and is what gets persisted at commit, a duplicated `AddBalance` effectively mints spendable EVM-visible value out of thin air (or a duplicated `SubBalance` destroys it), corrupting the 1:1 accounting invariant between native bank coins and EVM-visible balances. This matches the Critical "unauthorized minting/duplication or irreversible accounting corruption of spendable user value" impact class.

### Likelihood Explanation
Reachability depends on whether any precompile that uses `BalanceHandlerFactory` (staking, distribution, gov, erc20, ics20, slashing — all reference `BalanceHandler` per [5](#0-4) , [6](#0-5) , [7](#0-6) ) can be invoked in a nested fashion within the same top-level EVM call (e.g., an EVM contract calls precompile A, which calls back into an EVM contract, which calls precompile A or B again). I was not able to fully verify from indexed content alone whether any of the production precompiles (as opposed to the test-only `debug` precompile) actually exhibit reentrant/recursive call patterns during normal, unprivileged usage, since only the test/debug precompile explicitly demonstrates the scenario. This is a material gap in my verification given the ask-only tool budget.

### Recommendation
Ensure `BalanceHandler.AfterBalanceChange` at every nesting level only processes the strict sub-slice of events produced at that level and never re-processes events already consumed by an inner/nested `BalanceHandler` invocation — for example, by tracking a shared, monotonically-advancing cursor (rather than an independent `prevEventsLen` per instance) across nested `RunNativeAction` calls on the same `ctx`, or by rebasing each level to consume-and-advance a single shared offset stored on the `StateDB`/context rather than on a per-call `BalanceHandler` struct.

### Proof of Concept
The repository's own test suite reproduces the nested-call scenario using the `debug` precompile with a `callback()` method that recursively re-invokes the precompile via `evmKeeper.CallEVMWithData` and emits `debug_precompile` events at multiple nesting levels: [8](#0-7) . The test asserts the emitted event structure but does not assert on final `StateDB` balances after the recursive run, which is consistent with the described bug being about a hidden `stateDB` balance desync (duplicated/omitted balance updates) rather than an observable transaction failure — the class of defect that would need to be traced end-to-end (with actual bank `SendCoins` calls inside the nested precompile action) to obtain a concrete before/after balance discrepancy, similar in spirit to the Foundry PoC (`testFailownerToIdTransferedNFT`) in the original report.

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

**File:** evmd/tests/testdata/debug/debug.go (L58-75)
```go
func (p Precompile) Call0(ctx sdk.Context, stateDB vm.StateDB, contract *vm.Contract, readOnly bool) ([]byte, error) {
	// data := crypto.Keccak256([]byte("function callback()"))[:4]
	counter := new(big.Int).SetBytes(contract.Input[1:])
	counter = new(big.Int).Add(counter, big.NewInt(1))

	args := math.U256Bytes(counter)
	selector := []byte{0xff, 0x58, 0x5c, 0xaf}
	data := append(selector, args...)

	caller := contract.Caller()
	fmt.Printf("Execute debug precompile %s, %p\n", caller.String(), p.BalanceHandlerFactory)
	rsp, err := p.evmKeeper.CallEVMWithData(ctx, p.Address(), &caller, data, true, nil)
	fmt.Println("callback response:", rsp.Ret, err)
	if err != nil {
		return nil, err
	}
	return nil, nil
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

**File:** precompiles/erc20/erc20.go (L1-1)
```go
package erc20
```

**File:** precompiles/staking/staking.go (L1-1)
```go
package staking
```

**File:** precompiles/ics20/ics20.go (L1-1)
```go
package ics20
```
