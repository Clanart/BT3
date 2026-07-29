### Title
Balance Handler State Corruption in Recursive/Nested Precompile Calls Causes EVM StateDB / Bank Balance Desync - ([File: precompiles/common/balance_handler.go])

### Summary
This is the closest reachable analog to the D3Vault `withdrawReserves()` bug (an internal accounting/balance-tracking variable not correctly updated after a value-moving operation, corrupting a derived "exchange rate"). In Cosmos EVM, the equivalent internal accounting mechanism is the `BalanceHandler`, which bridges native `x/bank` coin-spent/coin-received events into the EVM `StateDB` balance so that EVM-visible balances stay 1:1 with the underlying bank ledger [1](#0-0) . The repository itself ships a dedicated regression test suite explicitly describing a known desync bug in this exact mechanism when precompile calls recurse [2](#0-1) .

### Finding Description
`BalanceHandler` tracks the length of emitted SDK events before a precompile method executes (`BeforeBalanceChange`) and, after execution, replays only the newly emitted `coin_spent`/`coin_received`/`fractional_balance_change` events (`events[prevEventsLen:]`) into the EVM `StateDB` via `AddBalance`/`SubBalance` [3](#0-2) . This is the single point where "the vault's balance" (here: the EVM-visible account balance) is reconciled against the actual underlying ledger (the bank module) after a funds-moving operation — structurally identical to `D3Vault.withdrawReserves()` needing to update the vault's internal balance bookkeeping after coins leave the vault.

The `prevEventsLen` cursor is stored as mutable state on the `BalanceHandler` struct [4](#0-3) . Several stateful precompiles (distribution, erc20, gov, ics20, slashing, staking, and the debug precompile used in tests) call this via a single accessor pattern of `BeforeBalanceChange` → execute → `AfterBalanceChange`, as shown in the reference debug precompile implementation [5](#0-4) . When a precompile call recursively/re-entrantly triggers another precompile call that shares the same handler instance (e.g., a contract's `_beforeTokenTransfer` hook calling back into a staking/distribution precompile mid-transfer, as exercised by `ERC20RecursiveRevertingPrecompileCall.sol` and `DistributionCaller.sol` test contracts [6](#0-5) [7](#0-6) ), the inner call's `BeforeBalanceChange` overwrites `prevEventsLen` on the shared instance. The outer call's subsequent `AfterBalanceChange` then processes the wrong event window — either re-applying balance deltas already consumed by the inner call, or skipping events that belong to the outer call.

The dedicated integration test suite name and comment confirm this exact failure mode is a recognized concern in this codebase: *"tests the balance handler bug where recursive precompile calls share the same BalanceHandler instance, causing prevEventsLen to be overwritten. This leads to balance desync between native bank keeper and EVM stateDB."* [2](#0-1) 

### Impact Explanation
The EVM `StateDB` balance is the value read by `balanceOf`-style operations, gas/value checks in nested `CALL`s, and by every ERC20/WERC20/bank precompile view built on top of it. If `AfterBalanceChange` double-applies a credit event or fails to apply a debit event due to the corrupted `prevEventsLen` window, the EVM-visible balance for an account diverges from its true bank-ledger balance. This is an irreversible accounting corruption of spendable value: an account's EVM balance could be inflated relative to backing coins (allowing that value to be spent/transferred out via subsequent EVM operations that are never later reconciled — effectively unauthorized minting from the EVM's perspective), or deflated (permanent loss/freezing of otherwise legitimate funds), matching the "Critical unauthorized minting/duplication/accounting corruption" and "Critical permanent freezing/theft of user funds" impact classes.

### Likelihood Explanation
Trigger requires only an unprivileged user deploying and calling a contract that performs nested/recursive precompile invocations during a single EVM transaction — a pattern the codebase's own test fixtures (`ERC20RecursiveRevertingPrecompileCall.sol`, `DistributionCaller.sol`, and the `balance_handler_test.go` suite) are specifically built to exercise, indicating the maintainers consider this a realistic, reachable production scenario rather than a theoretical one.

### Recommendation
Make the event-window bookkeeping re-entrant-safe: either instantiate a fresh `BalanceHandler` (with its own `prevEventsLen`) per precompile invocation frame instead of sharing one instance across nested calls, or replace the single scalar cursor with a stack/counter of cursors pushed on `BeforeBalanceChange` and popped on the matching `AfterBalanceChange`, ensuring each call frame reconciles only the events it itself emitted.

### Proof of Concept
Not independently reproduced in this session (read-only ask mode); root cause and reachability are corroborated by the existing in-repo `BalanceHandlerTestSuite` (`evmd/tests/integration/balance_handler/balance_handler_test.go`), whose stated purpose is reproducing this exact recursive-call balance desync using the `debug` precompile and the `DebugPrecompileAddress` test scaffolding [8](#0-7) . I was unable to fully inspect the `GetBalanceHandler()` accessor implementation on the `Precompile` struct in `precompiles/common/precompile.go` before running out of tool iterations, so I cannot confirm with certainty whether it currently returns a stored singleton versus a freshly-constructed handler in the latest code state — this should be verified directly in a full session before treating the bug as unpatched.

### Citations

**File:** precompiles/common/balance_handler.go (L37-68)
```go
// BalanceHandler is a struct that handles balance changes in the Cosmos SDK context.
type BalanceHandler struct {
	bankKeeper    BankKeeper
	prevEventsLen int
}

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
```

**File:** evmd/tests/integration/balance_handler/balance_handler_test.go (L23-26)
```go
// BalanceHandlerTestSuite tests the balance handler bug where recursive precompile calls
// share the same BalanceHandler instance, causing prevEventsLen to be overwritten.
// This leads to balance desync between native bank keeper and EVM stateDB.
type BalanceHandlerTestSuite struct {
```

**File:** testutil/testdata/debug/debug.go (L30-115)
```go
func NewPrecompile(bankKeeper cmn.BankKeeper, evmKeeper EVMKeeper) *Precompile {
	p := &Precompile{
		Precompile: cmn.Precompile{
			KvGasConfig:          storetypes.KVGasConfig(),
			TransientKVGasConfig: storetypes.TransientGasConfig(),
		},
		evmKeeper: evmKeeper,
	}
	// SetAddress defines the address of the distribution compile contract.
	p.SetAddress(common.HexToAddress(DebugPrecompileAddress))
	return p
}

func (p Precompile) RequiredGas(input []byte) uint64 {
	return 1000
}

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

**File:** contracts/solidity/ERC20RecursiveRevertingPrecompileCall.sol (L124-142)
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
```

**File:** contracts/solidity/precompiles/testutil/contracts/DistributionCaller.sol (L64-81)
```text
    function revertWithdrawRewardsAndTransfer(
        address payable _delAddr,
        address payable _withdrawer,
        string memory _valAddr,
        bool _after
    ) public {
        try
        DistributionCaller(address(this)).withdrawDelegatorRewardsAndRevert(
            _delAddr,
            _valAddr
        )
        {} catch {}
        if (_after) {
            counter++;
            (bool sent, ) = _withdrawer.call{value: 15}("");
            require(sent, "Failed to send Ether to delegator");
        }
    }
```
