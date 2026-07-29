### Title
Shared `BalanceHandler` instance across recursive/nested precompile calls causes `prevEventsLen` corruption and native/EVM balance desync - (File: precompiles/common/balance_handler.go, precompiles/common/precompile.go)

### Summary
The Nibiru finding is a case of a shared mutable pointer (`StateDB`) on a long-lived keeper object being clobbered by concurrent/nested execution paths, producing non-deterministic state derived from stale or wrong pointer contents. Cosmos EVM has a structurally identical pattern in `precompiles/common/balance_handler.go`: `BalanceHandler.prevEventsLen` is a mutable field on an object that is used to bracket "before/after" bank event windows around a precompile call. If the same `BalanceHandler` instance is reused across nested/recursive precompile invocations within one EVM transaction (a contract that calls a precompile, which in turn triggers another precompile call, e.g. via `CallEVM`/`ApplyMessage`-driven ERC20↔bank flows or a contract that re-enters the precompile address), the inner call's `BeforeBalanceChange` overwrites `prevEventsLen`, causing the outer call's `AfterBalanceChange` to slice the wrong event window `events[bh.prevEventsLen:]` [1](#0-0) [2](#0-1) .

### Finding Description
`BalanceHandler` records the event-log length before a precompile executes native bank operations, then afterward replays only the events emitted since that mark to synchronize `StateDB` balances with the native bank keeper's Coin movements [3](#0-2) . It is created via `BalanceHandlerFactory.NewBalanceHandler()` and referenced by the base `Precompile` struct through a `BalanceHandlerFactory` field [4](#0-3) [5](#0-4) .

The repo's own integration test explicitly documents and reproduces this exact bug class: "the balance handler bug where recursive precompile calls share the same BalanceHandler instance, causing prevEventsLen to be overwritten. This leads to balance desync between native bank keeper and EVM stateDB." [6](#0-5) . The test exercises a debug precompile invoked recursively from a caller contract and checks resulting event counts [7](#0-6) , and the debug precompile's `Run` method itself calls `p.GetBalanceHandler().BeforeBalanceChange(ctx)` / `AfterBalanceChange(ctx, stateDB)` around each precompile invocation [8](#0-7) .

If `GetBalanceHandler()` returns (or caches) a single shared instance rather than a fresh one per top-level EVM tx/call frame, then any reentrant or nested precompile call (a contract calling precompile A, whose native logic triggers a callback into precompile B, or a contract that calls the same precompile address again before the outer call returns) will reset `prevEventsLen` mid-flight. This breaks the invariant that `AfterBalanceChange` only replays the bank events belonging to its own call: the outer call can either replay the inner call's bank events a second time (double-crediting/debiting `StateDB` balances vs. the ledger) or skip its own events entirely (leaving `StateDB` balances out of sync with the native bank keeper's committed balances) [9](#0-8) .

### Impact Explanation
`StateDB` balances feed directly into subsequent EVM balance reads/transfers within the same transaction and are committed via `StateDB.Commit()`/`commitWithCtx`, which persists `stateObject` balances back through the keeper interface as canonical EVM-visible balances [10](#0-9) . A desync between what the native bank keeper actually moved and what `StateDB.AddBalance`/`SubBalance` recorded is a direct accounting-corruption primitive: an attacker-controlled contract that nests precompile calls (e.g., calling a bank/staking/distribution precompile from inside another precompile-triggered callback, or simply re-entering the precompile address in the same call frame) can cause EVM-visible balances to diverge from the true native ledger — enabling double-counted credits (duplication of spendable value) or lost debits (effectively minting spendable EVM balance) without a corresponding native bank operation. This matches the "Critical unauthorized minting/duplication/accounting corruption of spendable user value" impact class.

### Likelihood Explanation
Triggering requires only an ordinary, unprivileged EVM transaction that deploys/calls a contract structured to invoke a stateful precompile in a nested/recursive manner within one call — no validator, relayer, or governance privilege is needed. The project's own test suite already constructs exactly this scenario (a caller contract invoking a precompile recursively) to probe the behavior, indicating the code path is reachable through the standard EVM message-call flow [11](#0-10) .

### Recommendation
Ensure a fresh `BalanceHandler` (with `prevEventsLen` scoped to each individual precompile call frame) is created per call rather than shared/reused across nested or recursive precompile invocations — e.g., have `GetBalanceHandler()` always call `BalanceHandlerFactory.NewBalanceHandler()` per `Run` invocation instead of returning a cached instance, or thread the handler through call-stack-local state (similar to snapshot/revision handling already used by `StateDB` journal) so that inner calls cannot clobber an outer call's event-window bookkeeping.

### Proof of Concept
The repository's existing integration test is itself a working PoC skeleton: it registers a debug precompile, deploys a caller contract, and invokes a `callback` that recursively re-enters the precompile, then asserts on the resulting event/precompile-call counts [12](#0-11) . To convert this into a balance-corruption PoC: replace the debug precompile's no-op body with real bank-moving logic (e.g., the bank/staking precompile pattern in `precompiles/common/balance_handler.go`'s `AfterBalanceChange`), have the caller contract recursively invoke that precompile at least twice within one transaction, and compare final `StateDB`-derived EVM balance (via `eth_getBalance`) against the native bank keeper's balance for the same address after the transaction commits — a divergence confirms the desync.

**Note on completeness:** I was not able to inspect the full implementation of `Precompile.GetBalanceHandler()` (only its declaration context in `precompiles/common/precompile.go` lines 1-34 was available in the index) to confirm definitively whether it currently caches a single `BalanceHandler` instance per `Precompile` object or always constructs a new one per call. The existence and description of the dedicated regression test strongly suggests this bug class exists or existed in this codebase, but confirming the current mitigation state requires reading the full `GetBalanceHandler` method, which the indexed context did not include. Starting a Devin session with full repository access would allow verifying the exact current state of that function.

### Citations

**File:** precompiles/common/balance_handler.go (L18-35)
```go
// BalanceHandlerFactory is a factory struct to create BalanceHandler instances.
type BalanceHandlerFactory struct {
	bankKeeper BankKeeper
}

// NewBalanceHandler creates a new BalanceHandler instance.
func NewBalanceHandlerFactory(bankKeeper BankKeeper) *BalanceHandlerFactory {
	return &BalanceHandlerFactory{
		bankKeeper: bankKeeper,
	}
}

func (bhf BalanceHandlerFactory) NewBalanceHandler() *BalanceHandler {
	return &BalanceHandler{
		bankKeeper:    bhf.bankKeeper,
		prevEventsLen: 0,
	}
}
```

**File:** precompiles/common/balance_handler.go (L43-136)
```go
// BeforeBalanceChange is called before any balance changes by precompile methods.
// It records the current number of events in the context to later process balance changes
// using the recorded events.
func (bh *BalanceHandler) BeforeBalanceChange(ctx sdk.Context) {
	bh.prevEventsLen = len(ctx.EventManager().Events())
}

// AfterBalanceChange processes the recorded events and updates the stateDB accordingly.
// It handles the bank events for coin spent and coin received, updating the balances
// of the spender and receiver addresses respectively.
//
// NOTES: Balance change events involving BlockedAddresses are bypassed.
// Native balances are handled separately to prevent cases where a bank coin transfer
// initiated by a precompile is unintentionally overwritten by balance changes from within a contract.

// Typically, accounts registered as BlockedAddresses in app.go—such as module accounts—are not expected to receive coins.
// However, in modules like precisebank, it is common to borrow and repay integer balances
// from the module account to support fractional balance handling.
//
// As a result, even if a module account is marked as a BlockedAddress, a keeper-level SendCoins operation
// can emit an x/bank event in which the module account appears as a spender or receiver.
// If such events are parsed and used to invoke StateDB.AddBalance or StateDB.SubBalance, authorization errors can occur.
//
// To prevent this, balance changes from events involving blocked addresses are not applied to the StateDB.
// Instead, the state changes resulting from the precompile call are applied directly via the MultiStore.
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

**File:** precompiles/common/precompile.go (L25-33)
```go
// Precompile is the base struct for precompiles that requires to access cosmos native storage.
type Precompile struct {
	KvGasConfig          storetypes.GasConfig
	TransientKVGasConfig storetypes.GasConfig
	ContractAddress      common.Address

	// BalanceHandlerFactory is optional
	BalanceHandlerFactory *BalanceHandlerFactory
}
```

**File:** evmd/tests/integration/balance_handler/balance_handler_test.go (L23-26)
```go
// BalanceHandlerTestSuite tests the balance handler bug where recursive precompile calls
// share the same BalanceHandler instance, causing prevEventsLen to be overwritten.
// This leads to balance desync between native bank keeper and EVM stateDB.
type BalanceHandlerTestSuite struct {
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

**File:** x/vm/statedb/statedb.go (L713-745)
```go
// commitWithCtx writes the dirty states to keeper
// using the provided context
func (s *StateDB) commitWithCtx(ctx sdk.Context) error {
	for _, addr := range s.journal.sortedDirties() {
		obj := s.stateObjects[addr]
		if obj.selfDestructed {
			if err := s.keeper.DeleteAccount(ctx, obj.Address()); err != nil {
				return errorsmod.Wrapf(err, "failed to delete account %s", obj.Address())
			}
		} else {
			if obj.code != nil && obj.dirtyCode {
				if len(obj.code) == 0 {
					s.keeper.DeleteCode(ctx, obj.CodeHash())
				} else {
					s.keeper.SetCode(ctx, obj.CodeHash(), obj.code)
				}
			}
			if err := s.keeper.SetAccount(ctx, obj.Address(), obj.account); err != nil {
				return errorsmod.Wrap(err, "failed to set account")
			}

			for _, key := range obj.dirtyStorage.SortedKeys() {
				valueBytes := obj.dirtyStorage[key].Bytes()
				if len(valueBytes) == 0 {
					s.keeper.DeleteState(ctx, obj.Address(), key)
				} else {
					s.keeper.SetState(ctx, obj.Address(), key, valueBytes)
				}
			}
		}
	}
	return nil
}
```
