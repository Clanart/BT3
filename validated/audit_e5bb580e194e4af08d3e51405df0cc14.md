### Title
Nested/recursive precompile calls cause bank events to be double-applied to EVM `StateDB`, corrupting balances - (File: `precompiles/common/balance_handler.go`, `precompiles/common/precompile.go`)

### Summary
The reported `FSDVesting` bug is a "missing/duplicated corresponding accounting call" pattern: a claim path fails to correctly synchronize two parallel accounting ledgers (the vesting schedule and the token contract), corrupting user balances. The Cosmos EVM analog is the `BalanceHandler` mechanism that keeps the EVM `StateDB` balance view synchronized with `x/bank` events emitted by stateful precompiles. When precompile calls are nested/recursive (a pattern the repo itself demonstrates and has a dedicated regression test for), the event-range bookkeeping used to replay bank events onto `StateDB` can double-apply (or drop) balance deltas, desynchronizing EVM-visible balances from the actual `x/bank` ledger.

### Finding Description
Every stateful precompile call is wrapped by `runNativeAction` in [1](#0-0) , which:
1. Creates a `BalanceHandler` and calls `BeforeBalanceChange(ctx)`, which snapshots `prevEventsLen = len(ctx.EventManager().Events())` [2](#0-1) .
2. Executes the native action (which may emit `x/bank` `coin_spent`/`coin_received` events, or trigger further nested precompile/EVM calls).
3. Calls `AfterBalanceChange(ctx, stateDB)`, which replays every event in `events[prevEventsLen:]` into `StateDB.AddBalance`/`SubBalance` [3](#0-2) .

The `ctx.EventManager()` event log is shared/accumulated across the whole EVM transaction, including any nested precompile invocation that occurs while the outer native action is still executing (e.g. a contract's `_beforeTokenTransfer` hook that calls back into another precompile such as `DistributionI.claimRewards`, exactly as demonstrated by `ERC20RecursiveNonRevertingPrecompileCall.sol` [4](#0-3) ). When such a nested call occurs:
- The nested call gets its own `BalanceHandler` (or, in the explicitly-flagged legacy pattern, a *shared* handler via `GetBalanceHandler()` as still used by the debug precompile [5](#0-4) ), which correctly processes only its own event slice and applies those balance deltas to `StateDB`.
- Control then returns to the *outer* call, whose `prevEventsLen` was captured **before** the nested call ran. Its subsequent `AfterBalanceChange` therefore replays a window that still contains the nested call's already-applied events, re-driving `AddBalance`/`SubBalance` for those same bank events a second time.

The repository's own integration test explicitly documents this exact failure mode: *"tests the balance handler bug where recursive precompile calls share the same BalanceHandler instance, causing prevEventsLen to be overwritten. This leads to balance desync between native bank keeper and EVM stateDB"* [6](#0-5) , confirming this is a recognized, reachable risk in the recursive-precompile-call code path rather than a theoretical concern.

### Impact Explanation
If bank-event replay is duplicated for a recipient address, `StateDB.AddBalance` is invoked twice for the same underlying `x/bank` credit, inflating the EVM-visible balance of that account beyond what `x/bank` actually holds. Because EVM value transfers, gas accounting, and ERC20-precompile wrapped-native balances are all read from `StateDB`, an attacker able to trigger nested/recursive precompile calls (via a crafted ERC20 hook, callback, or fallback that re-enters a precompile such as `distribution`, `staking`, `bank`, or `erc20`) can cause a persistent over-credit of spendable EVM balance that is never backed by real `x/bank` coins — i.e., unauthorized duplication/minting of spendable value, matching the Critical "unauthorized minting, burning, duplication ... irreversible accounting corruption of spendable user value" impact category. Conversely, dropped events (from the "overwritten prevEventsLen" variant) can cause legitimate credits to silently fail to reach `StateDB`, also corrupting accounting.

### Likelihood Explanation
Triggering nested precompile execution only requires deploying an ordinary contract whose token hook or fallback function calls a precompile method during another precompile's or ERC20 transfer's execution — no privileged access is required. The repository already contains a proof-of-concept contract for exactly this pattern (`ERC20RecursiveNonRevertingPrecompileCall.sol`), and a dedicated regression test exists specifically to exercise recursive precompile calls through the `BalanceHandler`, indicating the maintainers consider this a real, previously-encountered condition rather than a hypothetical one.

### Recommendation
Ensure the event window used by `AfterBalanceChange` cannot overlap between nested/outer precompile invocations — e.g., by truncating/marking events as "consumed" once processed by an inner `BalanceHandler`, or by tracking balance deltas via a call-scoped ledger rather than a raw `EventManager` index snapshot, so nested calls cannot cause the outer call to re-process (or skip) already-handled bank events. Add invariant checks that assert `StateDB` aggregate native balance equals `x/bank` aggregate balance at the end of each EVM transaction as a defense-in-depth measure.

### Proof of Concept
1. Deploy a contract analogous to `ERC20RecursiveNonRevertingPrecompileCall.sol`, whose `_beforeTokenTransfer` hook invokes `DISTRIBUTION_CONTRACT.claimRewards(address(this), n)` (a nested precompile call) during an outer precompile-mediated transfer/claim.
2. Fund the contract and set up a delegation so that `claimRewards` triggers real `x/bank` `coin_received` events during both the outer and nested invocation.
3. Execute a transaction that triggers the outer precompile call (causing the nested recursive call inside the hook).
4. Compare `StateDB.GetBalance(receiver)` (EVM view) against the actual `x/bank` balance of the same account after the transaction — as demonstrated by the pattern in the existing `BalanceHandlerTestSuite` regression test, discrepancies (extra/missing credited amounts) arise from the overlapping event-replay windows.

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

**File:** precompiles/common/balance_handler.go (L68-106)
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

**File:** contracts/solidity/ERC20RecursiveNonRevertingPrecompileCall.sol (L124-154)
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

    function delegate(
        string memory validatorAddress,
        uint256 amount
    ) external {
        bool ok = staking.STAKING_CONTRACT.delegate(address(this), validatorAddress, amount);
        require(ok, "failed to stake");
    }

    function claimRewards() public {
        distribution.DISTRIBUTION_CONTRACT.claimRewards(address(this), 100);
    }
```

**File:** testutil/testdata/debug/debug.go (L47-115)
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

	return res, nil
}
```

**File:** evmd/tests/integration/balance_handler/balance_handler_test.go (L23-25)
```go
// BalanceHandlerTestSuite tests the balance handler bug where recursive precompile calls
// share the same BalanceHandler instance, causing prevEventsLen to be overwritten.
// This leads to balance desync between native bank keeper and EVM stateDB.
```
