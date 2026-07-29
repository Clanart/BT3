## Analog Found: WERC20 precompile permanently locks native tokens sent via non-deposit method calls

### Title
Native tokens sent to the WERC20 precompile via non-deposit/non-fallback calls are permanently locked — (File: `precompiles/werc20/werc20.go`)

### Summary
The BathBuddy `receive()` bug is a contract that accepts ETH with no way to release it. The Cosmos EVM analog is the WERC20 precompile (`precompiles/werc20/werc20.go`), which was explicitly designed so that *only* the `deposit()`/`fallback`/`receive` code paths return attached native value to the caller. Any other ABI method inherited from the underlying ERC20 precompile (`transfer`, `approve`, `transferFrom`, etc.) is routed to `HandleMethod` in `Execute()`, which contains no logic to return or account for `contract.Value()`. Because precompiles have no EVM bytecode enforcing Solidity's compiler-generated "non-payable" `CALLVALUE` check, an attacker can invoke these non-payable selectors with attached native value via a raw low-level call, and that value is transferred to the precompile's address as part of standard EVM call-value semantics without ever being credited back to the sender or exposed through any sweep/withdraw function.

### Finding Description
`Precompile.Execute` dispatches based on selector/method type: [1](#0-0) 

Only three paths (`Fallback`, `Receive`, `deposit`) call `p.Deposit`, which explicitly sends the attached coins back to the caller via `BankKeeper.SendCoins`: [2](#0-1) 

`Withdraw` is documented and implemented as a pure no-op — it never moves any native balance, because the design assumes native value is *never* actually held by the precompile: [3](#0-2) [4](#0-3) 

The default case in `Execute` falls through to `p.HandleMethod(ctx, contract, stateDB, method, args)` — the inherited ERC20 precompile logic for `transfer`/`approve`/`transferFrom`/etc. — with no handling of `contract.Value()` at all. The README's own security claim ("No Lock-up: Native tokens are never locked in the precompile") is predicated entirely on the invariant that value only ever reaches the precompile through the `deposit`/`fallback`/`receive` paths. That invariant is not enforced at the EVM call layer: Solidity's compiler injects the `CALLVALUE` non-payable guard only into bytecode it generates for regular contracts. A precompile has no bytecode, so nothing prevents a caller from crafting a raw `call{value: X}(abi.encodeWithSelector(IERC20.transfer.selector, ...))` targeting the WERC20 precompile address. The `SetupABI` dispatcher in `precompiles/common/precompile.go` will happily match the 4-byte selector to the `transfer` method regardless of `contract.Value()`, routing execution to `HandleMethod` — the path that never returns or accounts for attached value: [5](#0-4) 

I was unable to fully verify, within the available tool budget, the exact mechanics of `precompiles/common/balance_handler.go` (the `BalanceHandlerFactory`/`BalanceHandler` referenced in `runNativeAction`), which reconciles EVM-side balance changes with the bank keeper after a precompile call. It's possible this component is meant to universally sweep/settle any native value sent to a precompile address; if so, this specific path may already be closed. This detail should be confirmed by reading `precompiles/common/balance_handler.go` in full before treating this as confirmed-exploitable.

### Impact Explanation
If the `BalanceHandler` does not universally reconcile/reject unaccounted value sent to precompile addresses outside the `deposit` path, then any native token value attached to a call targeting a non-payable WERC20 selector becomes permanently and irrecoverably locked: it is credited to the precompile's address in the bank module (mirrored in the EVM's `statedb`), and there is no code path in `werc20.go` (or in the underlying `erc20.Precompile.HandleMethod`) that can move it back out. This matches the required Critical impact category of "permanent freezing, locking, theft, or unauthorized extraction of user funds ... or token-pair-backed balances," since it is an ordinary, unprivileged, low-level contract call.

### Likelihood Explanation
Likelihood depends entirely on whether `BalanceHandler.AfterBalanceChange` (in `precompiles/common/balance_handler.go`) generically reverts/rejects/reroutes value sent to precompiles whose invoked method does not explicitly account for it. That file was not fully reviewed here due to tool-call budget constraints, so likelihood cannot be conclusively assessed — it ranges from "not exploitable" (if `BalanceHandler` closes the gap generically for all precompiles) to "trivially exploitable by any unprivileged EOA via a single low-level call" (if it does not).

### Recommendation
1. Read `precompiles/common/balance_handler.go` fully to confirm whether `BalanceHandler.BeforeBalanceChange`/`AfterBalanceChange` generically prevents/reconciles unaccounted `contract.Value()` for any precompile method that isn't designed to receive value.
2. If no such generic protection exists, add an explicit guard in `Precompile.Execute` (`precompiles/werc20/werc20.go`) that rejects (reverts) any call with `contract.Value() > 0` routed to a method other than `deposit`/`fallback`/`receive`, mirroring the non-payable enforcement that Solidity-compiled contracts get for free.

### Proof of Concept
Conceptual PoC (pending confirmation of `BalanceHandler` behavior):
```solidity
// Low-level call bypasses Solidity's payable/non-payable compile-time check
(bool ok, ) = WERC20_ADDRESS.call{value: 1 ether}(
    abi.encodeWithSelector(IERC20.transfer.selector, attacker, 0)
);
// If ok == true and no revert occurs, the attached 1 ether is
// consumed by the EVM's call-value transfer to the precompile address
// but Execute() routes to HandleMethod (ERC20 transfer logic), which
// never returns or credits the value anywhere — it is stuck permanently,
// with Withdraw() being a documented no-op that cannot recover it.
```

### Citations

**File:** precompiles/werc20/werc20.go (L103-124)
```go
func (p Precompile) Execute(ctx sdk.Context, stateDB vm.StateDB, contract *vm.Contract, readOnly bool) ([]byte, error) {
	method, args, err := cmn.SetupABI(p.ABI, contract, readOnly, p.IsTransaction)
	if err != nil {
		return nil, err
	}

	var bz []byte

	switch {
	case method.Type == abi.Fallback,
		method.Type == abi.Receive,
		method.Name == DepositMethod:
		bz, err = p.Deposit(ctx, contract, stateDB)
	case method.Name == WithdrawMethod:
		bz, err = p.Withdraw(ctx, contract, stateDB, args)
	default:
		// ERC20 transactions and queries
		bz, err = p.HandleMethod(ctx, contract, stateDB, method, args)
	}

	return bz, err
}
```

**File:** precompiles/werc20/tx.go (L26-57)
```go
// Deposit handles the payable deposit function. It retrieves the deposited amount
// and sends it back to the sender using the bank keeper.
func (p Precompile) Deposit(
	ctx sdk.Context,
	contract *vm.Contract,
	stateDB vm.StateDB,
) ([]byte, error) {
	caller := contract.Caller()
	depositedAmount := contract.Value()

	callerAccAddress := sdk.AccAddress(caller.Bytes())
	precompileAccAddr := sdk.AccAddress(p.Address().Bytes())

	// Send the coins back to the sender
	if err := p.BankKeeper.SendCoins(
		ctx,
		precompileAccAddr,
		callerAccAddress,
		sdk.NewCoins(sdk.Coin{
			Denom:  evmtypes.GetEVMCoinExtendedDenom(),
			Amount: math.NewIntFromBigInt(depositedAmount.ToBig()),
		}),
	); err != nil {
		return nil, err
	}

	if err := p.EmitDepositEvent(ctx, stateDB, caller, depositedAmount.ToBig()); err != nil {
		return nil, err
	}

	return nil, nil
}
```

**File:** precompiles/werc20/tx.go (L59-80)
```go
// Withdraw is a no-op and mock function that provides the same interface as the
// WETH contract to support equality between the native coin and its wrapped
// ERC-20 (e.g. ATOM and WEVMOS).
func (p Precompile) Withdraw(ctx sdk.Context, contract *vm.Contract, stateDB vm.StateDB, args []interface{}) ([]byte, error) {
	amount, ok := args[0].(*big.Int)
	if !ok {
		return nil, fmt.Errorf("invalid argument type: %T", args[0])
	}
	amountInt := math.NewIntFromBigInt(amount)

	caller := contract.Caller()
	callerAccAddress := sdk.AccAddress(caller.Bytes())
	nativeBalance := p.BankKeeper.SpendableCoin(ctx, callerAccAddress, evmtypes.GetEVMCoinDenom())
	if nativeBalance.Amount.Mul(types.ConversionFactor()).LT(amountInt) {
		return nil, fmt.Errorf("account balance %v is lower than withdraw balance %v", nativeBalance.Amount, amountInt)
	}

	if err := p.EmitWithdrawalEvent(ctx, stateDB, caller, amount); err != nil {
		return nil, err
	}
	return nil, nil
}
```

**File:** precompiles/werc20/README.md (L102-107)
```markdown
## Security Considerations

1. **No Lock-up**: Native tokens are never locked in the precompile
2. **Direct Integration**: Operations directly interact with the bank module
3. **Balance Consistency**: EVM and Cosmos SDK balances remain synchronized
4. **Fallback Protection**: Sending native tokens to the contract automatically triggers deposit
```

**File:** precompiles/common/precompile.go (L234-252)
```go
// standardCallData is a helper function that returns the method to be called when the calldata is 4 bytes or more.
func standardCallData(api abi.ABI, contract *vm.Contract) (method *abi.Method, err error) {
	methodID := contract.Input[:4]
	// NOTE: this function iterates over the method map and returns
	// the method with the given ID
	method, err = api.MethodById(methodID)

	// Case 3.1 calldata contains a non-existing method ID, and `fallback` is not present - return error
	if err != nil && !api.HasFallback() {
		return nil, err
	}

	// Case 3.2: calldata contains a non-existing method ID - 'fallback' is called if present
	if err != nil && api.HasFallback() {
		return &api.Fallback, nil
	}

	return method, nil
}
```
