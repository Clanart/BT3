### Title
Integer Division Truncation in `NewDynamicFeeChecker` Allows Zero-Fee Cosmos Transactions When `NoBaseFee=true` - (File: `ante/evm/fee_checker.go`)

### Summary

`NewDynamicFeeChecker` computes `feeCap` via integer division of the submitted fee amount by the gas limit. When the submitted fee (in the EVM denom) is smaller than the gas limit, the division truncates to zero. If the chain is configured with `NoBaseFee=true` and the London hardfork enabled, `baseFeeInt` is also zero, the `feeCap >= baseFeeInt` check passes, and `effectivePrice` collapses to zero — causing the `DeductFeeDecorator` to deduct nothing from the sender.

### Finding Description

In `ante/evm/fee_checker.go` line 83, `feeCap` is computed as:

```go
feeCap := fee.Quo(sdkmath.NewIntFromUint64(gas))
```

`fee` is `sdkmath.Int` (integer), `gas` is `uint64`. `sdkmath.Int.Quo` performs truncating integer division. If `fee < gas` (e.g., fee = 999 aphoton, gas = 1000), `feeCap` becomes 0.

The guard at line 86 is:

```go
if feeCap.LT(baseFeeInt) {
    return nil, 0, errorsmod.Wrapf(...)
}
```

`baseFeeInt` is derived from `types.GetBaseFee` (`x/evm/types/utils.go` lines 244–254). When `NoBaseFee=true` and London is enabled, `feemarketParams.GetBaseFee()` returns `nil` (per `x/feemarket/types/params.go` lines 139–144), so `GetBaseFee` returns `new(big.Int)` — i.e., **zero, not nil**. The nil-check at line 57 does not trigger the fallback path. `baseFeeInt` is therefore 0.

With `feeCap=0` and `baseFeeInt=0`, the check `0 < 0` is false — the transaction passes.

Then at line 91:

```go
effectivePrice := sdkmath.NewIntFromBigInt(types.EffectiveGasPrice(baseFeeInt.BigInt(), feeCap.BigInt(), maxPriorityPrice.BigInt()))
```

`EffectiveGasPrice` (`x/evm/types/utils.go` line 232–234) computes `min(tipCap + baseFee, feeCap)` = `min(MaxInt64 + 0, 0)` = **0**.

At lines 94–99:

```go
effectiveFee := sdk.Coins{{Denom: denom, Amount: effectivePrice.Mul(sdkmath.NewIntFromUint64(gas))}}
```

`effectiveFee.Amount = 0 * gas = 0`.

The `DeductFeeDecorator.checkDeductFee` in `ante/evm/nativefee.go` line 110 only deducts if `!fee.IsZero()`. With `effectiveFee` carrying a zero amount, `IsZero()` is true and **no fees are deducted**.

The `MinGasPriceDecorator` (`ante/cosmos/min_gas_price.go` lines 57–59) short-circuits when `MinGasPrice.IsZero()`, which is the default (`DefaultMinGasPrice = sdkmath.LegacyZeroDec()`), providing no protection.

### Impact Explanation

Any unprivileged user can submit a Cosmos SDK transaction (e.g., `MsgSend`, `MsgDelegate`, any Cosmos message) with a fee amount smaller than the gas limit in the EVM denom and pay **zero fees** on a chain with `NoBaseFee=true` and London hardfork enabled. The fee collector module receives nothing. This is a direct mis-accounting of user fees in the ante handler, matching the High impact class: *"ante handler... bug that permits... valid user funds/fees to be mis-accounted."*

### Likelihood Explanation

`NoBaseFee=true` is a documented, governance-settable parameter explicitly described as "needed for 0 price calls." Chains that enable it (e.g., for testing, zero-fee environments, or specific deployment configurations) while keeping London enabled are directly vulnerable. The default `MinGasPrice=0` removes the only other guard. The attack requires no privileges — any user can craft a Cosmos tx with `fee=1, gas=1000000`.

### Recommendation

Replace the truncating integer division with a ceiling division or a decimal-based comparison to avoid truncation to zero:

```go
// Instead of:
feeCap := fee.Quo(sdkmath.NewIntFromUint64(gas))

// Use ceiling division:
gasInt := sdkmath.NewIntFromUint64(gas)
feeCap := fee.Add(gasInt).Sub(sdkmath.OneInt()).Quo(gasInt)
// or equivalently, reject if fee < baseFee * gas:
if fee.LT(baseFeeInt.Mul(gasInt)) {
    return nil, 0, errorsmod.Wrapf(...)
}
```

Alternatively, add a guard: if `feeCap` is zero but `fee` is non-zero, reject the transaction rather than allowing it to proceed with zero effective fee.

### Proof of Concept

**Chain configuration**: `NoBaseFee=true`, London hardfork enabled, `MinGasPrice=0` (all reachable via governance or genesis).

**Attacker submits a Cosmos SDK tx**:
- `fee = sdk.NewCoins(sdk.NewCoin("aphoton", sdkmath.NewInt(1)))`
- `gas = 1_000_000`

**Trace through `NewDynamicFeeChecker`**:

1. `baseFee = types.GetBaseFee(...)` → `new(big.Int)` (zero, not nil, because London is enabled but `NoBaseFee=true`) [1](#0-0) 
2. nil-check at line 57 does not trigger fallback [2](#0-1) 
3. `fee = 1`, `gas = 1_000_000` → `feeCap = 1 / 1_000_000 = 0` (integer truncation) [3](#0-2) 
4. `baseFeeInt = 0`; check `0 < 0` → false → passes [4](#0-3) 
5. `effectivePrice = EffectiveGasPrice(0, 0, MaxInt64) = min(MaxInt64, 0) = 0` [5](#0-4) 
6. `effectiveFee = 0 * 1_000_000 = 0` [6](#0-5) 
7. `DeductFeeDecorator`: `fee.IsZero()` → true → `DeductFees` not called → **0 aphoton deducted** [7](#0-6) 

The transaction executes with zero fee cost to the sender. The `MinGasPriceDecorator` is bypassed because `MinGasPrice=0` causes an early return. [8](#0-7)

### Citations

**File:** x/evm/types/utils.go (L232-234)
```go
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

**File:** ante/evm/fee_checker.go (L56-60)
```go
		baseFee := types.GetBaseFee(ctx.BlockHeight(), ethCfg, feemarketParams)
		if baseFee == nil {
			// london hardfork is not enabled: fallback to min-gas-prices logic
			return checkTxFeeWithValidatorMinGasPrices(ctx, feeTx)
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

**File:** ante/evm/fee_checker.go (L94-99)
```go
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

**File:** ante/cosmos/min_gas_price.go (L57-59)
```go
	if minGasPrice.IsZero() || simulate {
		return next(ctx, tx, simulate)
	}
```
