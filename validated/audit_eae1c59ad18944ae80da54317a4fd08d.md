### Title
Integer Division Truncation in `NewDynamicFeeChecker` Enables Complete Fee Bypass for Cosmos SDK Transactions — (`ante/evm/fee_checker.go`)

### Summary

In `NewDynamicFeeChecker`, the per-gas fee cap is derived via integer division: `feeCap = fee / gas`. When `fee < gas`, this truncates to zero. If `baseFee = 0` (reachable when `NoBaseFee = true`, or when `MinGasPrice` is a fractional `LegacyDec` value whose `TruncateInt()` is 0, allowing `CalculateBaseFee` to drive `baseFee` to 0), the `effectiveFee` returned to `DeductFeeDecorator` is zero. A user can therefore pass the `MinGasPriceDecorator` check by submitting the required minimum fee amount, yet have zero fees actually deducted — a complete fee bypass for Cosmos SDK transactions.

### Finding Description

**Root cause — integer division in `feeCap` computation:**

In `NewDynamicFeeChecker`:

```go
feeCap := fee.Quo(sdkmath.NewIntFromUint64(gas))   // integer division
```

`sdkmath.Int.Quo` is integer (floor) division. When `fee < gas`, `feeCap = 0`. [1](#0-0) 

The guard that follows only rejects the transaction if `feeCap < baseFee`:

```go
if feeCap.LT(baseFeeInt) {
    return nil, 0, errorsmod.Wrapf(errortypes.ErrInsufficientFee, ...)
}
``` [2](#0-1) 

When `baseFee = 0`, the condition `0 < 0` is false, so the check passes silently.

The effective fee is then computed as:

```go
effectivePrice := sdkmath.NewIntFromBigInt(types.EffectiveGasPrice(baseFeeInt.BigInt(), feeCap.BigInt(), maxPriorityPrice.BigInt()))
effectiveFee := sdk.Coins{{Denom: denom, Amount: effectivePrice.Mul(sdkmath.NewIntFromUint64(gas))}}
``` [3](#0-2) 

`EffectiveGasPrice = min(baseFee + tip, feeCap)`. With `feeCap = 0` and `baseFee = 0`, `effectivePrice = 0`, so `effectiveFee = 0 * gas = 0`. [4](#0-3) 

`DeductFeeDecorator` uses the `TxFeeChecker`-returned value as the actual deduction amount, so 0 coins are deducted from the sender.

**How `baseFee` reaches 0:**

`CalculateBaseFee` uses `TruncateInt()` when applying `MinGasPrice` as the lower bound:

```go
minGasPrice := params.MinGasPrice.TruncateInt().BigInt()
return ethermint.BigMax(x.Sub(parentBaseFee, baseFeeDelta), minGasPrice)
``` [5](#0-4) 

`MinGasPrice` is a `LegacyDec` (decimal). If governance sets `MinGasPrice = 0.5`, then `TruncateInt() = 0`, and the effective lower bound for `baseFee` is 0 — allowing `baseFee` to decrease to 0 over time on a low-activity chain. The `MinGasPriceDecorator` and `CheckEthMinGasPrice`, however, use the full decimal value (0.5) for their checks, creating a discrepancy. [6](#0-5) 

Alternatively, when `NoBaseFee = true`, `baseFee = 0` unconditionally.

**The bypass path (Cosmos SDK tx with `ExtensionOptionDynamicFeeTx`):**

`NewDynamicFeeChecker` is wired as the `txFeeChecker` in `DeductFeeDecorator` when `DynamicFeeChecker: true` (the default in `evmd/app.go`): [7](#0-6) [8](#0-7) 

The ante chain for such transactions includes `MinGasPriceDecorator` before `DeductFeeDecorator`. `MinGasPriceDecorator` checks `fee >= ceil(minGasPrice * gas)` using the full decimal, so it requires a non-zero fee. But `NewDynamicFeeChecker` then truncates `feeCap` to 0 and returns `effectiveFee = 0`, which is what `DeductFeeDecorator` actually deducts.

### Impact Explanation

Any unprivileged user can submit a Cosmos SDK transaction (with `ExtensionOptionDynamicFeeTx`) and pay zero fees despite the `MinGasPriceDecorator` requiring a non-zero minimum fee. The protocol collects no fees for these transactions. This is a direct mis-accounting of user funds/fees through the ante handler, matching the High impact category: *"ante handler... bug that permits... valid user funds/fees to be mis-accounted."*

### Likelihood Explanation

The conditions are:
1. `baseFee = 0` — reachable via `NoBaseFee = true` (governance) or via the `TruncateInt()` lower-bound bug when `MinGasPrice < 1`.
2. `MinGasPrice > 0` but fractional (e.g., `0.5`) — a valid `LegacyDec` governance-settable value.
3. Attacker submits `fee < gas` — trivially achievable (e.g., `fee = gas - 1`).

Both parameters are governance-settable. A fractional `MinGasPrice` (e.g., `0.5 aphoton/gas`) is a natural configuration on chains where the EVM denom has high denomination. Once conditions are met, any user can exploit this on every Cosmos SDK transaction.

### Recommendation

Replace the floor-division `feeCap` computation with ceiling division, or validate that `effectiveFee >= ceil(minGasPrice * gas)` before returning from `NewDynamicFeeChecker`. Additionally, `CalculateBaseFee` should use the full decimal `MinGasPrice` (not `TruncateInt()`) as the lower bound for `baseFee`, or enforce that `MinGasPrice` must be an integer value at the parameter validation layer.

### Proof of Concept

**Setup:** Chain with `NoBaseFee = true`, `MinGasPrice = 0.5`.

**Attack:**
1. Construct a Cosmos SDK tx with `gas = 21000`, `fee = 10500` (passes `MinGasPriceDecorator`: `ceil(0.5 * 21000) = 10500 ≤ 10500`).
2. In `NewDynamicFeeChecker`: `feeCap = 10500 / 21000 = 0` (integer division truncation).
3. `baseFeeInt = 0` (NoBaseFee=true).
4. Guard: `0 < 0` → false → passes.
5. `effectivePrice = min(0 + maxPriorityPrice, 0) = 0`.
6. `effectiveFee = 0 * 21000 = 0`.
7. `DeductFeeDecorator` deducts 0 coins.
8. User pays **zero fees** despite the minimum gas price requirement.

### Citations

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

**File:** x/evm/types/utils.go (L232-234)
```go
func EffectiveGasPrice(baseFee *big.Int, feeCap *big.Int, tipCap *big.Int) *big.Int {
	return ethermint.BigMin(new(big.Int).Add(tipCap, baseFee), feeCap)
}
```

**File:** x/feemarket/keeper/eip1559.go (L103-104)
```go
	minGasPrice := params.MinGasPrice.TruncateInt().BigInt()
	return ethermint.BigMax(x.Sub(parentBaseFee, baseFeeDelta), minGasPrice)
```

**File:** ante/cosmos/min_gas_price.go (L76-81)
```go
	for _, gp := range minGasPrices {
		fee := gp.Amount.Mul(gasLimit).Ceil().RoundInt()
		if fee.IsPositive() {
			requiredFees = requiredFees.Add(sdk.Coin{Denom: gp.Denom, Amount: fee})
		}
	}
```

**File:** evmd/app.go (L793-795)
```go
		ExtensionOptionChecker: ethermint.HasDynamicFeeExtensionOption,
		DynamicFeeChecker:      true,
		DisabledAuthzMsgs: []string{
```

**File:** evmd/ante/evm_handler.go (L36-38)
```go
	if options.DynamicFeeChecker {
		txFeeChecker = evm.NewDynamicFeeChecker(ethCfg, &evmParams, &feemarketParams)
	}
```
