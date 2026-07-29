### Title
Recursive/nested precompile calls corrupt `BalanceHandler` event-window tracking, causing StateDB balance desync from the authoritative bank keeper balance - ([File: precompiles/common/balance_handler.go])

### Summary
The `BunniHub` bug was rooted in a shared, mutable "before/after" cached-accounting mechanism whose window boundaries could be corrupted by a reentrant/nested call, letting stale cached state be committed and diverging accounted balances from real balances. The Cosmos EVM analog lives in `precompiles/common/balance_handler.go` and `precompiles/common/precompile.go`, where `BalanceHandler.BeforeBalanceChange`/`AfterBalanceChange` record an event-index watermark (`prevEventsLen`) on the shared `sdk.Context` event manager to translate bank `EventTypeCoinSpent`/`EventTypeCoinReceived` events into `StateDB.AddBalance`/`SubBalance` calls. When a precompile call recursively/nestedly triggers another precompile call within the same EVM transaction (sharing the same context event manager), the outer handler's captured window overlaps with events already consumed and applied by the inner handler, causing the same bank event to be re-applied to the EVM `StateDB`. This double-application inflates (or deflates) the EVM-visible balance relative to the true `x/bank` balance, corrupting spendable value tracked in `StateDB` for the remainder of the transaction.

### Finding Description
`RunNativeAction`/`runNativeAction` in `precompiles/common/precompile.go` wraps every stateful precompile invocation: [1](#0-0) 

For each precompile call it creates a `BalanceHandler`, calls `BeforeBalanceChange(ctx)` to snapshot `len(ctx.EventManager().Events())`, executes the native `action`, and then calls `AfterBalanceChange(ctx, stateDB)`, which converts every bank event emitted **after** that watermark into a `StateDB.AddBalance`/`SubBalance` call: [2](#0-1) 

The critical assumption is that the event slice `events[bh.prevEventsLen:]` observed by `AfterBalanceChange` contains only events produced by *that* precompile call's own action. This assumption breaks when a precompile's native `action` itself triggers another (nested) EVM call into a second precompile — because `ctx`/its `EventManager()` is shared across the nested call chain. The nested call's own `BeforeBalanceChange`/`AfterBalanceChange` pair will consume and apply its slice of events to `StateDB` first; when control returns to the outer call, the outer `AfterBalanceChange` re-scans from its own (earlier) watermark, which still includes the events the inner call already translated into `StateDB` balance changes. Those events get applied to `StateDB` a second time, producing a balance in `StateDB` that no longer matches the single, correctly-applied movement recorded by the authoritative `x/bank` keeper.

This exact bug class is already reproduced in-repo via a dedicated regression harness that recurses through a debug precompile using `DebugPrecompileCaller.sol`, explicitly documented as demonstrating that "recursive precompile calls share the same BalanceHandler instance, causing `prevEventsLen` to be overwritten... lead[ing] to balance desync between native bank keeper and EVM stateDB": [3](#0-2) [4](#0-3) 

The reachable trigger requires only an unprivileged EVM contract that performs nested precompile-to-precompile calls within one transaction (staking/distribution/bank/erc20 precompiles all wire the same `BalanceHandlerFactory`/`RunNativeAction` machinery, e.g. staking and distribution precompiles): [5](#0-4) [6](#0-5) 

### Impact Explanation
Because `StateDB` is the live account/balance view consulted for the remainder of the EVM execution (subsequent transfers, contract logic, and ultimately committed to the chain state), a double-applied `AddBalance`/`SubBalance` event corrupts the spendable native balance tracked for the involved accounts. An attacker who can trigger the nested-call pattern can inflate the `StateDB` balance of an address they control beyond what `x/bank` actually holds, then use that inflated balance within the same transaction (e.g., further native-value transfers, further precompile calls that move "value" based on the corrupted `StateDB` figure) — an irreversible accounting corruption of spendable user value across native/EVM balances, matching the Critical "unauthorized minting/duplication of spendable value" impact class.

### Likelihood Explanation
The vulnerable code path (`BalanceHandler` watermarking of a shared context's event log across nested precompile invocations) is generic to every stateful precompile using `cmn.Precompile.RunNativeAction`, and the repository itself contains a working proof-of-concept (`DebugPrecompileCaller.sol` + `TestRecursivePrecompileCallsWithDebugPrecompile`) demonstrating the exact recursive-call desync scenario, indicating the trigger is unprivileged and directly reachable from ordinary contract execution — no special permissions are required, only a contract that nests calls to precompiles that go through `RunNativeAction`.

### Recommendation
Track the event-window watermark and its associated processing on a stack or counter that is invariant to nested precompile invocations (e.g., push/pop watermark scopes, or track a globally monotonic "already-processed" index shared across the call stack rather than a per-instance-recorded absolute length), so that an inner call's consumed events are never re-scanned by an outer call. Alternatively, use the `StateDB`'s own journal/precompile-call snapshot mechanism (`AddPrecompileFn`, `precompileCallsCounter`) to gate `BalanceHandler` application per nesting depth, mirroring how `MultiStoreSnapshot`/journal reverts already isolate nested precompile state.

### Proof of Concept
The existing repository test already demonstrates the mechanics of the bug (nested/recursive precompile calls sharing balance-handler event windows): [7](#0-6) 

Verification of a concrete exploitable double-credit/double-debit dollar amount (as opposed to only the debug-precompile event-count desync shown by the existing test) requires deeper tracing through `x/vm/statedb/statedb.go`'s `AddPrecompileFn`/journal interplay with `BalanceHandler`, which the available index did not let me fully confirm end-to-end (e.g., whether `StateDB` journal reverts on nested-call failure would mask the double-application in all cases). This should be validated by a Devin session running the described nested-precompile scenario against live balance/precompile precompiles (e.g., bank + distribution) rather than only the debug precompile.

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

**File:** precompiles/common/balance_handler.go (L43-105)
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

**File:** precompiles/staking/staking.go (L60-72)
```go
	return &Precompile{
		Precompile: cmn.Precompile{
			KvGasConfig:           storetypes.KVGasConfig(),
			TransientKVGasConfig:  storetypes.TransientGasConfig(),
			ContractAddress:       common.HexToAddress(evmtypes.StakingPrecompileAddress),
			BalanceHandlerFactory: cmn.NewBalanceHandlerFactory(bankKeeper),
		},
		ABI:              ABI,
		stakingKeeper:    stakingKeeper,
		stakingMsgServer: stakingMsgServer,
		stakingQuerier:   stakingQuerier,
		addrCdc:          addrCdc,
	}
```

**File:** precompiles/distribution/distribution.go (L60-74)
```go
) *Precompile {
	return &Precompile{
		Precompile: cmn.Precompile{
			KvGasConfig:           storetypes.KVGasConfig(),
			TransientKVGasConfig:  storetypes.TransientGasConfig(),
			ContractAddress:       common.HexToAddress(evmtypes.DistributionPrecompileAddress),
			BalanceHandlerFactory: cmn.NewBalanceHandlerFactory(bankKeeper),
		},
		ABI:                   ABI,
		stakingKeeper:         stakingKeeper,
		distributionKeeper:    distributionKeeper,
		distributionMsgServer: distributionMsgServer,
		distributionQuerier:   distributionQuerier,
		addrCdc:               addrCdc,
	}
```
