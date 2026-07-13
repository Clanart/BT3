### Title
Integer Truncation in `NewDynamicFeeChecker` Allows Zero-Fee Cosmos Transactions When `baseFee = 0` - (File: `ante/evm/fee_checker.go`)

### Summary
`NewDynamicFeeChecker` computes the per-gas fee cap via integer division (`fee / gas`). When the declared fee is smaller than the gas limit, the quotient truncates to zero. If `baseFee = 0` (i.e., `NoBaseFee = true` with London hardfork active), the zero-feeCap passes the base-fee guard, the effective price collapses to zero, and the `DeductFeeDecorator` deducts nothing — the Cosmos transaction is committed with no fee paid.

### Finding Description

In `NewDynamicFeeChecker` (`ante/evm/fee_checker.go`):

```go
// line 83
feeCap := fee.Quo(sdkmath.NewIntFromUint64(gas))   // integer division — truncates to 0 when fee < gas
baseFeeInt := sdkmath.NewIntFromBigInt(baseFee)

// line 86 — guard passes when baseFee == 0 because 0 < 0 is false
if feeCap.LT(baseFeeInt) {
    return nil, 0, errorsmod.Wrapf(errortypes.ErrInsufficientFee, ...)
}

// line 91 — EffectiveGasPrice = min(tipCap + 0, 0) = 0
effectivePrice := sdkmath.NewIntFromBigInt(types.EffectiveGasPrice(baseFeeInt.BigInt(), feeCap.BigInt(), maxPriorityPrice.BigInt()))

// line 94-99 — effectiveFee = 0 * gas = 0
effectiveFee := sdk.Coins{{Denom: denom, Amount: effectivePrice.Mul(sdkmath.NewIntFromUint64(gas))}}
``` [1](#0-0) 

`DeductFeeDecorator.checkDeductFee` then skips deduction entirely because `fee.IsZero()` is true:

```go
// ante/evm/nativefee.go line 110
if !fee.IsZero() {
    err := evmkeeper.DeductFees(dfd.bankKeeper, ctx, deductFeesFromAcc, fee)
``` [2](#0-1) 

The `MinGasPriceDecorator` that runs earlier uses `Ceil()` and checks the *declared* fee coins against the required minimum, so a user who declares `fee = 1 aphoton` can satisfy that guard while the `NewDynamicFeeChecker` still returns `effectiveFee = 0`: [3](#0-2) 

The Cosmos ante-handler chain wires both decorators in sequence: [4](#0-3) 

### Impact Explanation
When `NoBaseFee = true` (base fee forced to 0) and `DynamicFeeChecker = true`, any unprivileged user can submit Cosmos SDK transactions (governance votes, staking, IBC, etc.) with a declared fee of as little as 1 aphoton and have **zero fees actually deducted**. The ante handler admits and commits these transactions without charging the sender. This is a fee-market ante handler bug that permits transactions with insufficient effective fees to commit and causes user-declared fees to be mis-accounted (declared but never collected).

### Likelihood Explanation
Requires three conditions that are individually common and can co-occur:
1. **London hardfork enabled** — standard for any EIP-1559 chain.
2. **`NoBaseFee = true`** — explicitly documented as the configuration "needed for 0 price calls"; chains that want fixed or zero base fees use this.
3. **`DynamicFeeChecker = true`** — the intended setting for EIP-1559 Cosmos-tx fee checking.

The attacker-controlled input is trivial: submit any Cosmos tx with `fee < gas` in the EVM denom (e.g., `fee = 1 aphoton`, `gas = 100 000`).

### Recommendation
Replace the integer-division `feeCap` computation with decimal arithmetic (matching the `Ceil()` approach used in `MinGasPriceDecorator`), or add an explicit guard that rejects the transaction when `feeCap` rounds to zero but the declared fee is non-zero:

```go
// Use LegacyDec to preserve sub-unit precision
feeCapDec := sdkmath.LegacyNewDecFromInt(fee).QuoInt64(int64(gas))
if feeCapDec.LT(sdkmath.LegacyNewDecFromBigInt(baseFee)) {
    return nil, 0, errorsmod.Wrapf(errortypes.ErrInsufficientFee, ...)
}
```

Alternatively, add a post-truncation check: if `feeCap.IsZero()` and `fee.IsPositive()`, reject the transaction.

### Proof of Concept

**Setup**: chain with London hardfork active, `NoBaseFee = true`, `DynamicFeeChecker = true`, `MinGasPrice = 0` (default).

**Attack**:
1. Attacker builds a Cosmos tx (e.g., `MsgVote`) with `fee = 1 aphoton`, `gas = 100 000`.
2. `MinGasPriceDecorator`: `minGasPrice = 0` → short-circuits, passes.
3. `NewDynamicFeeChecker`:
   - `fee = 1`, `gas = 100 000`
   - `feeCap = 1 / 100 000 = 0` (integer truncation) [5](#0-4) 
   - `baseFeeInt = 0` (because `NoBaseFee = true`)
   - Guard: `0 < 0` → **false**, no rejection [6](#0-5) 
   - `effectivePrice = EffectiveGasPrice(0, 0, MaxInt64) = min(MaxInt64, 0) = 0` [7](#0-6) 
   - `effectiveFee = 0 × 100 000 = 0` [8](#0-7) 
4. `DeductFeeDecorator`: `fee.IsZero()` → **true**, deduction skipped. [2](#0-1) 
5. Transaction is committed; attacker's balance is unchanged; 0 fees collected by the fee collector.

The attacker can repeat this indefinitely to spam the chain with zero-cost Cosmos transactions.

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

**File:** ante/evm/nativefee.go (L110-115)
```go
	if !fee.IsZero() {
		err := evmkeeper.DeductFees(dfd.bankKeeper, ctx, deductFeesFromAcc, fee)
		if err != nil {
			return err
		}
	}
```

**File:** ante/cosmos/min_gas_price.go (L74-88)
```go
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

**File:** evmd/ante/handler_options.go (L196-202)
```go
		ante.NewValidateBasicDecorator(),
		ante.NewTxTimeoutHeightDecorator(),
		cosmos.NewMinGasPriceDecorator(options.FeeMarketKeeper, evmDenom, &feemarketParams),
		ante.NewValidateMemoDecorator(options.AccountKeeper),
		ante.NewConsumeGasForTxSizeDecorator(options.AccountKeeper),
		ante.NewDeductFeeDecorator(options.AccountKeeper, options.BankKeeper, options.FeegrantKeeper, txFeeChecker),
		// SetPubKeyDecorator must be called before all signature verification decorators
```
