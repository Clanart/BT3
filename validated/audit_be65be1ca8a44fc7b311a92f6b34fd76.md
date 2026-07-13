### Title
`MinGasPrice.TruncateInt()` Precision Loss Silently Zeros the Base-Fee Floor, Enabling Zero-Fee Cosmos Transactions via `NewDynamicFeeChecker` Integer Division - (File: `x/feemarket/keeper/eip1559.go`)

---

### Summary

Two compounding integer-truncation bugs allow any unprivileged user to submit Cosmos SDK transactions that pass the `MinGasPriceDecorator` fee check yet have **zero fees actually deducted** from their account. The root cause is structurally identical to the external report: a decimal value is silently truncated to zero by integer division, causing a downstream accounting value to collapse to zero.

---

### Finding Description

**Bug 1 — `MinGasPrice.TruncateInt()` in `CalculateBaseFee`**

`x/feemarket/keeper/eip1559.go` line 103 converts the `MinGasPrice` parameter (a `sdkmath.LegacyDec`) to an integer floor before using it as the base-fee lower bound:

```go
// x/feemarket/keeper/eip1559.go
minGasPrice := params.MinGasPrice.TruncateInt().BigInt()   // ← precision loss
return ethermint.BigMax(x.Sub(parentBaseFee, baseFeeDelta), minGasPrice)
```

`validateMinGasPrice` only requires the value to be non-negative; it accepts any fractional value such as `0.5`. When `0 < MinGasPrice < 1`, `TruncateInt()` returns `0`, so the effective floor is `0` instead of `MinGasPrice`. The base fee is then free to decrease all the way to `0` during low-traffic periods. `BeginBlock` stores this zero value via `k.SetBaseFee(ctx, baseFee)`.

**Bug 2 — Integer division `feeCap = fee / gas` in `NewDynamicFeeChecker`**

`ante/evm/fee_checker.go` line 83 derives the per-gas price cap by integer division:

```go
// ante/evm/fee_checker.go
feeCap := fee.Quo(sdkmath.NewIntFromUint64(gas))   // ← integer truncation
baseFeeInt := sdkmath.NewIntFromBigInt(baseFee)

if feeCap.LT(baseFeeInt) { ... }   // passes when both are 0

effectivePrice := sdkmath.NewIntFromBigInt(
    types.EffectiveGasPrice(baseFeeInt.BigInt(), feeCap.BigInt(), maxPriorityPrice.BigInt()))
// EffectiveGasPrice = min(baseFee + tip, feeCap) = min(0 + MaxInt64, 0) = 0

effectiveFee := sdk.Coins{{Denom: denom,
    Amount: effectivePrice.Mul(sdkmath.NewIntFromUint64(gas))}}  // 0 * gas = 0
```

When `fee < gas` (e.g., `fee = 10500`, `gas = 21000`), `feeCap = 0`. With `baseFee = 0` (from Bug 1), the guard `feeCap.LT(baseFeeInt)` passes (`0 < 0` is false). `EffectiveGasPrice(0, 0, MaxInt64) = min(MaxInt64, 0) = 0`, so `effectiveFee = 0`.

**`DeductFeeDecorator` deducts the `effectiveFee` returned by the checker, not the original tx fee:**

```go
// ante/evm/nativefee.go
fee, priority, err = dfd.txFeeChecker(ctx, tx)   // returns effectiveFee = 0
// ...
if !fee.IsZero() {
    err := evmkeeper.DeductFees(dfd.bankKeeper, ctx, deductFeesFromAcc, fee)
}
// fee.IsZero() == true → nothing deducted
```

**The `MinGasPriceDecorator` runs earlier in the chain and correctly validates the raw tx fee, but the `DeductFeeDecorator` ignores that validated amount and deducts only the `effectiveFee = 0`.**

The ante handler chain for Cosmos txs (`newCosmosAnteHandler`) is:

```
MinGasPriceDecorator  →  ...  →  DeductFeeDecorator(NewDynamicFeeChecker)
```

`MinGasPriceDecorator` uses `minGasPrice` as a full `LegacyDec` (no truncation), so it correctly requires `fee ≥ ceil(0.5 × gas)`. But `NewDynamicFeeChecker` then computes `effectiveFee = 0` and `DeductFeeDecorator` deducts nothing.

---

### Impact Explanation

Any user can submit Cosmos SDK transactions with **zero fees deducted** even when `MinGasPrice > 0`. Fee collectors (validators/distribution module) receive nothing for these transactions. This is a direct fee mis-accounting bug in the ante handler path, matching the allowed High impact: *"ante handler, mempool, or proposal handling bug that permits valid user funds/fees to be mis-accounted."*

---

### Likelihood Explanation

- `MinGasPrice` is a governance parameter. `validateMinGasPrice` accepts any non-negative `LegacyDec`, including fractional values. A governance proposal setting `MinGasPrice = 0.5` is valid and passes all on-chain validation.
- `DynamicFeeChecker = true` is the intended production configuration (it is the EIP-1559 fee checker for Cosmos txs).
- Once `MinGasPrice` is fractional and the base fee has dropped to 0 (which happens naturally during low-traffic periods since the floor is silently 0), **every Cosmos tx with `fee < gas`** exploits this path. The condition `fee < gas` is trivially satisfied for any tx where the per-gas price is less than 1 (i.e., sub-wei pricing), which is the entire point of setting `MinGasPrice < 1`.

---

### Recommendation

1. **Fix `CalculateBaseFee`**: Use the full decimal value as the floor, not the truncated integer:
   ```go
   // x/feemarket/keeper/eip1559.go
   minGasPrice := params.MinGasPrice.Ceil().TruncateInt().BigInt()
   // or: enforce MinGasPrice >= 1 in validateMinGasPrice
   ```

2. **Fix `NewDynamicFeeChecker`**: Compute `feeCap` using decimal arithmetic to avoid truncation to zero:
   ```go
   // ante/evm/fee_checker.go
   feeCapDec := sdkmath.LegacyNewDecFromInt(fee).Quo(
       sdkmath.LegacyNewDecFromInt(sdkmath.NewIntFromUint64(gas)))
   feeCap := feeCapDec.Ceil().TruncateInt()
   ```
   Or add a guard: if `feeCap.IsZero() && fee.IsPositive()`, set `feeCap = sdkmath.OneInt()`.

3. **Add validation**: `validateMinGasPrice` should reject fractional values below 1 if the integer-truncation behavior is intentional, or document that `MinGasPrice` must be an integer.

---

### Proof of Concept

**Setup**: Governance sets `MinGasPrice = 0.5` (passes `validateMinGasPrice`). `DynamicFeeChecker = true`. Blocks are below gas target for several blocks → `CalculateBaseFee` computes `BigMax(parentBaseFee - delta, TruncateInt(0.5)) = BigMax(parentBaseFee - delta, 0)` → base fee reaches 0 and is stored via `SetBaseFee`.

**Attack tx**: Cosmos `MsgSend` with `gas = 21000`, `fee = 10500 aevmos`.

**Step-by-step**:

```
MinGasPriceDecorator:
  minGasPrice = 0.5 (LegacyDec, not truncated)
  required = ceil(0.5 × 21000) = 10500
  feeCoins = 10500 aevmos ≥ 10500 → PASS

NewDynamicFeeChecker (inside DeductFeeDecorator):
  baseFee = feemarketParams.BaseFee = 0  (stored by BeginBlock)
  feeCap  = 10500 / 21000 = 0           (integer division)
  feeCap(0) < baseFeeInt(0) → false → PASS guard
  effectivePrice = EffectiveGasPrice(0, 0, MaxInt64)
                 = min(0 + MaxInt64, 0) = 0
  effectiveFee   = 0 × 21000 = 0

DeductFeeDecorator:
  fee = effectiveFee = 0
  fee.IsZero() == true → DeductFees NOT called
  User account: 10500 aevmos NOT deducted
```

**Result**: Transaction executes successfully. User pays 0 fees. Fee collector receives 0.

---

**Relevant code locations:** [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4) [6](#0-5) [7](#0-6) [8](#0-7)

### Citations

**File:** x/feemarket/keeper/eip1559.go (L101-104)
```go
	// Set global min gas price as lower bound of the base fee, transactions below
	// the min gas price don't even reach the mempool.
	minGasPrice := params.MinGasPrice.TruncateInt().BigInt()
	return ethermint.BigMax(x.Sub(parentBaseFee, baseFeeDelta), minGasPrice)
```

**File:** x/feemarket/keeper/abci.go (L30-38)
```go
func (k *Keeper) BeginBlock(ctx sdk.Context) error {
	baseFee := k.CalculateBaseFee(ctx)

	// return immediately if base fee is nil
	if baseFee == nil {
		return nil
	}

	k.SetBaseFee(ctx, baseFee)
```

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

**File:** ante/evm/nativefee.go (L57-65)
```go
	fee := feeTx.GetFee()
	if !simulate {
		fee, priority, err = dfd.txFeeChecker(ctx, tx)
		if err != nil {
			return ctx, err
		}
	}
	if err := dfd.checkDeductFee(ctx, tx, fee); err != nil {
		return ctx, err
```

**File:** ante/cosmos/min_gas_price.go (L54-88)
```go
	minGasPrice := mpd.feemarketParams.MinGasPrice

	// Short-circuit if min gas price is 0 or if simulating
	if minGasPrice.IsZero() || simulate {
		return next(ctx, tx, simulate)
	}
	minGasPrices := sdk.DecCoins{
		{
			Denom:  mpd.evmDenom,
			Amount: minGasPrice,
		},
	}

	feeCoins := feeTx.GetFee()
	gas := feeTx.GetGas()

	requiredFees := make(sdk.Coins, 0)

	// Determine the required fees by multiplying each required minimum gas
	// price by the gas limit, where fee = ceil(minGasPrice * gasLimit).
	gasLimit := sdkmath.LegacyNewDecFromBigInt(new(big.Int).SetUint64(gas))

	for _, gp := range minGasPrices {
		fee := gp.Amount.Mul(gasLimit).Ceil().RoundInt()
		if fee.IsPositive() {
			requiredFees = requiredFees.Add(sdk.Coin{Denom: gp.Denom, Amount: fee})
		}
	}

	if !feeCoins.IsAnyGTE(requiredFees) {
		return ctx, errorsmod.Wrapf(errortypes.ErrInsufficientFee,
			"provided fee < minimum global fee (%s < %s). Please increase the gas price.",
			feeCoins,
			requiredFees)
	}
```

**File:** x/feemarket/types/params.go (L147-163)
```go
func validateMinGasPrice(i interface{}) error {
	v, ok := i.(sdkmath.LegacyDec)

	if !ok {
		return fmt.Errorf("invalid parameter type: %T", i)
	}

	if v.IsNil() {
		return fmt.Errorf("invalid parameter: nil")
	}

	if v.IsNegative() {
		return fmt.Errorf("value cannot be negative: %s", i)
	}

	return nil
}
```

**File:** evmd/ante/handler_options.go (L185-201)
```go
	var txFeeChecker ante.TxFeeChecker
	if options.DynamicFeeChecker {
		txFeeChecker = evm.NewDynamicFeeChecker(ethCfg, &evmParams, &feemarketParams)
	}
	decorators := make([]sdk.AnteDecorator, 0, 16+len(extra))
	decorators = append(decorators,
		cosmos.RejectMessagesDecorator{}, // reject MsgEthereumTxs
		// disable the Msg types that cannot be included on an authz.MsgExec msgs field
		cosmos.NewAuthzLimiterDecorator(options.DisabledAuthzMsgs),
		ante.NewSetUpContextDecorator(),
		ante.NewExtensionOptionsDecorator(options.ExtensionOptionChecker),
		ante.NewValidateBasicDecorator(),
		ante.NewTxTimeoutHeightDecorator(),
		cosmos.NewMinGasPriceDecorator(options.FeeMarketKeeper, evmDenom, &feemarketParams),
		ante.NewValidateMemoDecorator(options.AccountKeeper),
		ante.NewConsumeGasForTxSizeDecorator(options.AccountKeeper),
		ante.NewDeductFeeDecorator(options.AccountKeeper, options.BankKeeper, options.FeegrantKeeper, txFeeChecker),
```

**File:** x/evm/types/utils.go (L230-234)
```go
// EffectiveGasPrice compute the effective gas price based on eip-1159 rules
// `effectiveGasPrice = min(baseFee + tipCap, feeCap)`
func EffectiveGasPrice(baseFee *big.Int, feeCap *big.Int, tipCap *big.Int) *big.Int {
	return ethermint.BigMin(new(big.Int).Add(tipCap, baseFee), feeCap)
}
```
