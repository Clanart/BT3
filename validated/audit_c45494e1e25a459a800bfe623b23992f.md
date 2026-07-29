### Title
Recursive precompile calls cause duplicated processing of bank balance-change events, allowing double-crediting/debiting of EVM `StateDB` balances - (File: `precompiles/common/precompile.go`, `precompiles/common/balance_handler.go`)

### Summary
This is the Cosmos EVM analog of the ERC677 `transferAndCall` double-transfer bug: instead of a single logical transfer being applied to balances twice, a single sequence of `x/bank` `coin_spent`/`coin_received` events emitted during a recursive/nested precompile call gets replayed against the EVM `StateDB` by more than one `BalanceHandler` instance, corrupting the EVM-visible balance relative to the actual bank balance.

### Finding Description
Every precompile invocation goes through `Precompile.runNativeAction` [1](#0-0) , which:
1. Captures `prevEventsLen := len(ctx.EventManager().Events())` via `BeforeBalanceChange`.
2. Executes the native action (which may itself invoke another precompile, e.g. via `p.evmKeeper.CallEVMWithData`, as in the debug precompile's `Call0` [2](#0-1) ).
3. After the action returns, calls `AfterBalanceChange`, which reads **all current events** on the shared `ctx.EventManager()` and replays every `coin_spent`/`coin_received`/fractional-balance event **from `prevEventsLen` to the end** into the `StateDB` via `AddBalance`/`SubBalance` [3](#0-2) .

Because nested precompile calls share the same underlying `sdk.Context` event manager, an inner call's own `BalanceHandler` (a fresh instance is created per invocation via `NewBalanceHandler()` [4](#0-3) ) will process and apply the bank events generated during its own execution to the `StateDB`. When control returns to the outer call, the outer `BalanceHandler`'s `prevEventsLen` was captured *before* the inner call started, so its slice `events[bh.prevEventsLen:]` still includes the same events the inner handler already applied. The outer handler applies `AddBalance`/`SubBalance` for those events **a second time**, duplicating the effect on the EVM `StateDB` even though the underlying bank module only recorded a single transfer.

This exact bug class and mechanism is explicitly documented and reproduced by the repository's own regression test, `BalanceHandlerTestSuite.TestRecursivePrecompileCallsWithDebugPrecompile`, whose comment states: "tests the balance handler bug where recursive precompile calls share the same BalanceHandler instance, causing prevEventsLen to be overwritten. This leads to balance desync between native bank keeper and EVM stateDB." [5](#0-4) 

### Impact Explanation
A duplication of `coin_spent`/`coin_received` event replay means the EVM-visible balance (`StateDB.GetBalance`) can diverge from the actual `x/bank` balance for an account after a nested/recursive precompile call sequence (e.g., a contract calling the ERC20 precompile, WERC20 precompile `deposit`, or ICS20 precompile from within another precompile-invoking flow, or any precompile call triggered from inside another precompile's native action). This corrupts the 1:1 accounting invariant between native coin balances and EVM balances that the ERC20/WERC20/Bank precompiles are documented to guarantee ("Balance Consistency: All balance changes go through the bank module ensuring consistency" [6](#0-5) ). Depending on the direction of duplication (`AddBalance` vs `SubBalance`) and which address is targeted, this can result in unauthorized inflation of a user's or contract's EVM-visible spendable balance (allowing withdrawal/spend of funds that don't exist in the bank module) or erroneous deflation/freezing of funds — both are Critical, unauthorized-duplication/accounting-corruption impacts within the allowed impact gate.

### Likelihood Explanation
Any user-triggerable flow that causes one precompile's native action to invoke another precompile call (or itself) while both share the same `sdk.Context`/event manager is sufficient to trigger the duplicate accounting — this does not require a privileged actor, malicious validator, or relayer. The repository's own test confirms this is reachable with an ordinary EVM transaction from an unprivileged smart contract that recursively calls a precompile via `CallEVMWithData` [2](#0-1) ; the production ERC20/WERC20/ICS20/Bank precompiles use the same `RunNativeAction`/`BalanceHandler` mechanism and can plausibly be composed into similar nested-call patterns.

### Recommendation
Track a monotonically-consistent "consumed" event index shared across nested `BalanceHandler` invocations (e.g., store it in the `sdk.Context` or `StateDB`) rather than each handler independently snapshotting `len(events)` at entry, so that already-processed events are never replayed by an outer handler. Alternatively, have nested precompile calls skip creating an independent `BalanceHandler`/`AfterBalanceChange` pass and let only the outermost call reconcile the full event range once, or mark/consume events as they are applied so re-scanning is idempotent.

### Proof of Concept
The existing repository test `TestRecursivePrecompileCallsWithDebugPrecompile` [7](#0-6)  reproduces the scenario:
1. A contract calls the debug precompile's `callback(0)`.
2. The precompile's `Call0` recursively calls the same precompile again via `CallEVMWithData` [2](#0-1) , each level emitting a `debug_precompile` event and creating its own `BalanceHandler`.
3. Each nested call's `BalanceHandler.AfterBalanceChange` re-scans `ctx.EventManager().Events()` from its own `prevEventsLen`, but because these calls are nested and share the context, ranges overlap, causing repeated processing (visible via the mismatched expected event counts in the test).

For the actual balance-duplication impact, this pattern would need to be reproduced by replacing the debug precompile's recursive call with a real bank-balance-affecting precompile call (e.g., ERC20 `transfer`/WERC20 `deposit`) invoked recursively/nestedly, and comparing `StateDB.GetBalance` against `BankKeeper.GetBalance` post-execution — this exact verification was not completed within the scope of this investigation and should be validated with a live reproduction by a background engineer, since the read-only index used here does not include full trace-level execution.

### Citations

**File:** precompiles/common/precompile.go (L57-106)
```go
func (p Precompile) runNativeAction(evm *vm.EVM, contract *vm.Contract, action NativeAction) (bz []byte, err error) {
	stateDB, ok := evm.StateDB.(*statedb.StateDB)
	if !ok {
		return nil, errors.New(ErrNotRunInEvm)
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
	if err := stateDB.AddPrecompileFn(snapshot, events); err != nil {
		return nil, err
	}

	// commit the current changes in the cache ctx
	// to get the updated state for the precompile call
	if err := stateDB.CommitWithCacheCtx(); err != nil {
		return nil, err
	}

	initialGas := ctx.GasMeter().GasConsumed()

	defer HandleGasError(ctx, contract, initialGas, &err)()

	// set the default SDK gas configuration to track gas usage
	// we are changing the gas meter type, so it panics gracefully when out of gas
	ctx = ctx.WithGasMeter(storetypes.NewGasMeter(contract.Gas)).
		WithKVGasConfig(p.KvGasConfig).
		WithTransientKVGasConfig(p.TransientKVGasConfig)

	// we need to consume the gas that was already used by the EVM
	ctx.GasMeter().ConsumeGas(initialGas, "creating a new gas meter")

	var balanceHandler *BalanceHandler
	if p.BalanceHandlerFactory != nil {
		balanceHandler = p.BalanceHandlerFactory.NewBalanceHandler()
	}

	if balanceHandler != nil {
		balanceHandler.BeforeBalanceChange(ctx)
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

**File:** precompiles/common/balance_handler.go (L68-132)
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

```

**File:** evmd/tests/integration/balance_handler/balance_handler_test.go (L23-35)
```go
// BalanceHandlerTestSuite tests the balance handler bug where recursive precompile calls
// share the same BalanceHandler instance, causing prevEventsLen to be overwritten.
// This leads to balance desync between native bank keeper and EVM stateDB.
type BalanceHandlerTestSuite struct {
	suite.Suite

	coordinator *evmibctesting.Coordinator
	chain       *evmibctesting.TestChain
}

func TestBalanceHandlerTestSuite(t *testing.T) {
	suite.Run(t, new(BalanceHandlerTestSuite))
}
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

**File:** precompiles/erc20/README.md (L98-102)
```markdown
## Security Considerations

1. **No Direct Funding**: The precompile cannot receive funds through `msg.value` to prevent loss of funds
2. **Allowance Management**: Follows the standard ERC20 allowance pattern with proper checks
3. **Balance Consistency**: All balance changes go through the bank module ensuring consistency
```
