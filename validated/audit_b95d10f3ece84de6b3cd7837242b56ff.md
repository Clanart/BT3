### Title
Division-Before-Multiplication in `NewDynamicFeeChecker` Causes Systematic Fee Under-Deduction for Cosmos SDK Transactions - (File: `ante/evm/fee_checker.go`)

### Summary
`NewDynamicFeeChecker` derives `feeCap` by integer-dividing the total fee by gas (`fee / gas`), then later multiplies `effectivePrice * gas` to produce `effectiveFee`. The integer truncation in the first step permanently discards up to `gas - 1` atoms of fee per transaction. The returned `effectiveFee` is what `DeductFeeDecorator` actually deducts from the user's account, so the remainder is never collected by the protocol.

### Finding Description

In `NewDynamicFeeChecker`:

```go
// ante/evm/fee_checker.go line 83
feeCap := fee.Quo(sdkmath.NewIntFromUint64(gas))   // ① integer division — truncates remainder
...
effectivePrice := sdkmath.NewIntFromBigInt(
    types.EffectiveGasPrice(baseFeeInt.BigInt(), feeCap.BigInt(), maxPriorityPrice.BigInt()),
)
effectiveFee := sdk.Coins{{
    Denom:  denom,
    Amount: effectivePrice.Mul(sdkmath.NewIntFromUint64(gas)),  // ② multiply back
}}
return effectiveFee, priority, nil
``` [1](#0-0) 

Step ① computes `feeCap = floor(fee / gas)`, discarding `fee mod gas` (0 to `gas-1` atoms). Step ② reconstructs `effectiveFee = effectivePrice * gas ≤ feeCap * gas = fee − (fee mod gas)`. The remainder is silently dropped.

`DeductFeeDecorator.AnteHandle` overwrites its local `fee` variable with the return value of `txFeeChecker` and then calls `checkDeductFee` with that value:

```go
// ante/evm/nativefee.go lines 57-64
fee := feeTx.GetFee()
if !simulate {
    fee, priority, err = dfd.txFeeChecker(ctx, tx)  // fee replaced with effectiveFee
    ...
}
if err := dfd.checkDeductFee(ctx, tx, fee); err != nil { ... }
``` [2](#0-1) 

`checkDeductFee` calls `evmkeeper.DeductFees(dfd.bankKeeper, ctx, deductFeesFromAcc, fee)` with the truncated `effectiveFee`, not the original `fee` the user specified. [3](#0-2) 

This checker is wired into both `newCosmosAnteHandler` and `newLegacyCosmosAnteHandlerEip712` when `options.DynamicFeeChecker == true`: [4](#0-3) 

### Impact Explanation

Every Cosmos SDK transaction (including legacy EIP-712 Cosmos txs) processed through the dynamic fee path systematically under-pays fees by up to `gas - 1` atoms per transaction. For a transaction with `gas = 2,000,000`, the maximum per-transaction shortfall is 1,999,999 atoms of the EVM denom. The protocol's fee collector receives less than the fee the user committed to, constituting a fee mis-accounting on every such transaction. This matches the allowed High impact: *"ante handler bug that permits valid user funds/fees to be mis-accounted."*

### Likelihood Explanation

The path is triggered by any unprivileged Cosmos SDK transaction submitted to a chain with `DynamicFeeChecker: true` and London hardfork enabled. No special privileges or conditions are required. The precision loss is deterministic and occurs on every transaction where `fee mod gas ≠ 0`, which is the common case when wallets compute fees as `gasPrice * gasLimit + tip` with a non-zero remainder.

### Recommendation

Avoid the intermediate per-gas division. Instead, compute the effective fee directly from the total fee without first converting to a per-gas price:

```go
// Correct order: multiply first, divide last
// Keep feeCap only for the baseFee comparison check, but compute
// effectiveFee without re-multiplying a truncated quotient.
// One approach: if effectivePrice == feeCap (tip-capped case), use
// the original fee coins rather than effectivePrice * gas.
```

Concretely, after computing `effectivePrice`, if `effectivePrice.Equal(feeCap)` (i.e., the tip cap is the binding constraint), return the original `fee` coins rather than `effectivePrice.Mul(gas)`, so the full user-specified amount is deducted. Alternatively, restructure to avoid the `fee / gas` division entirely and work in total-fee space throughout.

### Proof of Concept

**Setup:** London hardfork enabled, `baseFee = 1000`, `gas = 21000`, no `ExtensionOptionDynamicFeeTx` (so `maxPriorityPrice = MaxInt64`).

**User submits:** `fee = 1000 * 21000 + 20999 = 21,020,999` atoms (a valid fee slightly above the minimum).

**Step ①:** `feeCap = floor(21,020,999 / 21000) = floor(1001.0) = 1001`

**Step ②:** `effectivePrice = min(baseFee + MaxInt64, feeCap) = 1001`

**effectiveFee:** `1001 * 21000 = 21,021,000`

Wait — in this case `effectiveFee > fee`. Let me redo with a remainder:

**User submits:** `fee = 1000 * 21000 + 500 = 21,000,500` atoms.

**Step ①:** `feeCap = floor(21,000,500 / 21000) = floor(1000.023...) = 1000`

**Step ②:** `effectivePrice = min(baseFee + MaxInt64, feeCap) = min(..., 1000) = 1000`

**effectiveFee:** `1000 * 21000 = 21,000,000` atoms deducted.

**Shortfall:** `21,000,500 − 21,000,000 = 500` atoms never collected. The user specified 500 atoms more than the minimum but pays exactly the minimum. The 500-atom remainder is silently discarded on every such transaction. [5](#0-4)

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
