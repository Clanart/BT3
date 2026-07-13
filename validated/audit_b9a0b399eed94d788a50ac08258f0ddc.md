### Title
Cosmos Tx with `ExtensionOptionDynamicFeeTx` and `MaxPriorityPrice=0` Bypasses `MinGasPrice` Enforcement, Paying Only `baseFee*gas` — (`evmd/ante/handler_options.go`, `ante/evm/fee_checker.go`, `ante/cosmos/min_gas_price.go`)

### Summary

When `MinGasPrice > 0` and `baseFee < MinGasPrice`, an unprivileged submitter can craft a Cosmos (non-EVM) transaction that attaches `ExtensionOptionDynamicFeeTx` with `MaxPriorityPrice = 0` and a declared fee of exactly `minGasPrice * gas`. `MinGasPriceDecorator` passes because it checks the **declared** fee. `NewDynamicFeeChecker` (called inside `DeductFeeDecorator`) then computes `effectiveFee = baseFee * gas` because `effectivePrice = min(baseFee + 0, feeCap) = baseFee`. The result is that only `baseFee * gas` is deducted from the sender and credited to the fee collector — violating the `MinGasPrice` invariant for every such transaction.

---

### Finding Description

**Decorator chain for Cosmos txs** (`newCosmosAnteHandler`, `evmd/ante/handler_options.go:178–212`):

```
RejectMessagesDecorator          // rejects MsgEthereumTx only
NewExtensionOptionsDecorator     // accepts ExtensionOptionDynamicFeeTx (checker = HasDynamicFeeExtensionOption)
...
MinGasPriceDecorator             // checks declared feeCoins >= ceil(minGasPrice * gas)
...
ante.NewDeductFeeDecorator(... , txFeeChecker=NewDynamicFeeChecker)
```

**`MinGasPriceDecorator`** reads `feeTx.GetFee()` (the declared fee field) and compares it to `ceil(minGasPrice * gas)`: [1](#0-0) 

It passes as long as the declared fee ≥ `minGasPrice * gas`. It does **not** look at `ExtensionOptionDynamicFeeTx` or compute an effective price.

**`NewDynamicFeeChecker`** is the `txFeeChecker` wired into `ante.NewDeductFeeDecorator`: [2](#0-1) 

When `ExtensionOptionDynamicFeeTx` is present with `MaxPriorityPrice = 0`, the checker reads it at line 69 and sets `maxPriorityPrice = 0`. It then computes:

```
effectivePrice = EffectiveGasPrice(baseFee, feeCap, 0)
               = min(baseFee + 0, feeCap)
               = baseFee          (since feeCap = declared_fee/gas ≥ baseFee)
effectiveFee   = baseFee * gas
``` [3](#0-2) 

`DeductFeeDecorator` then deducts this `effectiveFee` — not the declared fee: [4](#0-3) 

**`ante.NewDeductFeeDecorator`** (Cosmos SDK import, not the vendored one) is used in `newCosmosAnteHandler`: [5](#0-4) 

The `ExtensionOptionChecker` is `ethermint.HasDynamicFeeExtensionOption` (set in `evmd/app.go:793`), which returns `true` for `ExtensionOptionDynamicFeeTx`, so the extension option is accepted by `NewExtensionOptionsDecorator` and the tx proceeds through the Cosmos ante chain. [6](#0-5) 

---

### Impact Explanation

The `MinGasPrice` parameter is a chain-wide floor price enforced on all transactions. With `MinGasPrice = 2 * baseFee`:

| Step | Value |
|---|---|
| Declared fee (passes `MinGasPriceDecorator`) | `2 * baseFee * gas` |
| `feeCap` computed by checker | `2 * baseFee` |
| `effectivePrice = min(baseFee + 0, 2*baseFee)` | `baseFee` |
| **Actually deducted** | `baseFee * gas` |
| **Expected minimum** | `2 * baseFee * gas` |

The fee collector receives `baseFee * gas` instead of `minGasPrice * gas`. The attacker saves `(minGasPrice − baseFee) * gas` per transaction. This directly violates the "High" impact category: **ante handler bug that permits valid user funds/fees to be mis-accounted**.

---

### Likelihood Explanation

- Requires only: `MinGasPrice > 0` AND `baseFee < MinGasPrice` — both are normal operating conditions on any chain that sets a minimum gas price floor above the current base fee.
- No special privileges, governance access, or validator collusion needed.
- The attacker simply constructs a standard Cosmos SDK tx, adds `ExtensionOptionDynamicFeeTx` with `MaxPriorityPrice = 0`, and sets the declared fee to exactly `ceil(minGasPrice * gas)`.
- Repeatable on every block.

---

### Recommendation

`MinGasPriceDecorator` must be made aware of `ExtensionOptionDynamicFeeTx`. When the extension option is present, it should compute and check the **effective fee** (using `EffectiveGasPrice(baseFee, feeCap, maxPriorityPrice)`) rather than the declared fee. Alternatively, `NewDynamicFeeChecker` should enforce that `effectivePrice >= minGasPrice` and return an error if not, so the `DeductFeeDecorator` itself rejects the tx before deduction.

---

### Proof of Concept

```
Preconditions:
  baseFee       = 1_000_000_000   (1 Gwei)
  MinGasPrice   = 2_000_000_000   (2 Gwei)
  gas           = 100_000

Attack tx:
  msgs          = [MsgSend{...}]
  fee           = 200_000_000_000_000  (= 2 Gwei * 100_000, passes MinGasPriceDecorator)
  extension     = ExtensionOptionDynamicFeeTx{ MaxPriorityPrice: 0 }

Trace:
  MinGasPriceDecorator:
    requiredFees = ceil(2e9 * 100_000) = 200_000_000_000_000
    feeCoins     = 200_000_000_000_000  ✓ passes

  NewDynamicFeeChecker:
    feeCap        = 200_000_000_000_000 / 100_000 = 2_000_000_000
    maxPriority   = 0
    effectivePrice = min(1e9 + 0, 2e9) = 1_000_000_000
    effectiveFee  = 1e9 * 100_000 = 100_000_000_000_000

  DeductFeeDecorator deducts: 100_000_000_000_000
  Expected minimum:           200_000_000_000_000

  fee_collector_delta = 100_000_000_000_000  (half of required minimum)
```

Integration test assertion: `assert fee_collector_delta == baseFee * gas` (not `minGasPrice * gas`), confirming the invariant is broken.

### Citations

**File:** ante/cosmos/min_gas_price.go (L67-88)
```go
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

**File:** ante/evm/fee_checker.go (L62-108)
```go
		// default to `MaxInt64` when there's no extension option.
		maxPriorityPrice := sdkmath.NewInt(math.MaxInt64)

		// get the priority tip cap from the extension option.
		if hasExtOptsTx, ok := tx.(authante.HasExtensionOptionsTx); ok {
			for _, opt := range hasExtOptsTx.GetExtensionOptions() {
				if extOpt, ok := opt.GetCachedValue().(*ethermint.ExtensionOptionDynamicFeeTx); ok {
					maxPriorityPrice = extOpt.MaxPriorityPrice
					break
				}
			}
		}

		if maxPriorityPrice.Sign() == -1 {
			return nil, 0, errorsmod.Wrapf(errortypes.ErrInsufficientFee, "priority fee is negative")
		}

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

		bigPriority := effectivePrice.Sub(baseFeeInt).Quo(types.DefaultPriorityReduction)
		priority := int64(math.MaxInt64)

		if bigPriority.IsInt64() {
			priority = bigPriority.Int64()
		}

		return effectiveFee, priority, nil
```

**File:** x/evm/types/utils.go (L232-234)
```go
func EffectiveGasPrice(baseFee *big.Int, feeCap *big.Int, tipCap *big.Int) *big.Int {
	return ethermint.BigMin(new(big.Int).Add(tipCap, baseFee), feeCap)
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

**File:** evmd/app.go (L793-794)
```go
		ExtensionOptionChecker: ethermint.HasDynamicFeeExtensionOption,
		DynamicFeeChecker:      true,
```
