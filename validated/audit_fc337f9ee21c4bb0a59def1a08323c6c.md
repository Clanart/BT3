### Title
Gas Refund Sent to `msg.From` Instead of Fee Granter After Fee-Grant-Funded EVM Transaction - (File: `x/evm/keeper/gas.go`)

### Summary
When a Cosmos SDK fee grant is used to fund an EVM transaction, the ante handler deducts the full `gasLimit × gasPrice` from the **fee granter's** account. However, `RefundGasWithPrice` unconditionally refunds the unused gas to `msg.From` (the Ethereum transaction sender), not to the fee granter who actually paid. This is a direct structural analog to the HyperdriveLP mint-to-destination / burn-from-msg.sender mismatch: one step credits address A, a subsequent step debits/credits address B.

---

### Finding Description

**Step 1 — Fee deduction (ante handler):**

In `ante/evm/nativefee.go`, `checkDeductFee` resolves the payer:

```go
feePayer    := feeTx.FeePayer()   // = msg.From (Ethereum sender)
feeGranter  := feeTx.FeeGranter() // = granter address, if set
deductFeesFrom := feePayer

if feeGranter != nil {
    ...
    deductFeesFrom = feeGranterAddr   // ← fee deducted from GRANTER
}
evmkeeper.DeductFees(dfd.bankKeeper, ctx, deductFeesFromAcc, fee)
```

The full `gasLimit × effectiveGasPrice` is taken from the **fee granter**. [1](#0-0) 

**Step 2 — Gas refund (msg server / keeper):**

In `x/evm/keeper/gas.go`, `RefundGasWithPrice` always sends the leftover-gas refund to `msg.From`:

```go
err := k.bankKeeper.SendCoinsFromModuleToAccountVirtual(
    ctx,
    authtypes.FeeCollectorName,
    msg.From.Bytes(),   // ← always the Ethereum sender, never the granter
    refundedCoins,
)
``` [2](#0-1) 

**The mismatch:**

| Step | Address used | Amount |
|---|---|---|
| Ante: fee deduction | `feeGranter` | `gasLimit × gasPrice` |
| Keeper: gas refund | `msg.From` | `(gasLimit − gasUsed) × gasPrice` |

The fee granter overpays by `(gasLimit − gasUsed) × gasPrice`; that exact amount is credited for free to `msg.From`.

---

### Impact Explanation

This is a **High** severity fund mis-accounting bug matching the allowed impact: *"EVM state transition, gas refund, fee market, ante handler, or proposal handling bug that permits valid user funds/fees to be mis-accounted."*

- The fee granter loses `(gasLimit − gasUsed) × gasPrice` that should be returned to them.
- The transaction sender (`msg.From`) receives that same amount as an unearned windfall from the fee collector module.
- With a sufficiently large gas limit and low actual gas usage, the attacker can extract nearly the entire granted fee budget in a single transaction.

---

### Likelihood Explanation

The Cosmos SDK fee-grant module is a standard, production-enabled feature. Any user who has been granted a fee allowance can exploit this immediately by submitting a `MsgEthereumTx` wrapped in a Cosmos SDK transaction with `AuthInfo.Fee.Granter` set. No privileged access, governance action, or validator cooperation is required. The entry path is a normal, unprivileged transaction submission.

---

### Recommendation

`RefundGasWithPrice` must refund to the address that actually paid the fee. The simplest fix is to thread the `deductFeesFrom` address (resolved in the ante handler) through to the refund call, or to store it in the context/transient store so `RefundGasWithPrice` can read it. Concretely:

```go
// Resolve refund recipient: fee granter if present, otherwise msg.From
refundTo := msg.From.Bytes()
if granter := feeTx.FeeGranter(); granter != nil {
    refundTo = granter
}
err := k.bankKeeper.SendCoinsFromModuleToAccountVirtual(
    ctx, authtypes.FeeCollectorName, refundTo, refundedCoins,
)
```

---

### Proof of Concept

1. Alice (`feeGranter`) grants Bob (`msg.From`) a fee allowance of 10 ETH.
2. Bob constructs a `MsgEthereumTx` with `gasLimit = 1,000,000` and `gasPrice = 10 gwei`, setting `AuthInfo.Fee.Granter = Alice`.
3. Ante handler deducts `1,000,000 × 10 gwei = 0.01 ETH` from Alice.
4. The EVM executes Bob's transaction; actual `gasUsed = 21,000`.
5. `RefundGasWithPrice` computes `leftoverGas = 979,000` and sends `979,000 × 10 gwei ≈ 0.00979 ETH` to **Bob** (`msg.From`).
6. Alice paid `0.01 ETH` but only `0.00021 ETH` worth of gas was consumed on her behalf; she is never refunded the `0.00979 ETH` difference.
7. Bob received `0.00979 ETH` he never paid for. Repeating this drains Alice's entire granted allowance into Bob's balance. [1](#0-0) [3](#0-2)

### Citations

**File:** ante/evm/nativefee.go (L83-115)
```go
	feePayer := feeTx.FeePayer()
	feeGranter := feeTx.FeeGranter()
	deductFeesFrom := feePayer

	// if feegranter set deduct fee from feegranter account.
	// this works with only when feegrant enabled.
	if feeGranter != nil {
		feeGranterAddr := sdk.AccAddress(feeGranter)

		if dfd.feegrantKeeper == nil {
			return sdkerrors.ErrInvalidRequest.Wrap("fee grants are not enabled")
		} else if !bytes.Equal(feeGranterAddr, feePayer) {
			err := dfd.feegrantKeeper.UseGrantedFees(ctx, feeGranterAddr, feePayer, fee, sdkTx.GetMsgs())
			if err != nil {
				return errorsmod.Wrapf(err, "%s does not allow to pay fees for %s", feeGranter, feePayer)
			}
		}

		deductFeesFrom = feeGranterAddr
	}

	deductFeesFromAcc := dfd.accountKeeper.GetAccount(ctx, deductFeesFrom)
	if deductFeesFromAcc == nil {
		return sdkerrors.ErrUnknownAddress.Wrapf("fee payer address: %s does not exist", deductFeesFrom)
	}

	// deduct the fees
	if !fee.IsZero() {
		err := evmkeeper.DeductFees(dfd.bankKeeper, ctx, deductFeesFromAcc, fee)
		if err != nil {
			return err
		}
	}
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
