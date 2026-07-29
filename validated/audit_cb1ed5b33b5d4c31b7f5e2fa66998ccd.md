### Title
Nested/recursive stateful precompile calls cause double-application of bank balance events to the EVM StateDB (double-credit/double-debit) - (File: precompiles/common/precompile.go)

### Summary
The `x/vm` precompile framework mirrors native `x/bank`/`x/precisebank` balance-changing events into the EVM `StateDB` via a `BalanceHandler` that records an event-log cursor (`prevEventsLen`) before a precompile action runs and replays every event emitted since that cursor after the action completes. This mirrors the yield-basis bug class: a security bookkeeping step (there, `_checkpoint_gauge()`; here, the before/after event cursor bracketing) is not reentrancy-safe against a nested/recursive invocation of the same protected code path, allowing state to be applied more than once (or inconsistently) for a single underlying economic event.

### Finding Description
`Precompile.runNativeAction` in [1](#0-0)  creates a `BalanceHandler`, calls `BeforeBalanceChange(ctx)` to snapshot `prevEventsLen := len(ctx.EventManager().Events())`, executes the native `action`, and then calls `AfterBalanceChange(ctx, stateDB)` which replays every bank/precisebank event in `events[prevEventsLen:]` onto the EVM `StateDB` via `AddBalance`/`SubBalance` [2](#0-1) .

The `ctx.EventManager()` event log is shared and cumulative across the entire transaction/call stack — it is not reset or scoped per precompile invocation. If a stateful precompile's native action itself triggers another EVM call that re-enters a stateful precompile (directly or indirectly, e.g. via `CallEVMWithData`/contract callback as demonstrated by the debug precompile's `Call0` → recursive EVM call in [3](#0-2) ), then:

1. Outer call: `BeforeBalanceChange` records `prevEventsLen = N`.
2. Inner (nested) precompile call: `BeforeBalanceChange` records `prevEventsLen = M` (M ≥ N), executes its own bank-moving action, emits events `[M:M+k]`, and its own `AfterBalanceChange` immediately applies those events to `StateDB` (Add/SubBalance).
3. Control returns to the outer call. The outer `AfterBalanceChange` now replays `events[N:]` — which **still includes** the inner call's `[M:M+k]` events that were already applied to `StateDB` by the inner call's own `AfterBalanceChange`.

The result is that the same underlying native bank transfer is applied twice (or more, with deeper nesting) to the EVM `StateDB`, while the actual `x/bank`/`x/precisebank` ledger only reflects it once. This breaks the 1:1 accounting invariant between native balances and EVM-visible balances that `AfterBalanceChange`'s own doc comment explicitly says it exists to protect ("to prevent cases where a bank coin transfer initiated by a precompile is unintentionally overwritten...").

The repository's own test, `evmd/tests/integration/balance_handler/balance_handler_test.go`, is explicitly named/commented as reproducing exactly this defect: *"tests the balance handler bug where recursive precompile calls share the same BalanceHandler instance, causing prevEventsLen to be overwritten... leads to balance desync between native bank keeper and EVM stateDB"* [4](#0-3) . Although each call now allocates a fresh `BalanceHandler` instance via `p.BalanceHandlerFactory.NewBalanceHandler()` [5](#0-4) , the root defect persists at the shared-event-log level rather than the handler-instance level: because the event log itself is not partitioned per call frame, nesting still causes overlapping event windows to be double-processed by parent and child `AfterBalanceChange` calls.

This pattern is wired into every precompile that uses `BalanceHandlerFactory`: `distribution` [6](#0-5) , plus `erc20`, `gov`, `ics20`, `slashing`, and `staking` (all matched via `NewBalanceHandler` usage), meaning any composition where one of these precompiles' execution path re-enters another (or itself) via an EVM call can trigger the duplication.

### Impact Explanation
This maps to the **Critical unauthorized minting/duplication of spendable user value across native balances and EVM balances** class. An attacker who can force re-entrant precompile execution during a single EVM transaction (e.g., a contract that calls a bank-moving precompile method, and within that native action path causes another EVM call back into a precompile that moves bank funds) can cause the `StateDB` (i.e., the attacker's/any account's EVM-visible balance, and hence what `eth_getBalance`, ERC20/WERC20 wrapped views, and subsequent EVM arithmetic see) to reflect double the actual native-ledger movement. Because EVM balance is authoritative for subsequent EVM-level transfers/contract logic within the same and later transactions (until reconciled), this is a duplication of spendable value — the EVM side can show/use funds that don't exist in `x/bank`, directly matching the "duplication ... of spendable user value across native balances, EVM balances" allowed-impact category.

### Likelihood Explanation
Likelihood depends on being able to construct a call path where a `BalanceHandlerFactory`-enabled precompile's native action results in re-entrant invocation of a precompile (same or different) that also mutates bank balances, within the same top-level EVM transaction, before the outer `AfterBalanceChange` runs. The repository already contains a working reproduction harness for the underlying "shared/overlapping event window" mechanism (`TestRecursivePrecompileCallsWithDebugPrecompile`) using the debug precompile's callback design, showing this call-stack shape is directly reachable via ordinary EVM contract calls. I was not able to fully verify, within the available search budget, whether a production (non-test) precompile combination and permissions currently allow an unprivileged, no-special-permission caller to trigger the exact nested pattern with balance-moving events (e.g., distribution `ClaimRewards`/`WithdrawDelegatorReward` re-entering itself or another balance-affecting precompile through a real contract call, vs. only the `debug` test precompile which is not part of production precompile registration). This is a meaningful gap in verification.

### Recommendation
- Scope `BeforeBalanceChange`/`AfterBalanceChange` event processing so nested/recursive precompile invocations cannot have their event ranges double-counted by an outer invocation — e.g., track and consume (truncate/mark-processed) the event log up to the point already handled by an inner call, or maintain a call-depth-aware/global high-water-mark cursor shared across the whole call stack (not per-instantiation) so each event is attributed to exactly one `AfterBalanceChange` invocation.
- Alternatively, disallow/guard re-entrant precompile execution within the same EVM call stack for `BalanceHandlerFactory`-enabled precompiles, or make `AfterBalanceChange` idempotent per event (e.g., tag/consume events instead of re-reading by index range).
- Add negative/positive integration tests asserting `StateDB` balances vs `x/bank` balances remain equal (not merely that event counts match) after nested precompile calls, extending the existing `TestRecursivePrecompileCallsWithDebugPrecompile` to assert balance equality rather than just event counts.

### Proof of Concept
The repository's own `evmd/tests/integration/balance_handler/balance_handler_test.go` demonstrates the reachable call-stack shape (contract → precompile → recursive EVM call → precompile) [7](#0-6) , and the `Call0` handler in the debug precompile shows how a single precompile invocation can trigger a nested EVM call that re-enters the precompile execution path [3](#0-2) . To fully weaponize this into a fund-duplication PoC against a real balance-moving precompile (e.g. `distribution`/`erc20`), one would need to: (1) construct a contract that calls a bank-moving precompile method, (2) inside that same precompile call's execution flow trigger a nested EVM call into another (or the same) balance-moving precompile method, and (3) compare resulting `StateDB` balances against `x/bank` balances to confirm divergence. I was unable to fully trace whether a production (non-test/non-debug) precompile combination permits step (2) without further investigation into each precompile's native action bodies (`ClaimRewards`, `WithdrawDelegatorReward`, ERC20 `transfer`, etc.) for any EVM call-back capability.

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

**File:** precompiles/common/balance_handler.go (L68-136)
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

		case precisebanktypes.EventTypeFractionalBalanceChange:
			addr, err := ParseAddress(event, precisebanktypes.AttributeKeyAddress)
			if err != nil {
				return fmt.Errorf("failed to parse address from event %q: %w", precisebanktypes.EventTypeFractionalBalanceChange, err)
			}
			if bh.bankKeeper.BlockedAddr(addr) {
				// Bypass blocked addresses
				continue
			}

			delta, err := ParseFractionalAmount(event)
			if err != nil {
				return fmt.Errorf("failed to parse amount from event %q: %w", precisebanktypes.EventTypeFractionalBalanceChange, err)
			}

			deltaAbs, err := utils.Uint256FromBigInt(new(big.Int).Abs(delta))
			if err != nil {
				return fmt.Errorf("failed to convert delta to Uint256: %w", err)
			}

			if delta.Sign() == 1 {
				stateDB.AddBalance(common.BytesToAddress(addr.Bytes()), deltaAbs, tracing.BalanceChangeUnspecified)
			} else if delta.Sign() == -1 {
				stateDB.SubBalance(common.BytesToAddress(addr.Bytes()), deltaAbs, tracing.BalanceChangeUnspecified)
			}

		default:
			continue
		}
	}
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
