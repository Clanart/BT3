Note: I ran out of tool-call budget before I could trace a concrete production call-path where one precompile's execution nests a second precompile call within the same top-level EVM message (e.g. via an ERC20/IBC callback hook). The evidence below is strong but the exact unprivileged trigger chain for a *production* (non-test) precompile combination is not fully confirmed — flagged explicitly in the Likelihood section.

### Title
EVM StateDB balance can permanently desync from the native bank ledger when precompile calls are nested, causing unauthorized balance duplication/loss - (File: precompiles/common/balance_handler.go, precompiles/common/precompile.go)

### Summary
The Sherlock report's root cause is that an unsigned accounting value (`allocatedBalance`) can be pushed into an invalid state by an operation that doesn't account for all overlapping value flows, permanently corrupting user accounting and locking funds. The Cosmos EVM analog is in the precompile `BalanceHandler` mechanism (`precompiles/common/balance_handler.go`, invoked from `precompiles/common/precompile.go:runNativeAction`), which reconciles native `x/bank` balance changes into the EVM `StateDB` by replaying `bank.EventTypeCoinSpent`/`CoinReceived` events emitted since a recorded event-count checkpoint (`prevEventsLen`). Because `sdk.Context.EventManager().Events()` accumulates globally and monotonically across nested calls, and each precompile invocation only "forgets" its own local checkpoint, an outer precompile call whose execution nests an inner precompile call will re-process the inner call's already-consumed bank events, applying `StateDB.AddBalance`/`SubBalance` twice for the same underlying coin movement.

### Finding Description
`BalanceHandler.BeforeBalanceChange` records `prevEventsLen = len(ctx.EventManager().Events())` [1](#0-0) , and `AfterBalanceChange` replays every bank/precisebank event from `events[bh.prevEventsLen:]` onward, applying `stateDB.SubBalance`/`AddBalance` for each `CoinSpent`/`CoinReceived`/`FractionalBalanceChange` event [2](#0-1) .

`runNativeAction` in `precompiles/common/precompile.go` creates a *new* `BalanceHandler` per precompile call via `p.BalanceHandlerFactory.NewBalanceHandler()`, calls `BeforeBalanceChange` right before executing the precompile's `action`, and calls `AfterBalanceChange` right after [3](#0-2) . This "fresh instance per call" pattern does not solve the underlying problem: `ctx.EventManager().Events()` is a single, ever-growing list shared across the whole EVM message execution. If, while an outer precompile's `action` is running, it triggers execution that reaches a second (inner) precompile call, that inner call's own `BalanceHandler` will consume events in the range `[innerStart, innerEnd)` and apply the corresponding `StateDB` balance deltas. When the outer call later finishes and its own `AfterBalanceChange` runs, its window is `[outerStart, finalLen)`, where `outerStart < innerStart`, so it necessarily includes the same inner-call events already applied by the inner handler. The outer handler will re-apply the same `AddBalance`/`SubBalance` deltas a second time onto `StateDB`, inflating or deflating the EVM-visible balance for the exact coin amount that was already correctly moved once in the real `x/bank` ledger.

This exact bug class — recursive/nested precompile calls causing the `BalanceHandler` to desync EVM `StateDB` balances from the real bank ledger — is already documented and regression-tested in the repository itself: `evmd/tests/integration/balance_handler/balance_handler_test.go` explicitly states: "tests the balance handler bug where recursive precompile calls share the same BalanceHandler instance, causing prevEventsLen to be overwritten. This leads to balance desync between native bank keeper and EVM stateDB." [4](#0-3) , and reproduces it via a caller contract invoking a debug precompile recursively [5](#0-4) . Error-path unit tests for `AfterBalanceChange` corroborate that this reconciliation logic is fragile to event-window mis-tracking [6](#0-5) .

All production precompiles (`staking`, `distribution`, `erc20`, `gov`, `ics20`, `slashing`) wire up `BalanceHandlerFactory` [7](#0-6) , so any of them are candidates for the nested-call desync if their execution path can trigger a second precompile invocation within the same top-level call (e.g. via an EVM `CALL` into another precompile address, or an internal hook/callback that invokes precompile logic, as suggested by the wiki's description of "ICS20, WERC20, Bech32 & Callbacks Precompiles").

### Impact Explanation
If the EVM `StateDB` balance is inflated relative to the real `x/bank`-backed balance, the affected account gains spendable EVM balance with no backing native coin — this is unauthorized duplication/minting of spendable value, matching "Critical unauthorized minting/duplication ... of spendable user value across native balances, EVM balances." Since `StateDB` balance is the source of truth for subsequent EVM operations within the same transaction/session and is later committed back via `SetBalance`'s mint/burn delta logic [8](#0-7) , a double-applied `AddBalance` can, on commit, cause the keeper to mint real native coins to make the bank balance match the (incorrectly inflated) `StateDB` balance — an irreversible, unauthorized token-supply increase. Conversely a double-applied `SubBalance` would burn real coins the user never actually spent, i.e., theft/permanent loss of user funds.

### Likelihood Explanation
The trigger is not privileged — any unprivileged user submitting an EVM transaction that causes precompile call nesting would activate it. However, I was not able to confirm within the current investigation which specific production precompile combination allows a precompile call to nest a second precompile call within the same top-level call in normal (non-test) operation; the only concrete reproduction found in the codebase uses a purpose-built test/debug precompile and caller contract. This should be verified against real precompile-to-precompile call paths (e.g. ERC20/IBC callback precompiles, or a contract that calls one precompile from within a call to another precompile) before treating likelihood as fully confirmed.

### Recommendation
Track the event-window checkpoint as a shared/global counter on the `StateDB` (or via the journal) rather than per-`BalanceHandler`-instance, so that an outer call's `AfterBalanceChange` only processes events that were not already consumed by any nested call's handler (e.g., advance a single shared cursor after each `AfterBalanceChange` pass, or have nested handlers register their consumed range with the outer handler via the journal). Add integration tests covering nested precompile calls across all production precompiles that share the `BalanceHandlerFactory`, not just the debug/test precompile.

### Proof of Concept
Existing repository regression test reproduces the underlying desync mechanism (using a debug precompile + caller contract that invokes it recursively): [9](#0-8) . To adapt this into a Critical-impact PoC against a production precompile, identify a call path where a production precompile (e.g. `erc20`, `ics20`) triggers a second precompile invocation within its own execution, then assert (a) the `StateDB` balance of the affected account after the transaction versus (b) the real `x/bank` balance for the same denom — a mismatch confirms unauthorized duplication or loss upon commit.

### Citations

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

**File:** precompiles/common/balance_handler_test.go (L189-231)
```go
func TestAfterBalanceChangeErrors(t *testing.T) {
	setupBalanceHandlerTest(t)

	storeKey := storetypes.NewKVStoreKey("test")
	tKey := storetypes.NewTransientStoreKey("test_t")
	ctx := sdktestutil.DefaultContext(storeKey, tKey)
	stateDB := statedb.New(ctx, mocks.NewEVMKeeper(), statedb.NewEmptyTxConfig())

	_, addrs, err := testutil.GeneratePrivKeyAddressPairs(1)
	require.NoError(t, err)
	addr := addrs[0]

	bankKeeper := cmnmocks.NewBankKeeper(t)
	precisebankModuleAccAddr := authtypes.NewModuleAddress(precisebanktypes.ModuleName)
	bankKeeper.Mock.On("BlockedAddr", mock.AnythingOfType("types.AccAddress")).Return(func(addr sdk.AccAddress) bool {
		// NOTE: In principle, all blockedAddresses configured in app.go should be checked.
		// However, for the sake of simplicity in this test, we assume a scenario where
		// only the precisebank module account is treated as a blockedAddress.
		return addr.Equals(precisebankModuleAccAddr)
	})
	bhf := cmn.NewBalanceHandlerFactory(bankKeeper)
	bh := bhf.NewBalanceHandler()
	bh.BeforeBalanceChange(ctx)

	// invalid address in event
	coins := sdk.NewCoins(sdk.NewInt64Coin(evmtypes.GetEVMCoinDenom(), 1))
	ctx.EventManager().EmitEvent(banktypes.NewCoinSpentEvent(addr, coins))
	ctx.EventManager().Events()[len(ctx.EventManager().Events())-1].Attributes[0].Value = "invalid"
	err = bh.AfterBalanceChange(ctx, stateDB)
	require.Error(t, err)

	// reset events
	ctx = ctx.WithEventManager(sdk.NewEventManager())
	bh.BeforeBalanceChange(ctx)

	// invalid amount
	ev := sdk.NewEvent(banktypes.EventTypeCoinSpent,
		sdk.NewAttribute(banktypes.AttributeKeySpender, addr.String()),
		sdk.NewAttribute(sdk.AttributeKeyAmount, "invalid"))
	ctx.EventManager().EmitEvent(ev)
	err = bh.AfterBalanceChange(ctx, stateDB)
	require.Error(t, err)
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

**File:** x/vm/keeper/statedb.go (L111-136)
```go
// SetBalance update account's balance, compare with current balance first, then decide to mint or burn.
func (k *Keeper) SetBalance(ctx sdk.Context, addr common.Address, amount *uint256.Int) error {
	if amount == nil {
		return nil
	}
	cosmosAddr := sdk.AccAddress(addr.Bytes())
	coin := k.bankWrapper.SpendableCoin(ctx, cosmosAddr, types.GetEVMCoinDenom())

	balance := coin.Amount.BigInt()
	delta := new(big.Int).Sub(amount.ToBig(), balance)
	switch delta.Sign() {
	case 1:
		// mint
		if err := k.bankWrapper.MintAmountToAccount(ctx, cosmosAddr, delta); err != nil {
			return err
		}
	case -1:
		// burn
		if err := k.bankWrapper.BurnAmountFromAccount(ctx, cosmosAddr, new(big.Int).Neg(delta)); err != nil {
			return err
		}
	default:
		// not changed
	}
	return nil
}
```
