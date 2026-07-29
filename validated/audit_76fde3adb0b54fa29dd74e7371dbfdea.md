### Title
WERC20 precompile does not reject `msg.value` on ERC20 methods (`transfer`/`transferFrom`/`approve`), allowing native token loss when value is attached to non-`deposit` calls - (File: precompiles/werc20/werc20.go)

### Summary
The bug report describes a check that fails to reject a call when native value is attached alongside calldata, allowing bypass of a value-transfer restriction. The Cosmos EVM analog is in the WERC20 precompile: unlike the base ERC20 precompile, which explicitly rejects any call carrying `msg.value` before dispatching to a method, `werc20.Precompile.Execute` only handles value for the `deposit`/fallback/receive path and forwards all other methods (`transfer`, `transferFrom`, `approve`) straight to `HandleMethod` without any check on `contract.Value()`.

### Finding Description
The base ERC20 precompile explicitly guards against receiving funds on any call: [1](#0-0) 

This check runs unconditionally in `Execute`, before `SetupABI`/`HandleMethod` dispatch, so it protects `transfer`, `transferFrom`, and `approve` in the plain ERC20 precompile.

`WERC20.Precompile` wraps the ERC20 precompile but **overrides** `Execute` with its own dispatch logic that does not perform this check for the ERC20-inherited methods: [2](#0-1) 

Only the `deposit`/fallback/receive branch consumes `contract.Value()` (via `Deposit`, which explicitly reads `contract.Value()` and forwards it back to the caller with `BankKeeper.SendCoins`): [3](#0-2) 

The `withdraw` branch and the `default` branch (ERC20 methods routed to `p.HandleMethod`) never read or act on `contract.Value()`: [4](#0-3) 

In the standard go-ethereum EVM call flow (inherited by this fork), when a `CALL` targets any address — including a precompile — with a non-zero value, the EVM transfers that value from the caller to the target address's balance in the state DB *before* invoking the precompiled contract's `Run`. For the WERC20 precompile, this means the precompile's own EVM-visible balance is credited with the sent value as part of the call mechanics, independent of what the precompile's `Run`/`Execute` logic does afterward. Since `transfer`, `transferFrom`, and `approve` (routed to `p.HandleMethod`) do nothing to reconcile or return that value (unlike `deposit`, which explicitly calls `SendCoins` to send the deposited amount back to the caller), any native value attached to a `transfer`/`transferFrom`/`approve` call to the WERC20 precompile address is absorbed into the precompile's EVM balance with no corresponding bank-module accounting and no path to recover it.

This mirrors the reported bug-class: the precompile-level state machine has one check path (base ERC20's `Execute`, meant to reject "shouldn't receive funds") that is bypassed because the derived contract's overridden dispatch logic reintroduces the case where "any calldata specified" (i.e., a valid ERC20 method selector) causes the value-check to never be reached.

### Impact Explanation
This falls under "Critical permanent freezing, locking, theft, or unauthorized extraction of user funds... or token-pair-backed balances." An unprivileged user (or any contract calling the WERC20 precompile) who attaches non-zero `msg.value` to a `transfer`, `transferFrom`, or `approve` call would have that native value siphoned into the precompile address's EVM balance without any corresponding bank-module reflection, and with no mechanism in the precompile to reclaim or reconcile it — since only `deposit()` reads and forwards `contract.Value()`. This breaks the WERC20 module's core invariant documented in its own README that "Native tokens are never locked in the precompile" and that "EVM and Cosmos SDK balances remain synchronized": [5](#0-4) 

### Likelihood Explanation
I was not able to fully confirm, within the scope of this investigation, the exact behavior of the specific EVM fork's `Call`/`RunPrecompiledContract` implementation in this repository (i.e., whether it unconditionally transfers `msg.value` into a precompile target's state-DB balance prior to invoking `Run`, as standard go-ethereum does). I could not locate or inspect that exact call site in the available index. If that standard geth behavior holds (which is the default in essentially all go-ethereum-derived EVMs, including Cosmos EVM's fork), the trigger requires nothing more than a single unprivileged transaction crafting a `CALL` with value to the WERC20 precompile address and any ERC20 selector — trivially reachable by any user or contract.

### Recommendation
Add a check equivalent to the base ERC20 precompile's `ErrCannotReceiveFunds` guard for every branch in `werc20.Precompile.Execute` except `deposit`/fallback/receive (which are the only paths designed to intentionally consume `contract.Value()`). Concretely, in the `withdraw` and `default` (ERC20 method) branches, reject the call if `contract.Value().Sign() > 0`, mirroring `precompiles/erc20/erc20.go`'s check, so that value can only ever be attached to `deposit()`.

### Proof of Concept
1. Deploy/attach to the WERC20 precompile address for a token pair.
2. From an EOA or contract with native token balance, construct a raw `CALL` to the WERC20 precompile with:
   - `value` = some non-zero native amount (e.g., 1 token)
   - `data` = ABI-encoded `transfer(address,uint256)` (or `approve`/`transferFrom`) with a valid recipient/amount.
3. Observe that `Execute` dispatches to the `default` branch (`p.HandleMethod`), performing the ERC20 transfer accounting via the bank keeper, while the attached native `value` is absorbed into the EVM state-DB balance of the precompile address as part of standard EVM call semantics.
4. Verify no `SendCoins`/refund call occurs for that attached value (unlike in `Deposit`), and that there is no interface (via `withdraw` or otherwise) to recover funds credited to the precompile's own balance — confirming the value is permanently stuck/lost from the user's perspective.

### Citations

**File:** precompiles/erc20/erc20.go (L148-156)
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

**File:** precompiles/werc20/README.md (L102-106)
```markdown
## Security Considerations

1. **No Lock-up**: Native tokens are never locked in the precompile
2. **Direct Integration**: Operations directly interact with the bank module
3. **Balance Consistency**: EVM and Cosmos SDK balances remain synchronized
```
