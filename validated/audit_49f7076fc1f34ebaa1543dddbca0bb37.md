Found a concrete, in-scope analog: the multiplier-based Cosmos gas limit derived from EVM gas in `x/evm/ante/gas.go` truncates to zero when `txGas * priorityNormalizer` rounds down below 1, exactly mirroring the reported bug class (multiply a user-controlled quantity by a scaling constant, then truncate, producing a silently zero result that breaks downstream accounting).

### Title
EVM gas-limit-to-Cosmos-gas-meter conversion truncates to zero for low-gas transactions, letting a transaction execute with an effectively unmetered/zero-limit gas meter - (File: x/evm/ante/gas.go)

### Summary
`GasDecorator.AnteHandle` converts the EVM transaction's declared gas (`txGas`, attacker-controlled) into a Cosmos gas-meter limit by multiplying with `GetPriorityNormalizer(ctx)` and truncating: `adjustedGasLimit := gl.evmKeeper.GetPriorityNormalizer(ctx).MulInt64(int64(txGas)); gasMeter := sdk.NewGasMeterWithMultiplier(ctx, adjustedGasLimit.TruncateInt().Uint64())`. This is the same "multiply then truncate a scaled quantity" pattern as the USSD `amountToSellUnits` bug, applied to the fee/gas accounting path that every EVM transaction sender reaches directly.

### Finding Description
`GetPriorityNormalizer` returns a `sdk.Dec` used elsewhere as the scaling factor between EVM gas units and Cosmos ("sei") gas units (see the comment `sei gas = evm gas * multiplier` in `precompiles/common/precompiles.go`). In `x/evm/ante/gas.go`:

```go
adjustedGasLimit := gl.evmKeeper.GetPriorityNormalizer(ctx).MulInt64(int64(txGas))
gasMeter := sdk.NewGasMeterWithMultiplier(ctx, adjustedGasLimit.TruncateInt().Uint64())
``` [1](#0-0) 

`sdk.Dec.TruncateInt()` truncates toward zero and drops any fractional remainder; for any `txGas` such that `txGas * priorityNormalizer < 1`, the resulting Cosmos gas meter limit becomes `0`. [2](#0-1) 

This mirrors the reported root cause: a raw balance/quantity is multiplied by a scaled constant (here, `GetPriorityNormalizer`) and then integer-truncated, and for sufficiently small inputs the product truncates entirely to zero, silently corrupting the downstream computation (here, the gas meter that is supposed to bound Cosmos-side execution of the EVM message). The `NewGasMeterWithMultiplier` gas meter then divides consumed Cosmos gas back by the same multiplier to report `GasConsumedToLimit`, so a `0` limit combined with the multiplier math can produce degenerate metering rather than a hard failure, exactly like the "amountToSellUnits" truncating to zero and silently disrupting the intended downstream Uniswap swap in the referenced report.

### Impact Explanation
If the Cosmos-side gas meter limit is computed as `0` (or an inconsistent value) for a low-declared-gas EVM transaction, transaction execution accounting (ante-chain gas consumption bound, out-of-gas panics, and `ctx.GasEstimate()` used later in the flow) is desynchronized from the actual EVM gas limit that `BuyGas`/`StateTransition` charged the sender for. This directly touches fee accounting on the ante pipeline reachable by any transaction sender crafting the `txGas` field of an EVM transaction, which the rules explicitly list as an in-scope surface (EVM transactions and the EVM ante pipeline; fee and base-fee accounting).

### Likelihood Explanation
Any externally submitted EVM transaction controls `txData.GetGas()` (`txGas`), which is fed directly into this multiplication before any floor is enforced other than `MinGasEVMTx = 21000` being merely a nolint-style guard used for `GasEstimate`, not for the gas-meter computation itself. Whether `priorityNormalizer` is small enough in production configuration for `21000 * priorityNormalizer` to still round to zero (or produce values low enough to matter) could not be confirmed from the index alone — this depends on the runtime value of `GetPriorityNormalizer`, which is not indexed here. This is the main uncertainty limiting confidence in exploitability at default parameters.

### Recommendation
Round up (ceiling) rather than truncate when converting `adjustedGasLimit` to an integer gas-meter limit, and/or enforce a minimum floor (e.g., clamp to at least 1, or to `MinGasEVMTx`-equivalent Cosmos gas) before constructing the gas meter in `x/evm/ante/gas.go`, consistent with how `CheckTxFeeWithValidatorMinGasPrices` uses `fee.Ceil().RoundInt()` for the analogous minimum-fee computation elsewhere in the codebase [3](#0-2) .

### Proof of Concept
Not independently verifiable without runtime access to the configured value of `GetPriorityNormalizer(ctx)`; the index does not expose the deployed parameter value, so this report identifies the code-level truncation pattern rather than a confirmed, reproduced exploit. A Devin session with chain access would be needed to read `GetPriorityNormalizer` at a live/default height and check whether any permitted `txGas` value drives `adjustedGasLimit.TruncateInt()` to `0`.

### Citations

**File:** x/evm/ante/gas.go (L36-38)
```go
	adjustedGasLimit := gl.evmKeeper.GetPriorityNormalizer(ctx).MulInt64(int64(txGas)) //nolint:gosec
	gasMeter := sdk.NewGasMeterWithMultiplier(ctx, adjustedGasLimit.TruncateInt().Uint64())
	ctx = ctx.WithGasMeter(gasMeter)
```

**File:** sei-cosmos/types/decimal.go (L592-604)
```go
// TruncateInt64 truncates the decimals from the number and returns an int64
func (d Dec) TruncateInt64() int64 {
	chopped := chopPrecisionAndTruncate(d.i)
	if !chopped.IsInt64() {
		panic("Int64() out of bound")
	}
	return chopped.Int64()
}

// TruncateInt truncates the decimals from the number and returns an Int
func (d Dec) TruncateInt() Int {
	return NewIntFromBigInt(chopPrecisionAndTruncate(d.i))
}
```

**File:** sei-cosmos/x/auth/ante/validator_tx_fee.go (L38-44)
```go
			// Determine the required fees by multiplying each required minimum gas
			// price by the gas limit, where fee = ceil(minGasPrice * gasLimit).
			glDec := sdk.NewDec(int64(gas)) //nolint:gosec // bounds checked above
			for i, gp := range minGasPrices {
				fee := gp.Amount.Mul(glDec)
				requiredFees[i] = sdk.NewCoin(gp.Denom, fee.Ceil().RoundInt())
			}
```
