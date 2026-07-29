## Finding

Cosmos EVM's WERC20 precompile inherits the ERC20 payable-value guard bypass problem described in the reference report: not all entrypoints forward/validate `msg.value`, letting native tokens become permanently stuck at a non-EOA address.

### Title
Native tokens sent with `msg.value` to non-deposit WERC20 methods are permanently locked - (File: precompiles/werc20/werc20.go)

### Summary
The standalone ERC20 precompile explicitly rejects any call carrying a nonzero `msg.value` because the precompile address is not an EOA and cannot recover stray funds: `if value := contract.Value(); value.Sign() == 1 { return nil, fmt.Errorf(...) }` [1](#0-0)  . The WERC20 precompile, however, overrides `Execute` and only special-cases `Deposit`/`Withdraw`/fallback/receive; every other ERC20 method (`transfer`, `transferFrom`, `approve`, etc.) is routed straight to the shared `HandleMethod` without ever checking `contract.Value()`: [2](#0-1) .

### Finding Description
When an EVM `CALL` carries a nonzero value, the go-ethereum call machinery unconditionally transfers that native balance from the caller to the callee address in the `StateDB` as part of standard `CALL` semantics, before/independently of the callee's own logic (this is analogous to `Nexus.executeUserOp`/fallback not forwarding `msg.value`: the value movement happens at the call layer, and it is up to the receiving logic to actually account for it). For the WERC20 precompile, that balance transfer happens to the precompile's contract address, which is a raw precompile address rather than a module or EOA account.

Only the `Deposit` handler consumes `contract.Value()` and immediately routes it back to the caller via `p.BankKeeper.SendCoins`: [3](#0-2) . If a caller instead invokes any other ERC20 method (e.g. `transfer`, `approve`, `transferFrom`) while attaching `msg.value` (which the EVM permits since the check for rejecting value is not inherited/re-implemented in `werc20.Execute`), the value is transferred into the precompile's EVM balance by the call machinery, but no corresponding code path (`Withdraw` is a documented no-op, and `HandleMethod` never touches `contract.Value()`) ever returns or forwards those funds. The `Withdraw` method is explicitly a no-op by design (matches WETH-style semantics on this system where native/WERC20 balances are the same view) and does not transfer anything: [4](#0-3) .

Because the WERC20 precompile address is not a normal account (it has no private key and its only actionable methods are `deposit`/`withdraw`, both of which don't sweep stray balances), any native value attached to a non-deposit call becomes permanently unrecoverable — exactly the "native tokens stuck ... unrecoverable" impact described in the source report for factories/fallback handlers that don't forward `msg.value`.

### Impact Explanation
This is a Critical, unprivileged, permanent loss-of-funds bug: any ordinary user can trigger it by simply crafting an EVM transaction that calls a non-deposit WERC20 method (`transfer`, `approve`, `transferFrom`, or any ABI method dispatched via `HandleMethod`) with a non-zero value field. The attacker's own funds (or funds an attacker tricks another integrator/contract into sending, e.g. a DeFi contract naively forwarding `msg.value` while calling a non-`deposit` WERC20 function) end up irretrievably locked at the WERC20 precompile address, matching the "permanent freezing/locking/theft of user funds ... token-pair-backed balances" allowed-impact category.

### Likelihood Explanation
High likelihood: no privileged access or special conditions are required. Standard EOA or contract calls that both attach value and invoke a non-`deposit` method (which is easy to do accidentally, e.g. `wrappedToken.transferFrom{value: x}(...)` in third-party integration code, or deliberately by an attacker griefing a victim contract) trigger the loss. The `erc20` precompile already treats this exact scenario as security-critical enough to explicitly guard against it, underscoring that omitting the same guard in `werc20` is an oversight rather than intended behavior.

### Recommendation
Add the same guard used in `erc20.Precompile.Execute` to `werc20.Precompile.Execute` for all non-deposit code paths: reject (or explicitly refund) any nonzero `contract.Value()` when the resolved method is not `Deposit`/fallback/`receive`, e.g.:
```go
if method.Name != DepositMethod && method.Type != abi.Fallback && method.Type != abi.Receive {
    if value := contract.Value(); value.Sign() == 1 {
        return nil, fmt.Errorf(erc20.ErrCannotReceiveFunds, value.String())
    }
}
```
Alternatively, always route through `Deposit`'s bank-forwarding logic first whenever `contract.Value()` is nonzero, regardless of which method was called, ensuring `msg.value` is never silently absorbed and stranded.

### Proof of Concept
1. Deploy/attach to the WERC20 precompile at its fixed EVM address.
2. Craft an EVM transaction calling `transfer(address,uint256)` (or `approve`, `transferFrom`) on the WERC20 precompile with a nonzero `value` field, e.g. via `ethers`: `werc20.transfer(receiver, 1, { value: parseEther("1") })`.
3. The EVM's call semantics move 1 ETH-equivalent native balance from the caller to the WERC20 precompile address in the `StateDB`/bank layer during message execution.
4. `werc20.Execute` dispatches to `HandleMethod` → `Transfer`, which never reads or refunds `contract.Value()`: [5](#0-4) .
5. The transaction succeeds; the sent native value remains credited to the WERC20 precompile address. Since `Withdraw` is a no-op and there is no other method that sweeps or refunds the precompile's own balance, the funds are permanently unrecoverable.

### Citations

**File:** precompiles/erc20/erc20.go (L148-155)
```go
func (p Precompile) Execute(ctx sdk.Context, stateDB vm.StateDB, contract *vm.Contract, readOnly bool) ([]byte, error) {
	// ERC20 precompiles cannot receive funds because they are not managed by an
	// EOA and will not be possible to recover funds sent to an instance of
	// them.This check is a safety measure because currently funds cannot be
	// received due to the lack of a fallback handler.
	if value := contract.Value(); value.Sign() == 1 {
		return nil, fmt.Errorf(ErrCannotReceiveFunds, contract.Value().String())
	}
```

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
