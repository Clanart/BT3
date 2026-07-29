## Analog Identified: Reentrant precompile calls corrupt the shared `BalanceHandler` event window, causing StateDB balance desync

### Title
Reentrant precompile calls during a token-callback/hook can desync EVM StateDB balances from the bank keeper via the shared `BalanceHandler.prevEventsLen` window - (File: `precompiles/common/balance_handler.go`)

### Summary
The Gearbox ERC777 bug is about a malicious token invoking a callback during a state-changing operation, letting the attacker re-enter and manipulate contract state mid-flight. The Cosmos EVM analog is the `BalanceHandler` used by every stateful precompile (`erc20`, `distribution`, `staking`, `bank`, `gov`, `slashing`, `ics20`): a native ERC20/precompile `transfer` (or `distribution.claimRewards`, `staking.delegate`, etc.) can trigger an EVM-level callback into a user contract (e.g., an OpenZeppelin `_beforeTokenTransfer` hook wired to call back into a precompile, as the repo's own `ERC20RecursiveRevertingPrecompileCall.sol` test contract does). If that callback re-enters another precompile call, the nested precompile call captures the bank keeper's `ctx.EventManager()` event-count "window" (`prevEventsLen`) independently, and this window bookkeeping is not properly nested/restored across the recursive calls.

### Finding Description
`BalanceHandler.BeforeBalanceChange` records `len(ctx.EventManager().Events())` as `prevEventsLen`, and `AfterBalanceChange` reads `events[bh.prevEventsLen:]` to translate bank `coin_spent`/`coin_received`/precisebank fractional events into `stateDB.AddBalance`/`SubBalance` calls: [1](#0-0) 

Every stateful precompile call creates its own `BalanceHandler` instance per top-level `Run`/`RunNativeAction` invocation: [2](#0-1) 

However, when a precompile's own execution (e.g. `staking.delegate`, `distribution.claimRewards`, `erc20.transfer`) triggers an EVM call back into a Solidity contract (via `CallEVM`/`CallEVMWithData`) whose bytecode itself calls another precompile — exactly the pattern demonstrated by the repo's own `ERC20RecursiveRevertingPrecompileCall.sol` (`_beforeTokenTransfer` recursively invoking `distribution.claimRewards` via `try/catch`) and by the dedicated `debug` precompile test harness (`testutil/testdata/debug/debug.go`, `Call0`) — the nested precompile call's `BeforeBalanceChange`/`AfterBalanceChange` pair operates on the *same* `ctx.EventManager()` event log as the outer call, but the outer call's `prevEventsLen` was captured *before* the nested call ran. The repository's own test suite comment states this outright:

> "BalanceHandlerTestSuite tests the balance handler bug where recursive precompile calls share the same BalanceHandler instance, causing prevEventsLen to be overwritten. This leads to balance desync between native bank keeper and EVM stateDB." [3](#0-2) 

and:

> "TestRecursivePrecompileCallsWithDebugPrecompile demonstrates the balance handler bug by triggering recursive calls that share the same BalanceHandler instance." [4](#0-3) 

The debug precompile's `Run` method mirrors the production `runNativeAction` flow exactly (snapshot, `AddPrecompileFn`, `BeforeBalanceChange`, execute, `AfterBalanceChange`), and its `Call0` handler recurses back into the EVM via `CallEVMWithData`, letting a caller contract re-enter the precompile before the outer call's `AfterBalanceChange` runs: [5](#0-4) 

This is structurally identical to the ERC777 hazard: a token/precompile operation invokes external/user-controlled code mid-transfer, and that reentrant call interferes with bookkeeping (here, the event-index watermark) that the outer call relies on to finalize balances. Because bank `SendCoins`/`MintCoins`/`BurnCoins` events emitted by the *nested* precompile call get consumed (or mis-windowed) by the inner `BalanceHandler`, the outer call's `AfterBalanceChange` can either re-process already-applied events (double-crediting/debiting the EVM `StateDB`) or skip events that should have been applied (losing a balance update), producing a mismatch between the true bank-keeper balance and the EVM `StateDB` balance view that all subsequent EVM execution (transfers, `balanceOf`, gas payment, DeFi accounting) relies on.

Real-world path: the "recursive precompile calls" ICS20 test (`evmd/tests/ibc/ics20_recursive_precompile_calls_test.go`) demonstrates this exact class of interaction where a native ERC20's `_beforeTokenTransfer` hook recursively calls `distribution.claimRewards` and reverts, during an ICS20 transfer of that ERC20 — precisely paralleling "malicious token callback blocks/corrupts operation" from the ERC777 report, but generalized to any stateful precompile chain reachable from a user-deployed ERC20/contract hook (mint/burn/transfer hooks, `receive()`/`fallback()` on WERC20 deposits, etc.): [6](#0-5) [7](#0-6) 

### Impact Explanation
If the event-window desync causes the EVM `StateDB` to apply a bank-keeper balance delta twice (or apply a stale/incorrect delta), an attacker-controlled contract with a transfer/mint/burn hook or `receive()` fallback that recurses into a balance-affecting precompile (`erc20.transfer`, `distribution.claimRewards`, `staking.delegate/undelegate`, `bank` precompile sends, `werc20.deposit`) can cause the `StateDB` balance for an address to diverge from the actual bank-module ledger balance. Because `StateDB` balances are what subsequent EVM opcodes, `balanceOf` calls, and gas debits act on within the same transaction/block, this is a path toward unauthorized duplication or loss of spendable value — matching the "Critical unauthorized minting/duplication/irreversible accounting corruption" impact gate. It can also manifest as non-deterministic divergence between validators if event ordering or gas-dependent revert paths differ, risking AppHash divergence in edge cases.

### Likelihood Explanation
Any unprivileged user can deploy an ERC20 (or other) contract with a standard hook (`_beforeTokenTransfer`, `receive`, `fallback`) that calls back into a precompile, and can trigger it via ordinary `transfer`/`mint`/ICS20-transfer flows — no privileged access or validator collusion required. The repository's own test suite already exercises and names this exact mechanism ("balance handler bug", "recursive precompile calls share the same BalanceHandler instance"), strongly indicating the root cause is real and reachable via production precompile entrypoints, though the currently-committed test assertions (`evmd/tests/ibc/ics20_recursive_precompile_calls_test.go`, `balance_handler_test.go`) appear tuned to specific expected event counts/balances rather than proving the invariant is *always* preserved under arbitrary depths/orderings of reentrancy — leaving unexplored variants (deeper recursion, mixed precompiles, partial reverts) unverified as safe.

### Recommendation
- Make `BeforeBalanceChange`/`AfterBalanceChange` reentrancy-safe by tracking the event window as a stack (push/pop) rather than a single mutable `prevEventsLen` field per `BalanceHandler`, or by allocating a fresh, independent event-sub-range per nesting depth that composes correctly when nested calls complete.
- Alternatively, disallow/guard recursive precompile calls that share a `BalanceHandler` from being interleaved with EVM calls to arbitrary bytecode, or flush/apply the inner call's `AfterBalanceChange` fully before restoring the outer call's window baseline.
- Add fuzz/property tests asserting `stateDB` balances exactly match bank-keeper balances after arbitrarily deep/nested reentrant precompile calls (mint, burn, transfer, claimRewards, delegate) combined with reverts at each level, not just the specific depths currently exercised by `balance_handler_test.go` and `ics20_recursive_precompile_calls_test.go`.

### Proof of Concept
1. Deploy an ERC20 similar to `contracts/solidity/ERC20RecursiveRevertingPrecompileCall.sol`, whose `_beforeTokenTransfer` hook calls back into a balance-affecting precompile (e.g., `distribution.claimRewards`, or in the debug harness, `debugprecompile.Call0` which invokes `CallEVMWithData` back into the caller contract) before completing.
2. Trigger the outer precompile call (e.g., an ICS20 transfer of the ERC20, or a direct `transfer`) so that `BeforeBalanceChange` records `prevEventsLen` for the outer call, then the hook fires and a nested precompile call runs its own `BeforeBalanceChange`/`AfterBalanceChange` cycle against the same `ctx.EventManager()`.
3. Compare the resulting `stateDB.GetBalance()` values against the bank keeper's ledger balance for the involved accounts (as done in `evmd/tests/integration/balance_handler/balance_handler_test.go`, which explicitly counts `debug_precompile` events to detect the miscounting), demonstrating a mismatch/desync between the EVM-visible balance and the true bank balance.

### Citations

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

**File:** evmd/tests/integration/balance_handler/balance_handler_test.go (L23-25)
```go
// BalanceHandlerTestSuite tests the balance handler bug where recursive precompile calls
// share the same BalanceHandler instance, causing prevEventsLen to be overwritten.
// This leads to balance desync between native bank keeper and EVM stateDB.
```

**File:** evmd/tests/integration/balance_handler/balance_handler_test.go (L43-45)
```go
// TestRecursivePrecompileCallsWithDebugPrecompile demonstrates the balance handler bug
// by triggering recursive calls that share the same BalanceHandler instance.
func (s *BalanceHandlerTestSuite) TestRecursivePrecompileCallsWithDebugPrecompile() {
```

**File:** testutil/testdata/debug/debug.go (L47-144)
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

func (p Precompile) Execute(ctx sdk.Context, stateDB vm.StateDB, contract *vm.Contract, readOnly bool) ([]byte, error) {
	switch contract.Input[0] {
	case 0: // callback()
		return p.Call0(ctx, stateDB, contract, readOnly)
	case 1: // call1()
		return p.Call1(ctx, stateDB, contract, readOnly)
	}
	return nil, fmt.Errorf("unknown method: %x", contract.Input[0])
}

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

**File:** evmd/tests/ibc/ics20_recursive_precompile_calls_test.go (L52-55)
```go
// Test suite for ICS20 recursive precompile calls
// Tests the native balance handler bug where reverted distribution calls
// leave persistent bank events that are incorrectly aggregated

```

**File:** contracts/solidity/ERC20RecursiveRevertingPrecompileCall.sol (L124-155)
```text
    function _beforeTokenTransfer(
        address from,
        address to,
        uint256 amount
    ) internal virtual override(ERC20, ERC20Pausable) {
        // Emit an event to track if this hook is called
        emit BeforeTokenTransferHookCalled(from, to, amount);

        for(uint256 i=0; i < 5; i++) {
            try ERC20RecursiveRevertingPrecompileCall(address(this)).claimRewardsAndRevert() {

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

    function claimRewardsAndRevert() public {
        distribution.DISTRIBUTION_CONTRACT.claimRewards(address(this), 100);
        revert();
    }
```
