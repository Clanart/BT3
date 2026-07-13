### Title
Integer Division Truncation in `NewDynamicFeeChecker` Causes `feeCap` to Zero-Out, Allowing Cosmos Transactions to Execute with Zero Fees - (File: `ante/evm/fee_checker.go`)

### Summary

`NewDynamicFeeChecker` derives the per-gas fee cap for Cosmos SDK transactions by integer-dividing the total fee by the gas limit (`feeCap = fee / gas`). When the total fee is smaller than the gas limit, integer truncation produces `feeCap = 0`. On chains where the base fee is also 0 (i.e., `NoBaseFee = true` with London hardfork enabled), the subsequent `feeCap < baseFee` guard passes (0 < 0 is false), `EffectiveGasPrice` resolves to 0, and the returned `effectiveFee` is a zero-amount coin. `DeductFeeDecorator` then skips deduction entirely, so the sender pays nothing.

### Finding Description

In `ante/evm/fee_checker.go`, `NewDynamicFeeChecker` computes the gas price cap as:

```go
feeCap := fee.Quo(sdkmath.NewIntFromUint64(gas))   // line 83 – integer division
```

`sdkmath.Int.Quo` is integer (floor) division. If `fee < gas`, the result is 0.

The guard that follows:

```go
if feeCap.LT(baseFeeInt) {   // line 86
    return nil, 0, errorsmod.Wrapf(...)
}
```

only rejects the transaction when `feeCap < baseFee`. When `baseFee = 0` (the value returned by `types.GetBaseFee` when `NoBaseFee = true` and London is active), the condition `0 < 0` is false and the transaction is not rejected.

`EffectiveGasPrice` is then called with `feeCap = 0` and `baseFee = 0`:

```go
effectivePrice := sdkmath.NewIntFromBigInt(
    types.EffectiveGasPrice(baseFeeInt.BigInt(), feeCap.BigInt(), maxPriorityPrice.BigInt()))
// = min(maxPriorityPrice + 0, 0) = 0
```

The returned `effectiveFee` is:

```go
effectiveFee := sdk.Coins{{Denom: denom, Amount: effectivePrice.Mul(sdkmath.NewIntFromUint64(gas))}}
// = sdk.Coins{{Denom: denom, Amount: 0}}
```

`DeductFeeDecorator.checkDeductFee` then skips deduction because `fee.IsZero()` is true:

```go
if !fee.IsZero() {          // line 110 of nativefee.go
    err := evmkeeper.DeductFees(...)
}
```

The `MinGasPriceDecorator` that runs earlier short-circuits when `MinGasPrice = 0` (the default), so it provides no backstop.

The root cause is the same class as the external report: a scaling factor (`gas`) is divided out before a guard comparison, causing the intermediate value to truncate to 0, which then propagates through all downstream fee accounting.

### Impact Explanation

Any Cosmos SDK transaction (bank send, staking, governance, IBC, etc.) routed through the Cosmos ante handler with `DynamicFeeChecker = true` can be submitted with a nominal fee of 1 aevmos and a large gas limit (e.g., 10 000 000). The `feeCap` truncates to 0, the effective fee is 0, and no funds are transferred to the fee collector. The sender retains their full balance while the transaction executes normally. This constitutes a fee mis-accounting bug in the ante handler: valid fees that must be paid are silently zeroed out, matching the allowed High impact category.

### Likelihood Explanation

The conditions are:
1. London hardfork enabled (standard for any EIP-1559 chain).
2. `NoBaseFee = true` — explicitly documented and used by chains that need zero-price calls; `GetBaseFee` returns `new(big.Int)` (0) rather than `nil` in this case, so the code does **not** fall back to the validator min-gas-price path.
3. `MinGasPrice = 0` — the default value (`DefaultMinGasPrice = sdkmath.LegacyZeroDec()`).

All three conditions hold simultaneously on any chain that ships with default feemarket parameters and enables `NoBaseFee`. The attacker only needs to craft a Cosmos tx where `fee < gas`, which is trivially achievable (e.g., `fee = 1`, `gas = 21000`).

### Recommendation

Perform the multiplication before the division, or avoid the division entirely. The correct approach is to compare the total fee against `baseFee * gas` without first dividing:

```go
// Instead of:
feeCap := fee.Quo(sdkmath.NewIntFromUint64(gas))
if feeCap.LT(baseFeeInt) { ... }

// Use:
requiredFee := baseFeeInt.Mul(sdkmath.NewIntFromUint64(gas))
if fee.LT(requiredFee) { ... }
// Then derive effectivePrice = fee / gas (for the effectiveFee computation),
// but clamp it correctly and ensure effectiveFee = fee when feeCap rounds down.
```

Alternatively, use `sdkmath.LegacyDec` arithmetic (which preserves sub-integer precision) for the `feeCap` derivation, consistent with how `MinGasPriceDecorator` computes `requiredFees` via `gp.Amount.Mul(gasLimit).Ceil().RoundInt()`.

### Proof of Concept

**Setup:** Chain with London hardfork active, `NoBaseFee = true`, `MinGasPrice = 0` (defaults).

**Attack:**
1. Attacker constructs a Cosmos `MsgSend` transaction with `gas = 1_000_000` and `fee = 1 aevmos`.
2. `MinGasPriceDecorator` short-circuits because `MinGasPrice.IsZero()` is true.
3. `NewDynamicFeeChecker` is invoked:
   - `baseFee = types.GetBaseFee(...)` → `new(big.Int)` (0, not nil, because `NoBaseFee=true` with London active).
   - `feeCap = 1 / 1_000_000 = 0` (integer truncation).
   - Guard: `0 < 0` → false → no rejection.
   - `effectivePrice = min(MaxInt64 + 0, 0) = 0`.
   - `effectiveFee = sdk.Coins{{Denom: "aevmos", Amount: 0}}`.
4. `DeductFeeDecorator.checkDeductFee` receives `fee.IsZero() = true` → skips `DeductFees`.
5. Transaction executes; attacker's balance is unchanged; fee collector receives nothing. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4)

### Citations

**File:** ante/evm/fee_checker.go (L79-99)
```go
		gas := feeTx.GetGas()
		feeCoins := feeTx.GetFee()
		fee := feeCoins.AmountOf(denom)

		feeCap := fee.Quo(sdkmath.NewIntFromUint64(gas))
		baseFeeInt := sdkmath.NewIntFromBigInt(baseFee)

		if feeCap.LT(baseFeeInt) {
			return nil, 0, errorsmod.Wrapf(errortypes.ErrInsufficientFee, "insufficient gas prices; got: %s required: %s", feeCap, baseFeeInt)
		}

		// calculate the effective gas price using the EIP-1559 logic.
		effectivePrice := sdkmath.NewIntFromBigInt(types.EffectiveGasPrice(baseFeeInt.BigInt(), feeCap.BigInt(), maxPriorityPrice.BigInt()))

		// NOTE: create a new coins slice without having to validate the denom
		effectiveFee := sdk.Coins{
			{
				Denom:  denom,
				Amount: effectivePrice.Mul(sdkmath.NewIntFromUint64(gas)),
			},
		}
```

**File:** ante/evm/nativefee.go (L109-115)
```go
	// deduct the fees
	if !fee.IsZero() {
		err := evmkeeper.DeductFees(dfd.bankKeeper, ctx, deductFeesFromAcc, fee)
		if err != nil {
			return err
		}
	}
```

**File:** x/evm/types/utils.go (L244-254)
```go
func GetBaseFee(height int64, ethCfg *params.ChainConfig, feemarketParams *feemarkettypes.Params) *big.Int {
	if !IsLondon(ethCfg, height) {
		return nil
	}
	baseFee := feemarketParams.GetBaseFee()
	// should not be nil if london hardfork enabled
	if baseFee == nil {
		return new(big.Int)
	}
	return baseFee
}
```

**File:** x/feemarket/types/params.go (L139-145)
```go
func (p Params) GetBaseFee() *big.Int {
	if p.NoBaseFee {
		return nil
	}

	return p.BaseFee.BigInt()
}
```

**File:** ante/cosmos/min_gas_price.go (L54-59)
```go
	minGasPrice := mpd.feemarketParams.MinGasPrice

	// Short-circuit if min gas price is 0 or if simulating
	if minGasPrice.IsZero() || simulate {
		return next(ctx, tx, simulate)
	}
```
