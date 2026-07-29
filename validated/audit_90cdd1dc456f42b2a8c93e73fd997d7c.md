Based on my research, I found a plausible Critical-severity analog in the **WERC20 precompile's value-handling**, though I was not able to fully confirm one link in the chain (see the Uncertainty note at the end) before running out of tool budget.

### Title
Native token sent to non-payable WERC20 precompile methods can be permanently locked (and phantom-minted) at the precompile address - (File: `precompiles/werc20/tx.go`, `precompiles/werc20/werc20.go`, `x/vm/keeper/statedb.go`)

### Summary
The external report's root cause class is "a function that should move native value doesn't correctly account for/gate `msg.value`." The Cosmos EVM analog is in the WERC20 precompile: `deposit()` is the only method designed to receive `contract.Value()` and immediately forwards it back to the caller via `BankKeeper.SendCoins` [1](#0-0) . The base ERC20 precompile explicitly guards against any other method receiving value: `if value := contract.Value(); value.Sign() == 1 { return nil, fmt.Errorf(ErrCannotReceiveFunds, ...) }` [2](#0-1) . WERC20 overrides `Execute`/`Run` to add `Deposit`/`Withdraw` handling on top of the embedded ERC20 `Precompile` [3](#0-2) , and `Withdraw()` itself never inspects or rejects `contract.Value()` [4](#0-3) .

### Finding Description
When any EVM `CALL` sends `msg.value` to an address (including a precompile), go-ethereum's Call machinery moves that value in the `StateDB` from caller to callee (`SubBalance`/`AddBalance` on `stateObject`) before the callee's code/precompile `Run` executes [5](#0-4) . This is purely an in-memory `uint256` balance change; it is only reconciled to real bank balances later, when `Keeper.SetBalance` diffs the new EVM balance against the current spendable bank coin and mints or burns the delta [6](#0-5) .

`Deposit()` relies on the assumption that the value credited to the WERC20 precompile's address is properly reflected in the real bank balance of the precompile account by the time `SendCoins` executes, and forwards it back to the caller [7](#0-6) . However, unlike the base `erc20.Precompile.Execute`, which rejects value for *every* method (`ErrCannotReceiveFunds` at [8](#0-7) ), the `Withdraw` handler in WERC20 does not check `contract.Value()` at all before doing its no-op logic [9](#0-8) . If value can reach `Withdraw` (or any WERC20-dispatched method besides `deposit`) without being rejected by WERC20's own `Execute`/dispatch logic, the attached native value is:
1. Debited from the caller's real bank balance at EVM `Commit` time (via burn, matching the negative EVM balance delta for the caller).
2. Credited (minted) into the WERC20 precompile's Cosmos account at `Commit` time (via `MintAmountToAccount`, matching the positive EVM balance delta for the precompile address), because the WERC20 precompile is otherwise never expected to hold value.
3. Never moved back out, since `Withdraw()` performs no bank operation at all — only an event emission and a spendable-balance check on the *caller*, not the precompile [10](#0-9) .

There is no admin/user-facing mechanism shown in the reviewed code to sweep funds out of the WERC20 precompile's underlying account, so any coins minted into it this way would be permanently unspendable/locked.

### Impact Explanation
If the root cause is confirmed (see Uncertainty below), this is a Critical unauthorized/irreversible loss of user funds: an unprivileged caller who calls `withdraw()` (or any WERC20 method other than `deposit`) with attached `msg.value` would have that native value irrecoverably burned from their spendable balance and effectively vanish into the precompile's account with no path to reclaim it — a permanent freezing/loss of user funds, matching the "Critical permanent freezing, locking ... of user funds" impact gate.

### Likelihood Explanation
Likelihood depends entirely on whether WERC20's own `Execute`/method-dispatch logic (the body of `Run`/`Execute` in `precompiles/werc20/werc20.go`, which I could not fully view) omits the `ErrCannotReceiveFunds`-style guard for methods other than `deposit`. If that guard is present and applied uniformly (e.g., inherited unmodified from the base `erc20.Precompile.Execute`) before dispatching to `Withdraw`/`Transfer`/etc., this issue does not exist and the value would be rejected/reverted before ever reaching the vulnerable no-op `Withdraw` path.

### Recommendation
- Ensure `WERC20.Execute` (or its `Run`) rejects `contract.Value() > 0` for every method except `deposit` and the fallback/`receive` handlers, mirroring the check already present in `precompiles/erc20/erc20.go:148-155`.
- Add a defensive check inside `Withdraw()` itself to reject any non-zero `contract.Value()`.
- Add an integration test that calls `withdraw()`/`transfer()`/`approve()` on the WERC20 precompile with non-zero `msg.value` and asserts the transaction reverts and the caller's balance is unaffected.

### Proof of Concept
Conceptual (subject to confirmation of the root cause):
1. Deploy/obtain the WERC20 precompile address for the chain's native token.
2. Call `withdraw(1)` (or any WERC20 method other than `deposit`) attaching `value: 1 ether`.
3. If WERC20 does not reject the attached value before dispatch, the transaction succeeds; the caller's spendable native balance decreases by 1 ether at commit time (burn), while the WERC20 precompile's Cosmos account balance increases by 1 ether (mint) with no corresponding bank movement performed by `Withdraw()`.
4. Verify no subsequent call can retrieve that 1 ether from the precompile's account — funds are permanently locked.

### Uncertainty
I was unable to view the full body of `precompiles/werc20/werc20.go`'s `Run`/`Execute` method dispatch (only saw lines 1-98, which end right where dispatch to `Deposit`/`Withdraw` would occur) or the exact `x/vm` EVM `Call` code path that invokes precompiles with value, due to running out of tool iterations. Confirming whether WERC20 actually omits the value guard for non-deposit methods, and confirming that a precompile call's EVM-level value transfer indeed reaches `Keeper.SetBalance`'s mint/burn reconciliation at commit time (rather than being blocked/reverted elsewhere), requires further investigation in a full Devin session with complete file access before this can be treated as a confirmed, exploitable vulnerability rather than a plausible analog.

### Citations

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

**File:** precompiles/werc20/werc20.go (L39-98)
```go
// Precompile defines the precompiled contract for WERC20.
type Precompile struct {
	*erc20.Precompile
}

const (
	// DepositRequiredGas defines the gas required for the Deposit transaction.
	DepositRequiredGas uint64 = 23_878
	// WithdrawRequiredGas defines the gas required for the Withdraw transaction.
	WithdrawRequiredGas uint64 = 9207
)

// NewPrecompile creates a new WERC20 Precompile instance implementing the
// PrecompiledContract interface. This type wraps around the ERC20 Precompile
// instance to provide additional methods.
func NewPrecompile(
	tokenPair erc20types.TokenPair,
	bankKeeper cmn.BankKeeper,
	erc20Keeper Erc20Keeper,
	transferKeeper ibcutils.TransferKeeper,
) *Precompile {
	erc20Precompile := erc20.NewPrecompile(tokenPair, bankKeeper, erc20Keeper, transferKeeper)

	// use the IWERC20 ABI
	erc20Precompile.ABI = ABI

	return &Precompile{
		Precompile: erc20Precompile,
	}
}

// RequiredGas calculates the contract gas use.
func (p Precompile) RequiredGas(input []byte) uint64 {
	// TODO: these values were obtained from Remix using the WEVMOS9.sol.
	// We should execute the transactions from Cosmos EVM testnet
	// to ensure parity in the values.

	// If there is no method ID, then it's the fallback or receive case
	if len(input) < 4 {
		return DepositRequiredGas
	}

	methodID := input[:4]
	method, err := p.MethodById(methodID)
	if err != nil {
		return 0
	}

	switch method.Name {
	case DepositMethod:
		return DepositRequiredGas
	case WithdrawMethod:
		return WithdrawRequiredGas
	default:
		return p.Precompile.RequiredGas(input)
	}
}

func (p Precompile) Run(evm *vm.EVM, contract *vm.Contract, readonly bool) ([]byte, error) {
	return p.RunNativeAction(evm, contract, func(ctx sdk.Context) ([]byte, error) {
```

**File:** x/vm/statedb/state_object.go (L109-126)
```go
// AddBalance adds amount to s's balance.
// It is used to add funds to the destination account of a transfer.
func (s *stateObject) AddBalance(amount *uint256.Int) uint256.Int {
	if amount.IsZero() {
		return *(s.Balance())
	}
	return s.SetBalance(new(uint256.Int).Add(s.Balance(), amount))
}

// SubBalance removes amount from s's balance.
// It is used to remove funds from the origin account of a transfer.
// Returns the previous balance
func (s *stateObject) SubBalance(amount *uint256.Int) uint256.Int {
	if amount.IsZero() {
		return *(s.Balance())
	}
	return s.SetBalance(new(uint256.Int).Sub(s.Balance(), amount))
}
```

**File:** x/vm/keeper/statedb.go (L111-136)
```go
// SetBalance update account's balance, compare with current balance first, then decide to mint or burn.
func (k *Keeper) SetBalance(ctx sdk.Context, addr common.Address, amount *uint256.Int) error {
	if amount == nil {
		return nil
	}
	cosmosAddr := sdk.AccAddress(addr.Bytes())
	coin := k.bankWrapper.SpendableCoin(ctx, cosmosAddr, types.GetEVMCoinDenom())

	balance := coin.Amount.BigInt()
	delta := new(big.Int).Sub(amount.ToBig(), balance)
	switch delta.Sign() {
	case 1:
		// mint
		if err := k.bankWrapper.MintAmountToAccount(ctx, cosmosAddr, delta); err != nil {
			return err
		}
	case -1:
		// burn
		if err := k.bankWrapper.BurnAmountFromAccount(ctx, cosmosAddr, new(big.Int).Neg(delta)); err != nil {
			return err
		}
	default:
		// not changed
	}
	return nil
}
```
