### Title
Precision Loss in `NewDynamicFeeChecker` Causes Systematic Fee Underpayment for Cosmos Transactions - (File: `ante/evm/fee_checker.go`)

---

### Summary

`NewDynamicFeeChecker` in `ante/evm/fee_checker.go` derives the per-gas fee cap from the declared total fee using integer (floor) division. The effective fee subsequently deducted from the sender is `floor(fee/gas) * gas`, which is strictly less than the declared fee whenever `fee mod gas ≠ 0`. The fee collector is systematically under-credited, and the sender retains the remainder without any protocol check preventing it.

---

### Finding Description

In `NewDynamicFeeChecker`, the per-gas fee cap for a Cosmos SDK transaction is computed at line 83:

```go
feeCap := fee.Quo(sdkmath.NewIntFromUint64(gas))   // integer (floor) division
```

`sdkmath.Int.Quo` performs truncating integer division. The effective fee is then:

```go
effectiveFee := sdk.Coins{{
    Denom:  denom,
    Amount: effectivePrice.Mul(sdkmath.NewIntFromUint64(gas)),  // line 97
}}
```

where `effectivePrice = min(baseFee + tip, feeCap)`. Because `feeCap = ⌊fee/gas⌋`, we have:

```
effectiveFee = effectivePrice × gas ≤ feeCap × gas = ⌊fee/gas⌋ × gas ≤ fee
```

The gap `fee − effectiveFee = fee mod gas` can be as large as `gas − 1` atomphoton. This `effectiveFee` is what `DeductFeeDecorator.checkDeductFee` passes to `evmkeeper.DeductFees`, so only `effectiveFee` is transferred to the fee collector; the remainder stays in the sender's account with no further check.

The baseFee admission check at line 86 is:

```go
if feeCap.LT(baseFeeInt) { ... }   // feeCap = ⌊fee/gas⌋
```

This check is correct (it passes iff `fee ≥ baseFee × gas`), so the precision loss does **not** allow under-baseFee transactions to slip through. The sole effect is that the amount actually deducted is `⌊fee/gas⌋ × gas` rather than `fee`. [1](#0-0) 

The `DeductFeeDecorator` in `ante/evm/nativefee.go` deducts exactly the `effectiveFee` returned by the checker: [2](#0-1) 

`DeductFees` then sends only that amount to the fee collector: [3](#0-2) 

This checker is wired into both the standard Cosmos ante handler and the legacy EIP-712 ante handler: [4](#0-3) 

---

### Impact Explanation

Every Cosmos SDK transaction processed through `NewDynamicFeeChecker` where `fee mod gas ≠ 0` results in the fee collector receiving `fee mod gas` fewer atomphoton than the sender declared. The maximum shortfall per transaction is `gas − 1` atomphoton. This is a systematic, deterministic fee mis-accounting: the sender's declared fee is not fully deducted, and the fee collector (and therefore validators/stakers) receive less than the protocol intends. This matches the allowed High impact: *"ante handler … bug that permits … valid user funds/fees to be mis-accounted."*

---

### Likelihood Explanation

The condition `fee mod gas ≠ 0` is satisfied by virtually every real transaction, because users set fees as round numbers (e.g., `1000000 atomphoton`) while gas limits are independent integers. The bug is triggered on every Cosmos SDK transaction that uses the dynamic fee checker with EIP-1559 enabled (London hardfork active), which is the default production configuration.

---

### Recommendation

Replace the truncating division with ceiling division so that `feeCap × gas ≥ fee`:

```go
// Before (truncates remainder):
feeCap := fee.Quo(sdkmath.NewIntFromUint64(gas))

// After (ceiling division):
feeCap := fee.Add(sdkmath.NewIntFromUint64(gas - 1)).Quo(sdkmath.NewIntFromUint64(gas))
```

Alternatively, skip the round-trip conversion entirely and deduct the full declared `fee` when `effectivePrice` equals `feeCap` (i.e., when the sender is willing to pay their full declared amount).

---

### Proof of Concept

**Given:**
- `baseFee = 10` atomphoton/gas
- `gas = 100`
- `fee = 1099` atomphoton (declared by sender)
- `maxPriorityPrice = MaxInt64` (no `ExtensionOptionDynamicFeeTx`)

**Step-by-step:**

1. `feeCap = ⌊1099 / 100⌋ = 10`
2. Admission check: `10 < 10` → false → transaction accepted
3. `effectivePrice = min(10 + MaxInt64, 10) = 10`
4. `effectiveFee = 10 × 100 = 1000`
5. `DeductFees` transfers `1000` atomphoton to fee collector
6. Sender retains `1099 − 1000 = 99` atomphoton that should have been collected

**Expected:** fee collector receives `1099` atomphoton (or at minimum `baseFee × gas = 1000`; the declared surplus `99` should not silently remain with the sender without an explicit refund mechanism).

**Actual:** fee collector receives `1000` atomphoton; sender keeps `99` atomphoton with no protocol record of the discrepancy.

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

**File:** ante/evm/nativefee.go (L57-66)
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
	}
```

**File:** x/evm/keeper/utils.go (L207-216)
```go
func DeductFees(bankKeeper types.BankKeeper, ctx sdk.Context, acc sdk.AccountI, fees sdk.Coins) error {
	if !fees.IsValid() {
		return errorsmod.Wrapf(errortypes.ErrInsufficientFee, "invalid fee amount: %s", fees)
	}
	if ctx.BlockHeight() > 0 {
		if err := bankKeeper.SendCoinsFromAccountToModuleVirtual(ctx, acc.GetAddress(), authtypes.FeeCollectorName, fees); err != nil {
			return errorsmod.Wrap(errortypes.ErrInsufficientFunds, err.Error())
		}
	}
	return nil
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
