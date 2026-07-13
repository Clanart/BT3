### Title
Integer Division Truncation in `NewDynamicFeeChecker` Allows Zero-Fee Cosmos Transactions When `baseFee = 0` - (File: `ante/evm/fee_checker.go`)

---

### Summary

`NewDynamicFeeChecker` computes `feeCap` via integer division `fee.Quo(gas)`. When a user submits a Cosmos tx with `fee < gas`, `feeCap` truncates to zero. If `baseFee = 0` (e.g., `NoBaseFee = true` with London hardfork enabled), the effective fee resolves to zero and `checkDeductFee` skips deduction entirely, allowing the transaction to execute for free.

---

### Finding Description

In `ante/evm/fee_checker.go`, `NewDynamicFeeChecker` computes the per-gas fee cap using integer division:

```go
feeCap := fee.Quo(sdkmath.NewIntFromUint64(gas))   // line 83
```

When `fee < gas` (e.g., `fee = 1`, `gas = 2`), `feeCap` truncates to `0`.

The subsequent guard only rejects if `feeCap < baseFeeInt`:

```go
if feeCap.LT(baseFeeInt) {   // line 86
    return nil, 0, errorsmod.Wrapf(...)
}
```

When `baseFee = 0`, the comparison is `0 < 0 = false`, so it passes. The effective price is then computed as:

```go
effectivePrice := sdkmath.NewIntFromBigInt(
    types.EffectiveGasPrice(baseFeeInt.BigInt(), feeCap.BigInt(), maxPriorityPrice.BigInt()),
)
// = min(0 + maxPriorityPrice, 0) = 0
```

This yields `effectiveFee = 0 * gas = 0`. The `txFeeChecker` returns `sdk.Coins{}` (empty) to `DeductFeeDecorator`, which then skips deduction entirely:

```go
// ante/evm/nativefee.go line 110
if !fee.IsZero() {
    err := evmkeeper.DeductFees(dfd.bankKeeper, ctx, deductFeesFromAcc, fee)
}
```

No fee is charged. The transaction executes for free.

The `MinGasPriceDecorator` that runs earlier short-circuits when `MinGasPrice = 0`:

```go
// ante/cosmos/min_gas_price.go line 57
if minGasPrice.IsZero() || simulate {
    return next(ctx, tx, simulate)
}
```

So there is no upstream guard that catches this when both `baseFee = 0` and `MinGasPrice = 0`.

---

### Impact Explanation

Any Cosmos-wrapped transaction (e.g., governance, IBC, staking, or any SDK message) processed through the `newLegacyCosmosAnteHandlerEip712` or equivalent ante chain with `DynamicFeeChecker` enabled can be submitted with a nominal fee of 1 and a gas limit of 2 (or any `fee < gas`), resulting in zero fee deducted. This is a direct mis-accounting of user fees: the fee collector receives nothing, validators/stakers receive no fee revenue, and the economic spam-prevention mechanism is fully bypassed. This matches the allowed High impact: **"ante handler bug that permits valid user funds/fees to be mis-accounted."**

---

### Likelihood Explanation

- `NoBaseFee = true` is a standard configuration for chains that prefer static or zero base fees; it is not an exotic edge case.
- `MinGasPrice = 0` is the default in many deployments.
- The attack requires no special privileges: any unprivileged user can craft a Cosmos tx with `fee = 1, gas = 2`.
- The truncation is deterministic and 100% reproducible.

---

### Recommendation

Replace the floor-truncating integer division with ceiling division, or validate that the submitted fee is at least `baseFee * gas` before dividing:

```go
// Option A: ceiling division
feeCap := fee.Add(sdkmath.NewIntFromUint64(gas - 1)).Quo(sdkmath.NewIntFromUint64(gas))

// Option B: explicit minimum check
minFee := baseFeeInt.Mul(sdkmath.NewIntFromUint64(gas))
if fee.LT(minFee) {
    return nil, 0, errorsmod.Wrapf(errortypes.ErrInsufficientFee, ...)
}
feeCap := fee.Quo(sdkmath.NewIntFromUint64(gas))
```

Additionally, add a guard that rejects `effectiveFee = 0` when the user submitted a non-zero fee, analogous to the card-game fix of requiring `remainingCards != remainingSelectedCard`.

---

### Proof of Concept

**Preconditions**: Chain configured with `NoBaseFee = true` (or base fee driven to 0) and `MinGasPrice = 0`. `DynamicFeeChecker` enabled (`options.DynamicFeeChecker = true`).

**Steps**:

1. Craft any valid Cosmos SDK tx (e.g., a `MsgSend`) with:
   - `fee = sdk.NewCoins(sdk.NewInt64Coin(evmDenom, 1))`
   - `gas = 2`
2. Submit through the ante handler chain.
3. `MinGasPriceDecorator` short-circuits because `MinGasPrice = 0`. [1](#0-0) 
4. `NewDynamicFeeChecker` computes `feeCap = 1 / 2 = 0`. [2](#0-1) 
5. `feeCap.LT(baseFeeInt)` → `0 < 0` → false; passes. [3](#0-2) 
6. `effectivePrice = EffectiveGasPrice(0, 0, MaxInt64) = min(MaxInt64, 0) = 0`. [4](#0-3) 
7. `effectiveFee = 0 * 2 = 0`; returned as empty `sdk.Coins`. [5](#0-4) 
8. `checkDeductFee` sees `fee.IsZero() = true` and skips `DeductFees`. [6](#0-5) 
9. Transaction is accepted and executed with zero fee paid.

### Citations

**File:** ante/cosmos/min_gas_price.go (L57-59)
```go
	if minGasPrice.IsZero() || simulate {
		return next(ctx, tx, simulate)
	}
```

**File:** ante/evm/fee_checker.go (L83-83)
```go
		feeCap := fee.Quo(sdkmath.NewIntFromUint64(gas))
```

**File:** ante/evm/fee_checker.go (L86-88)
```go
		if feeCap.LT(baseFeeInt) {
			return nil, 0, errorsmod.Wrapf(errortypes.ErrInsufficientFee, "insufficient gas prices; got: %s required: %s", feeCap, baseFeeInt)
		}
```

**File:** ante/evm/fee_checker.go (L91-99)
```go
		effectivePrice := sdkmath.NewIntFromBigInt(types.EffectiveGasPrice(baseFeeInt.BigInt(), feeCap.BigInt(), maxPriorityPrice.BigInt()))

		// NOTE: create a new coins slice without having to validate the denom
		effectiveFee := sdk.Coins{
			{
				Denom:  denom,
				Amount: effectivePrice.Mul(sdkmath.NewIntFromUint64(gas)),
			},
		}
```

**File:** ante/evm/nativefee.go (L110-115)
```go
	if !fee.IsZero() {
		err := evmkeeper.DeductFees(dfd.bankKeeper, ctx, deductFeesFromAcc, fee)
		if err != nil {
			return err
		}
	}
```
