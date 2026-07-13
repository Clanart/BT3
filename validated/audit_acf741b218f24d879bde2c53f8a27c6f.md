### Title
Integer Division Truncation in `NewDynamicFeeChecker` Allows Zero-Fee Cosmos Transactions When `NoBaseFee = true` - (File: `ante/evm/fee_checker.go`)

---

### Summary

`NewDynamicFeeChecker` computes `feeCap` via integer division of the total fee by gas limit. When the fee amount is smaller than the gas limit (e.g., 1 wei fee, 21 000 gas), the division truncates to zero. When the chain runs with `NoBaseFee = true` (London enabled), the base fee is also zero, so the `feeCap < baseFee` guard passes silently and the returned `effectiveFee` is zero — meaning the Cosmos-SDK fee deduction step charges the sender nothing.

---

### Finding Description

In `ante/evm/fee_checker.go` the `NewDynamicFeeChecker` closure computes the per-gas price cap with integer division:

```go
// ante/evm/fee_checker.go:83
feeCap := fee.Quo(sdkmath.NewIntFromUint64(gas))
```

`sdkmath.Int.Quo` is truncating integer division. Any `fee < gas` produces `feeCap = 0`.

The guard that should reject under-priced transactions is:

```go
// ante/evm/fee_checker.go:86-88
if feeCap.LT(baseFeeInt) {
    return nil, 0, errorsmod.Wrapf(errortypes.ErrInsufficientFee, ...)
}
```

When `NoBaseFee = true`, `x/feemarket/types/params.go` `GetBaseFee()` returns `nil`:

```go
// x/feemarket/types/params.go:139-145
func (p Params) GetBaseFee() *big.Int {
    if p.NoBaseFee {
        return nil
    }
    return p.BaseFee.BigInt()
}
```

`x/evm/types/utils.go` `GetBaseFee()` converts that `nil` to `new(big.Int)` (i.e., 0) so the checker does **not** fall back to the validator min-gas-price path:

```go
// x/evm/types/utils.go:244-254
func GetBaseFee(height int64, ethCfg *params.ChainConfig, feemarketParams *feemarkettypes.Params) *big.Int {
    if !IsLondon(ethCfg, height) {
        return nil
    }
    baseFee := feemarketParams.GetBaseFee()
    if baseFee == nil {
        return new(big.Int)   // ← 0, not nil
    }
    return baseFee
}
```

With `baseFeeInt = 0` and `feeCap = 0`, the comparison `0 < 0` is false — the check passes. The effective price is then:

```go
// ante/evm/fee_checker.go:91-99
effectivePrice := sdkmath.NewIntFromBigInt(
    types.EffectiveGasPrice(baseFeeInt.BigInt(), feeCap.BigInt(), maxPriorityPrice.BigInt()))
// EffectiveGasPrice(0, 0, maxPriorityPrice) = min(maxPriorityPrice+0, 0) = 0

effectiveFee := sdk.Coins{{Denom: denom, Amount: effectivePrice.Mul(sdkmath.NewIntFromUint64(gas))}}
// Amount = 0 * gas = 0
```

The function returns `effectiveFee = {denom: "aevmos", amount: 0}`. The Cosmos SDK `DeductFeeDecorator` uses this returned value to deduct fees from the sender — deducting zero means the sender pays nothing.

The `MinGasPriceDecorator` does not save the situation because it short-circuits when `minGasPrice.IsZero()` (the default):

```go
// ante/cosmos/min_gas_price.go:57-59
if minGasPrice.IsZero() || simulate {
    return next(ctx, tx, simulate)
}
```

---

### Impact Explanation

An unprivileged user can submit a valid Cosmos-SDK transaction (e.g., a `MsgSend` or any wrapped message) with a fee of 1 wei and a gas limit of 21 000 (or any `fee < gas` combination). The ante handler accepts the transaction and the fee deduction step charges 0. This is a fee mis-accounting bug: the chain processes work for free, validators receive no compensation, and the fee market's economic assumptions are violated. This matches the allowed High impact: *"ante handler … bug that permits … valid user funds/fees to be mis-accounted."*

---

### Likelihood Explanation

The condition requires:
1. London hardfork enabled (standard for any EVM-compatible Ethermint chain).
2. `NoBaseFee = true` — a documented, supported configuration explicitly described in the feemarket spec as "needed for 0 price calls."
3. `MinGasPrice = 0` — the default value.

All three conditions are simultaneously present in the default or common "zero-fee" deployment mode. Any unprivileged user can craft the transaction with no special knowledge beyond knowing the gas limit.

---

### Recommendation

Replace the truncating integer division with a check that rejects any fee amount that does not cover at least `baseFee * gas` before computing `feeCap`:

```go
// Reject if total fee is less than baseFee * gas (prevents truncation to zero)
minRequired := baseFeeInt.Mul(sdkmath.NewIntFromUint64(gas))
if fee.LT(minRequired) {
    return nil, 0, errorsmod.Wrapf(errortypes.ErrInsufficientFee, ...)
}
feeCap := fee.Quo(sdkmath.NewIntFromUint64(gas))
```

Alternatively, use `sdkmath.LegacyDec` (decimal) arithmetic for `feeCap` to preserve sub-unit precision before comparing against `baseFeeInt`.

---

### Proof of Concept

**Preconditions:** Chain with London hardfork enabled, `NoBaseFee = true`, `MinGasPrice = 0` (all defaults for a zero-fee Ethermint deployment).

**Attack transaction:** A Cosmos `MsgSend` with:
- `fee_amount = [{denom: "aevmos", amount: "1"}]` (1 wei)
- `gas_limit = 21000`

**Trace through `NewDynamicFeeChecker`:**

```
fee      = 1
gas      = 21000
feeCap   = 1 / 21000 = 0          ← integer truncation (line 83)
baseFee  = new(big.Int) = 0        ← NoBaseFee=true path (utils.go:251)
baseFeeInt = 0

feeCap.LT(baseFeeInt) → 0 < 0 → false  ← guard bypassed (line 86)

effectivePrice = EffectiveGasPrice(0, 0, MaxInt64)
               = min(MaxInt64 + 0, 0) = 0   ← (utils.go:233)

effectiveFee = {denom:"aevmos", amount: 0 * 21000} = {amount: 0}
```

**Result:** `DeductFeeDecorator` deducts 0 coins. The transaction executes with no fee paid. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4)

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

**File:** x/feemarket/types/params.go (L139-145)
```go
func (p Params) GetBaseFee() *big.Int {
	if p.NoBaseFee {
		return nil
	}

	return p.BaseFee.BigInt()
}
```

**File:** x/evm/types/utils.go (L230-234)
```go
// EffectiveGasPrice compute the effective gas price based on eip-1159 rules
// `effectiveGasPrice = min(baseFee + tipCap, feeCap)`
func EffectiveGasPrice(baseFee *big.Int, feeCap *big.Int, tipCap *big.Int) *big.Int {
	return ethermint.BigMin(new(big.Int).Add(tipCap, baseFee), feeCap)
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

**File:** ante/cosmos/min_gas_price.go (L54-59)
```go
	minGasPrice := mpd.feemarketParams.MinGasPrice

	// Short-circuit if min gas price is 0 or if simulating
	if minGasPrice.IsZero() || simulate {
		return next(ctx, tx, simulate)
	}
```
