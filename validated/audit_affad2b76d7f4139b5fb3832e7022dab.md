### Title
Inconsistent Balance Accounting Between Virtual Fee Deductions and `CheckEthCanTransfer` / EVM Execution in Multi-Message Transactions — (File: `ante/eth.go`, `x/evm/keeper/utils.go`, `x/evm/keeper/gas.go`)

---

### Summary

In Ethermint's ante handler pipeline, transaction fees are deducted **virtually** (via `SendCoinsFromAccountToModuleVirtual` into the ObjectStore) in `CheckEthGasConsume`, but the balance checks in `CheckEthCanTransfer` and the EVM execution path (`CanTransfer` / `SubBalance`) read exclusively from the **KV store**, which does not reflect those virtual deductions. For a Cosmos transaction carrying multiple `MsgEthereumTx` messages, this inconsistency allows a sender to pass the value-transfer check and execute a value transfer whose amount, combined with the fees of a preceding message, exceeds the sender's actual balance.

---

### Finding Description

**Ante handler execution order** (`evmd/ante/handler_options.go`, `newEthAnteHandler`):

1. `VerifyEthAccount` — checks each message's `tx.Cost()` (fee + value) against the **KV balance** independently.
2. `CheckEthCanTransfer` — checks each message's `value` against the **KV balance** independently.
3. `CheckEthGasConsume` — deducts the effective fee for each message **virtually** (ObjectStore only). [1](#0-0) 

**Virtual deduction path** (`x/evm/keeper/utils.go`, `DeductFees`):

```go
bankKeeper.SendCoinsFromAccountToModuleVirtual(ctx, acc.GetAddress(), authtypes.FeeCollectorName, fees)
```

This writes to the ObjectStore, leaving the KV store balance unchanged. [2](#0-1) 

**Balance read in `CheckEthCanTransfer`** (`ante/eth.go`, `canTransfer`):

```go
balance := evmKeeper.GetBalance(ctx, sdk.AccAddress(from.Bytes()), denom)
return balance.ToBig().Cmp(amount) >= 0
```

`GetBalance` calls `bankKeeper.GetBalance` which reads the **KV store** — it does not see the virtual deductions already applied by `CheckEthGasConsume` for earlier messages in the same tx. [3](#0-2) 

**Balance read in EVM execution** (`x/evm/keeper/statedb.go`, `SubBalance`):

```go
k.bankKeeper.SendCoinsFromAccountToModule(ctx, addr, types.ModuleName, coins)
```

This is a **non-virtual** KV-store operation. It reads and writes the KV balance, completely unaware of the pending virtual deductions in the ObjectStore. [4](#0-3) 

**Gas refund path** (`x/evm/keeper/gas.go`, `RefundGasWithPrice`) also uses `SendCoinsFromModuleToAccountVirtual`, confirming the entire fee lifecycle is virtual. [5](#0-4) 

**Concrete inconsistency for a two-message tx** (M1: fee=60, value=0; M2: fee=0, value=50; balance=100):

| Step | Function | Balance read | Result |
|------|----------|-------------|--------|
| 1 | `VerifyEthAccount` M1 | KV=100 ≥ 60 | ✓ |
| 2 | `VerifyEthAccount` M2 | KV=100 ≥ 50 | ✓ (M1 cost not subtracted) |
| 3 | `CheckEthCanTransfer` M2 | KV=100 ≥ 50 | ✓ (M1 fee not subtracted) |
| 4 | `CheckEthGasConsume` M1 | virtual deduct 60 | virtual=40 |
| 5

### Citations

**File:** evmd/ante/handler_options.go (L131-145)
```go
		if err := evmante.VerifyEthAccount(ctx, tx, options.EvmKeeper, evmDenom, accountGetter, rules); err != nil {
			return ctx, err
		}

		if err := evmante.CheckEthCanTransfer(ctx, tx, baseFee, rules, options.EvmKeeper, evmParams); err != nil {
			return ctx, err
		}

		ctx, err = evmante.CheckEthGasConsume(
			ctx, tx, rules, options.EvmKeeper,
			baseFee, evmDenom,
		)
		if err != nil {
			return ctx, err
		}
```

**File:** x/evm/keeper/utils.go (L207-216)
```go
func DeductFees(bankKeeper types.BankKeeper, ctx sdk.Context, acc sdk.AccountI, fees sdk.Coins) error {
	if !fees.IsValid() {
		return errorsmod.Wrapf(errortypes.ErrInsufficientFee, "invalid fee amount: %s", fees)
	}
	if ctx.BlockHeight() > 0 {
		if err := bankKeeper.SendCoinsFromAccountToModuleVirtual(ctx, acc.GetAddress(), authtypes.FeeCollectorName, fees); err != nil {
			return errorsmod.Wrap(errortypes.ErrInsufficientFunds, err.Error())
		}
	}
	return nil
```

**File:** ante/eth.go (L255-259)
```go
// canTransfer adapted the core.CanTransfer from go-ethereum
func canTransfer(ctx sdk.Context, evmKeeper interfaces.EVMKeeper, denom string, from common.Address, amount *big.Int) bool {
	balance := evmKeeper.GetBalance(ctx, sdk.AccAddress(from.Bytes()), denom)
	return balance.ToBig().Cmp(amount) >= 0
}
```

**File:** x/evm/keeper/statedb.go (L92-101)
```go
func (k *Keeper) SubBalance(ctx sdk.Context, addr sdk.AccAddress, coin sdk.Coin) (uint256.Int, error) {
	coins := sdk.NewCoins(coin)
	prevBalance := k.GetBalance(ctx, addr, coin.Denom)
	if err := k.bankKeeper.SendCoinsFromAccountToModule(ctx, addr, types.ModuleName, coins); err != nil {
		return uint256.Int{}, err
	}
	if err := k.bankKeeper.BurnCoins(ctx, types.ModuleName, coins); err != nil {
		return uint256.Int{}, err
	}
	return prevBalance, nil
```

**File:** x/evm/keeper/gas.go (L54-88)
```go
// RefundGasWithPrice transfers the leftover gas to sender using the provided gas price.
func (k *Keeper) RefundGasWithPrice(
	ctx sdk.Context,
	msg *core.Message,
	leftoverGas uint64,
	gasPrice *big.Int,
	denom string,
) error {
	if gasPrice == nil {
		gasPrice = new(big.Int)
	}

	// Return EVM tokens for remaining gas, exchanged at the original rate.
	remaining := new(big.Int).Mul(new(big.Int).SetUint64(leftoverGas), gasPrice)

	switch remaining.Sign() {
	case -1:
		// negative refund errors
		return errorsmod.Wrapf(types.ErrInvalidRefund, "refunded amount value cannot be negative %d", remaining.Int64())
	case 1:
		// positive amount refund
		refundedCoins := sdk.Coins{sdk.NewCoin(denom, sdkmath.NewIntFromBigInt(remaining))}

		// refund to sender from the fee collector module account, which is the escrow account in charge of collecting tx fees
		err := k.bankKeeper.SendCoinsFromModuleToAccountVirtual(ctx, authtypes.FeeCollectorName, msg.From.Bytes(), refundedCoins)
		if err != nil {
			err = errorsmod.Wrapf(errortypes.ErrInsufficientFunds, "fee collector account failed to refund fees: %s", err.Error())
			return errorsmod.Wrapf(err, "failed to refund %d leftover gas (%s)", leftoverGas, refundedCoins.String())
		}
	default:
		// no refund, consume gas and update the tx gas meter
	}

	return nil
}
```
