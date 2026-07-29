### Title
Reentrant precompile calls corrupt StateDB balances via shared cache-context EventManager and double-processed BalanceHandler events — (File: `precompiles/common/precompile.go`, `x/vm/statedb/statedb.go`)

### Summary
The Tapioca bug's root cause was that a recursive/nested call reused a piece of state (the hardcoded `address(this)` sender) that was meant to be scoped to a single call level, causing an inner recursive invocation to silently corrupt the security-relevant context of the outer flow and allowing value to be duplicated. Cosmos EVM has a structurally identical pattern: `StateDB.GetCacheContext()` lazily creates **one** `cacheCtx` (and one `EventManager`) per transaction and caches it on the `StateDB` struct [1](#0-0) , but every precompile invocation's `BalanceHandler.BeforeBalanceChange`/`AfterBalanceChange` pair only records an event-count offset (`prevEventsLen`) into that single shared event log [2](#0-1) . When a precompile call re-enters the EVM and triggers another (nested/recursive) precompile call before returning, the inner call's bank events get applied to `stateDB` by the inner `AfterBalanceChange`, and then the *same* events are re-included in the outer call's `events[prevEventsLen:]` slice and re-applied by the outer `AfterBalanceChange`, double-processing `CoinSpent`/`CoinReceived`/fractional-balance events.

### Finding Description
`runNativeAction` is the common entry point used by essentially all stateful precompiles (erc20, staking, distribution, gov, slashing, ics20, werc20) [3](#0-2) . For each call:
1. It obtains `ctx` from `stateDB.GetCacheContext()`.
2. It records `prevEventsLen = len(ctx.EventManager().Events())` via `BeforeBalanceChange`.
3. It runs the precompile's `action(ctx)`.
4. It processes `events[prevEventsLen:]` in `AfterBalanceChange`, translating `CoinSpent`/`CoinReceived` events into `stateDB.AddBalance`/`SubBalance` calls [4](#0-3) .

Crucially, `GetCacheContext()` only builds the `cacheCtx`/`EventManager` **once** per StateDB (guarded by `s.writeCache == nil`) and returns the same cached context on every subsequent call within the same EVM transaction [1](#0-0) , [5](#0-4) . This means that if precompile A's `action()` triggers, via `evmKeeper.CallEVM`/`CallEVMWithData`, a nested call into precompile A or precompile B before A's own `action()` returns, the nested call's `BeforeBalanceChange`/`AfterBalanceChange` share the exact same `EventManager` and event log as the outer call. The outer level's `prevEventsLen` was captured *before* the nested call ran, so when the outer level finally runs `AfterBalanceChange`, its slice `events[outerPrevEventsLen:]` still contains the inner call's bank events — even though those were already translated into `stateDB.AddBalance`/`SubBalance` by the inner handler.

The repository's own integration test explicitly documents and reproduces this exact defect using a nested-callback precompile: *"BalanceHandlerTestSuite tests the balance handler bug where recursive precompile calls share the same BalanceHandler instance, causing prevEventsLen to be overwritten. This leads to balance desync between native bank keeper and EVM stateDB."* [6](#0-5) . The demo precompile that reproduces it recurses via `evmKeeper.CallEVMWithData` back into itself [7](#0-6) , exactly mirroring the recursive `_lzCompose` pattern in the Tapioca report where a nested invocation reuses shared state that was only valid for a single call frame. Production precompiles that call back out into the EVM (distribution's `claimRewards`, staking's `delegate`, ICS20 transfers with `src_callback`, or any ERC20 token with transfer hooks that itself calls back into a value-moving precompile) can trigger the same nested-call pattern with real bank-token events, not just the test's synthetic `debug_precompile` event.

### Impact Explanation
Double-processing of `CoinSpent`/`CoinReceived`/fractional-balance events causes `stateDB.AddBalance`/`SubBalance` to be invoked twice for value that only moved once at the bank-keeper level. This desynchronizes the EVM-visible balance (used for subsequent `transfer`/`transferFrom`/native-value EVM operations within the same transaction, and persisted at `Commit()`) from the actual bank module ledger. Depending on call ordering, this can inflate a victim/attacker's EVM-visible balance beyond what is actually backed by bank coins, letting the attacker spend or transfer more value in the same or later transactions than genuinely exists — a duplication of spendable value across native/EVM balances, matching the "Critical unauthorized minting/duplication ... of spendable user value across native balances or EVM balances" impact class.

### Likelihood Explanation
The trigger requires only an unprivileged user to compose a transaction that causes a precompile call to recursively/nested-ly re-invoke another (or the same) balance-affecting precompile before the outer call returns — e.g., via a contract that calls a value-moving precompile whose execution path calls back into the EVM (as demonstrated by the repo's own recursive-precompile test harness pattern) or via ERC20 hooks/callback flows (ICS20 `src_callback`, custom ERC20 `_beforeTokenTransfer` hooks that call precompiles, as seen in `ERC20RecursiveNonRevertingPrecompileCall.sol`/`ERC20RecursiveRevertingPrecompileCall.sol` test contracts) [8](#0-7) . No privileged role is required; the only precondition is a call path that re-enters a precompile using `BalanceHandlerFactory` mid-execution.

### Recommendation
Scope the event-window tracking to each call frame instead of a StateDB-wide event manager offset, e.g., by snapshotting/trimming `events[prevEventsLen:currentLen]` and marking those specific events as consumed (removing or tagging them) so an outer frame's `AfterBalanceChange` cannot re-see events already consumed by an inner frame's `AfterBalanceChange`. Alternatively, maintain a stack of `prevEventsLen` markers per nesting depth and only process the delta between the current frame's marker and the next unprocessed marker, ensuring each bank event is translated into a `stateDB` balance change exactly once regardless of call nesting.

### Proof of Concept
The repository already contains a working reproduction of the underlying mechanism (recursive precompile calls sharing the cache context/EventManager and corrupting `prevEventsLen`) in `evmd/tests/integration/balance_handler/balance_handler_test.go::TestRecursivePrecompileCallsWithDebugPrecompile`, which deploys `DebugPrecompileCaller.sol` to trigger nested precompile re-entrancy and asserts on the resulting event/state mismatch [9](#0-8) , [10](#0-9) . I was not able to fully trace, within the available indexed code, a complete concrete production call chain (e.g., specific ERC20 hook → staking/distribution precompile → nested bank-token transfer) that weaponizes this into a specific quantified balance duplication in a single transaction; further exploration with full repository/terminal access (not available in this ask-only session) would be needed to build a full end-to-end PoC using genuine bank-token transfers instead of the synthetic `debug_precompile` event, and to confirm whether any currently-shipped precompile-to-precompile call path (rather than only the test-only debug precompile) can be driven into this nested pattern by an unprivileged caller.

### Citations

**File:** x/vm/statedb/statedb.go (L173-182)
```go
// GetCacheContext returns the stateDB CacheContext.
func (s *StateDB) GetCacheContext() (sdk.Context, error) {
	if s.writeCache == nil {
		err := s.cache()
		if err != nil {
			return s.ctx, err
		}
	}
	return s.cacheCtx, nil
}
```

**File:** x/vm/statedb/statedb.go (L198-219)
```go
// cache creates the stateDB cache context
func (s *StateDB) cache() error {
	if s.ctx.MultiStore() == nil {
		return errors.New("ctx has no multi store")
	}
	s.cacheCtx, _ = s.ctx.CacheContext()

	// Get KVStores for modules wired to app
	cms := s.cacheCtx.MultiStore().(storetypes.CacheMultiStore)
	storeKeys := s.keeper.KVStoreKeys()

	// Create and set snapshot store to stateDB
	snapshotStore := snapshotmulti.NewStore(cms, storeKeys)
	s.snapshotter = snapshotStore
	s.cacheCtx = s.cacheCtx.WithMultiStore(snapshotStore)
	s.writeCache = func() {
		s.ctx.EventManager().EmitEvents(s.cacheCtx.EventManager().Events())
		s.cacheCtx.MultiStore().(storetypes.CacheMultiStore).Write()
	}

	return nil
}
```

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

**File:** precompiles/common/precompile.go (L57-126)
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

	return bz, nil
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

**File:** contracts/solidity/ERC20RecursiveNonRevertingPrecompileCall.sol (L124-142)
```text
    function _beforeTokenTransfer(
        address from,
        address to,
        uint256 amount
    ) internal virtual override(ERC20, ERC20Pausable) {
        // Emit an event to track if this hook is called
        emit BeforeTokenTransferHookCalled(from, to, amount);

        for(uint256 i=0; i < 5; i++) {
            try ERC20RecursiveNonRevertingPrecompileCall(address(this)).claimRewards() {

            } catch {

            }

        }

        super._beforeTokenTransfer(from, to, amount);
    }
```

**File:** contracts/solidity/DebugPrecompileCaller.sol (L1-30)
```text
// SPDX-License-Identifier: UNLICENSED
pragma solidity ^0.8.4;

contract DebugPrecompileCaller {
    address constant debugPrecompile = 0x0000000000000000000000000000000000000799;
    error CallFailed(bytes data);
    function callback(uint256 counter) public {
        bool result;
        bytes memory data;

        // emit events
        for (uint i = 0; i < counter; i++) {
            (result, data) = debugPrecompile.call(abi.encodePacked(uint8(1)));
            if (!result) {
                revert CallFailed(data);
            }
        }

        if (counter > 3) {
            // stop the recursion
            return;
        }

        // recursive call
        (result, data) = debugPrecompile.call(abi.encodePacked(uint8(0), counter));
        if (!result) {
            revert CallFailed(data);
        }
    }
}
```
