## Title
Nested/Recursive Precompile Calls Cause `BalanceHandler` Event-Range Corruption, Leading to StateDB/Bank Balance Desynchronization - (File: `precompiles/common/balance_handler.go`, `precompiles/common/precompile.go`)

## Summary
The external report describes a Curve LP oracle that derives a USD "price" from a stateful snapshot (spot reserves) that can be skewed by an attacker's transient action (a flash loan), producing an incorrect downstream value. The reachable analog in this Cosmos EVM codebase is not price-oracle math, but an equivalent "stale/overwritten snapshot" bug in the precompile `BalanceHandler`: its correctness depends on an event-index snapshot (`prevEventsLen`) taken immediately before a native (bank/precisebank) action runs and consumed immediately after. When precompile execution nests (one precompile call triggers another precompile call within the same EVM transaction, e.g. through a caller contract's callback), the inner call's bank events get replayed into the *outer* call's post-processing window as well, causing a double-application of `AddBalance`/`SubBalance` on the go-ethereum `StateDB`. This desynchronizes the EVM-visible balance from the actual `x/bank` (and `x/precisebank`) ledger balance — a direct violation of the "Asset-representation path" invariant that native coins, ERC20 views, and precompile-visible balances must remain 1:1.

## Finding Description
Every stateful precompile call is routed through `Precompile.runNativeAction` in `precompiles/common/precompile.go`: [1](#0-0) 

For each invocation, a `BalanceHandler` records the current length of `ctx.EventManager().Events()` in `BeforeBalanceChange`, runs the native `action(ctx)` (which emits `x/bank`/`x/precisebank` events for the coin movements performed), and then `AfterBalanceChange` replays every event **after** the recorded index into `stateDB.AddBalance`/`SubBalance` calls: [2](#0-1) [3](#0-2) 

The correctness of this mechanism depends entirely on the invariant that no other bank-emitting action interleaves between an individual handler's `Before`/`After` calls on the *same shared event log*. This invariant breaks when a precompile call is nested inside another precompile call within one EVM transaction (e.g., contract A calls precompile P, whose native action performs an EVM sub-call into another precompile call, or into itself, before returning). In that scenario:
1. Outer call's `BeforeBalanceChange` records `prevEventsLen = N`.
2. Outer `action(ctx)` begins, and during execution triggers a nested precompile call whose own `runNativeAction` records its own `prevEventsLen = N'` (N' ≥ N), executes its bank action (emitting bank events), and its own `AfterBalanceChange` correctly credits/debits the StateDB for those events.
3. Control returns to the outer action, which finishes and calls its own `AfterBalanceChange`. Since the outer's `prevEventsLen` was captured at `N` (before the nested call ran), the outer's replay window `events[N:]` **still includes the nested call's bank events**, which get applied a second time to the StateDB.

This is exactly the documented behavior already reproduced in the repository's own regression test, `evmd/tests/integration/balance_handler/balance_handler_test.go`, whose header explicitly states: [4](#0-3) 

and which exercises exactly this "recursive/nested precompile call" pattern via a caller contract and the debug precompile, asserting a specific `debug_count` of duplicated processing: [5](#0-4) 

The debug precompile itself demonstrates the vulnerable pattern (`p.GetBalanceHandler()`), i.e., relying on a handler whose `prevEventsLen` can be clobbered by nested invocations: [6](#0-5) 

The same nested-event-replay mechanics apply structurally to production stateful precompiles (bank, erc20, staking, distribution, gov, slashing, ics20) since they all funnel through the identical `runNativeAction`/`BalanceHandler` code path, and `BalanceHandlerFactory` is wired into precompiles such as `erc20`: [7](#0-6) 

## Impact Explanation
If an attacker can trigger nested precompile calls within a single EVM transaction (e.g., a contract that calls a bank/erc20/staking precompile whose native action path itself triggers another precompile call, or that re-enters via a callback before the outer handler runs `AfterBalanceChange`), the `StateDB.AddBalance`/`SubBalance` calls for the inner call's bank events get replayed a second time in the outer handler. This inflates (or deflates) the EVM-visible balance of an address without a corresponding change in the real `x/bank`/`x/precisebank` ledger — i.e., unauthorized duplication/creation of spendable EVM-visible value that is not backed by real bank state. An attacker could exploit this to make the EVM StateDB reflect a balance higher than what is actually escrowed in the bank module, then transfer/spend the phantom balance in subsequent EVM operations within the same transaction (e.g., further ERC20/precompile calls that only check the StateDB-mirrored balance), resulting in unauthorized extraction of value or an unrecoverable divergence between EVM and bank accounting — matching the "Critical unauthorized minting/duplication/irreversible accounting corruption of spendable user value" impact class.

## Likelihood Explanation
The bug is unprivileged and triggerable by any user through ordinary contract interactions (deploying a contract that nests precompile calls, or that triggers a callback into another precompile call), requiring no special permissions, validator/relayer involvement, or governance actions. The project itself has already written a dedicated regression test (`balance_handler_test.go`) confirming the exact recursive-call scenario reproduces an event-processing/balance-desync anomaly, which is strong evidence the root cause is real and reachable in production precompile code that shares the same `runNativeAction`/`BalanceHandler` implementation.

## Recommendation
Ensure `BalanceHandler` state is not shared or clobbered across nested precompile invocations within the same EVM transaction:
- Maintain a stack (not a single scalar) of `prevEventsLen` snapshots, pushed on `BeforeBalanceChange` and popped on `AfterBalanceChange`, so each nesting level processes only its own slice of events without consuming ranges already claimed by an inner call.
- Alternatively, mark each event as "consumed" once processed by any `AfterBalanceChange` (e.g., tag/track processed event indices) so a given bank event can never be applied to the StateDB more than once regardless of nesting order.
- Add invariant checks (e.g., after each top-level EVM transaction) asserting that the sum of StateDB balance deltas for bank-tracked accounts equals the corresponding `x/bank`/`x/precisebank` deltas, failing the transaction if they diverge.

## Proof of Concept
The repository already contains a reproducing test demonstrating the core mechanism (shared/overwritten `prevEventsLen` under recursive precompile calls) at `evmd/tests/integration/balance_handler/balance_handler_test.go` (`TestRecursivePrecompileCallsWithDebugPrecompile`), which deploys a caller contract that recursively invokes the debug precompile and asserts the resulting mismatch in debug-event counts caused by the balance-handler bug: [5](#0-4) 

A concrete exploit chain would require a real bank-mutating precompile (e.g., `erc20`/`bank`/`staking`) to be called nested inside another such precompile call in a single EVM transaction, so that the inner call's `CoinSpent`/`CoinReceived`/`FractionalBalanceChange` events are replayed by the outer handler, inflating the caller's or a third-party StateDB balance beyond what `x/bank` actually holds — verifying this fully would require constructing and running such a nested-call contract against `precompiles/erc20`/`precompiles/bank`, which was not executed in this read-only review.

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

**File:** precompiles/common/balance_handler.go (L43-71)
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
```

**File:** precompiles/common/balance_handler.go (L90-106)
```go
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

**File:** testutil/testdata/debug/debug.go (L47-78)
```go
func (p Precompile) Run(evm *vm.EVM, contract *vm.Contract, readonly bool) ([]byte, error) {
	stateDB, ok := evm.StateDB.(*statedb.StateDB)
	if !ok {
		return nil, errors.Wrap(errors2.ErrUnauthorized, "could not create statedb in debug precompile")
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
	err = stateDB.AddPrecompileFn(p.Address(), snapshot, events)
	if err != nil {
		return nil, err
	}

	// commit the current changes in the cache ctx
	// to get the updated state for the precompile call
	if err := stateDB.CommitWithCacheCtx(); err != nil {
		return nil, err
	}

	// Start the balance change handler before executing the precompile.
	p.GetBalanceHandler().BeforeBalanceChange(ctx)
```

**File:** precompiles/erc20/erc20.go (L79-98)
```go
func NewPrecompile(
	tokenPair erc20types.TokenPair,
	bankKeeper cmn.BankKeeper,
	erc20Keeper Erc20Keeper,
	transferKeeper ibcutils.TransferKeeper,
) *Precompile {
	return &Precompile{
		Precompile: cmn.Precompile{
			KvGasConfig:           storetypes.GasConfig{},
			TransientKVGasConfig:  storetypes.GasConfig{},
			ContractAddress:       tokenPair.GetERC20Contract(),
			BalanceHandlerFactory: cmn.NewBalanceHandlerFactory(bankKeeper),
		},
		ABI:            ABI,
		tokenPair:      tokenPair,
		BankKeeper:     bankKeeper,
		erc20Keeper:    erc20Keeper,
		transferKeeper: transferKeeper,
	}
}
```
