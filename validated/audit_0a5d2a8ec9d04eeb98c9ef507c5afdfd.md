### Title
Integer Truncation in `NewDynamicFeeChecker` Allows Cosmos Transactions to Execute with Zero Effective Fee When `baseFee = 0` - (File: `ante/evm/fee_checker.go`)

### Summary

`NewDynamicFeeChecker` computes the per-gas fee cap via truncating integer division `feeCap = fee / gas`. When `baseFee = 0` (NoBaseFee mode with London hardfork enabled) and the user's total fee is less than the gas limit, `feeCap` truncates to 0. The subsequent `feeCap < baseFeeInt` guard passes (0 < 0 is false), `effectivePrice` resolves to 0, and `effectiveFee = 0` is returned to `DeductFeeDecorator`, which then skips deduction entirely. A user who satisfies `MinGasPriceDecorator` with a fractional minimum gas price can execute Cosmos transactions for free.

### Finding Description

In `ante/evm/fee_checker.go`, `NewDynamicFeeChecker` computes:

```go
feeCap := fee.Quo(sdkmath.NewIntFromUint64(gas))   // line 83 — truncating integer division
baseFeeInt := sdkmath.NewIntFromBigInt(baseFee)

if feeCap.LT(baseFeeInt) {                          // line 86
    return nil, 0, errorsmod.Wrapf(...)
}

effectivePrice := sdkmath.NewIntFromBigInt(
    types.EffectiveGasPrice(baseFeeInt.BigInt(), feeCap.BigInt(), maxPriorityPrice.BigInt()))

effectiveFee := sdk.Coins{{
    Denom:  denom,
    Amount: effectivePrice.Mul(sdkmath.NewIntFromUint64(gas)),  // line 97
}}
return effectiveFee, priority, nil
``` [1](#0-0) 

`EffectiveGasPrice` is `min(tipCap + baseFee, feeCap)`:

```go
func EffectiveGasPrice(baseFee *big.Int, feeCap *big.Int, tipCap *big.Int) *big.Int {
    return ethermint.BigMin(new(big.Int).Add(tipCap, baseFee), feeCap)
}
``` [2](#0-1) 

When `baseFee = 0` and `feeCap = 0`, `EffectiveGasPrice = min(tipCap + 0, 0) = 0`, so `effectiveFee = 0 * gas = 0`.

`DeductFeeDecorator` then skips deduction entirely:

```go
fee, priority, err = dfd.txFeeChecker(ctx, tx)   // returns effectiveFee = 0
...
if !fee.IsZero() {
    err := evmkeeper.DeductFees(...)              // never reached
}
``` [3](#0-2) 

`baseFee = 0` is reached when London hardfork is enabled but `feemarketParams.GetBaseFee()` returns 0 or nil. `GetBaseFee` in `x/evm/types/utils.go` converts a nil result to `new(big.Int)` (zero):

```go
baseFee := feemarketParams.GetBaseFee()
if baseFee == nil {
    return new(big.Int)   // zero
}
return baseFee
``` [4](#0-3) 

The `MinGasPriceDecorator` runs before `DeductFeeDecorator` and uses `Ceil()` to compute required fees, so it correctly enforces a non-zero fee. However, it checks the raw fee coins provided by the user, not the `effectiveFee` that will actually be deducted:

```go
fee := gp.Amount.Mul(gasLimit).Ceil().RoundInt()
if fee.IsPositive() {
    requiredFees = requiredFees.Add(sdk.Coin{Denom: gp.Denom, Amount: fee})
}
if !feeCoins.IsAnyGTE(requiredFees) {
    return ctx, errorsmod.Wrapf(errortypes.ErrInsufficientFee, ...)
}
``` [5](#0-4) 

The two decorators operate on different fee values: `MinGasPriceDecorator` validates the raw tx fee, while `DeductFeeDecorator` deducts the `effectiveFee` returned by `txFeeChecker`. This mismatch is the root cause.

The `NewDynamicFeeChecker` is wired into both `newCosmosAnteHandler` and `newLegacyCosmosAnteHandlerEip712` when `options.DynamicFeeChecker = true`: [6](#0-5) 

### Impact Explanation

When `baseFee = 0` (NoBaseFee mode with London hardfork enabled) and `MinGasPrice` is a fractional value less than 1 per gas unit, an attacker can submit Cosmos transactions that pass `MinGasPriceDecorator` but have `effectiveFee = 0` deducted. The fee collector module receives nothing. This is a direct mis-accounting of user funds/fees: the chain's fee enforcement is bypassed for every Cosmos transaction, allowing unlimited free execution of arbitrary Cosmos messages (bank sends, governance votes, IBC relays, etc.).

This matches the allowed High impact: **"ante handler bug that permits valid user funds/fees to be mis-accounted."**

### Likelihood Explanation

The condition requires:
1. London hardfork enabled (standard for EIP-1559 chains)
2. `NoBaseFee = true` OR `BaseFee = 0` in feemarket params — a valid configuration for chains that want a static minimum price without dynamic base fee adjustment
3. `MinGasPrice` set to a fractional value (e.g., `0.5` aevmos/gas), which is common when the EVM denom has 18 decimals and operators set human-readable prices
4. `DynamicFeeChecker = true` in ante handler options

Conditions 1, 2, and 4 are co-present in any chain that enables the dynamic fee checker with NoBaseFee mode. Condition 3 is common in practice. The attacker needs only to craft a Cosmos tx with `fee < gas` while satisfying `MinGasPriceDecorator`.

### Recommendation

Replace the truncating integer division with a ceiling division or a direct comparison against the minimum required fee before computing `effectiveFee`:

```go
// Option A: reject if feeCap rounds to zero but baseFee is also zero
if feeCap.IsZero() && baseFeeInt.IsZero() {
    // effectiveFee would be 0; check raw fee against MinGasPrice separately
    return nil, 0, errorsmod.Wrapf(errortypes.ErrInsufficientFee,
        "fee per gas rounds to zero: fee %s, gas %d", fee, gas)
}
```

Or, more robustly, ensure `DeductFeeDecorator` deducts the **larger** of `effectiveFee` and the raw fee coins when `effectiveFee < rawFee`, or validate that `effectiveFee` is non-zero whenever `MinGasPrice > 0`.

### Proof of Concept

**Setup:**
- `NoBaseFee = true`, London hardfork enabled → `baseFee = 0`
- `MinGasPrice = 0.5` aevmos/gas
- `DynamicFeeChecker = true`

**Attack:**
1. Attacker submits a Cosmos `MsgSend` with `gas = 21000`, `fee = 10500 aevmos`
2. `MinGasPriceDecorator`: `required = ceil(0.5 × 21000) = 10500`; `10500 >= 10500` → **passes**
3. `NewDynamicFeeChecker`:
   - `feeCap = 10500 / 21000 = 0` (truncated)
   - `baseFeeInt = 0`
   - `0 < 0` → false → **passes**
   - `effectivePrice = min(MaxInt64 + 0, 0) = 0`
   - `effectiveFee = 0 × 21000 = 0`
4. `DeductFeeDecorator`: `fee.IsZero()` → true → **no deduction**
5. Transaction executes; attacker's balance is unchanged; fee collector receives 0.

Repeating this for every Cosmos transaction on the chain results in complete fee bypass.

### Citations

**File:** ante/evm/fee_checker.go (L83-99)
```go
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

**File:** ante/evm/nativefee.go (L57-115)
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

	newCtx := ctx.WithPriority(priority)

	return next(newCtx, tx, simulate)
}

func (dfd DeductFeeDecorator) checkDeductFee(ctx sdk.Context, sdkTx sdk.Tx, fee sdk.Coins) error {
	feeTx, ok := sdkTx.(sdk.FeeTx)
	if !ok {
		return errorsmod.Wrap(sdkerrors.ErrTxDecode, "Tx must be a FeeTx")
	}

	if addr := dfd.accountKeeper.GetModuleAddress(types.FeeCollectorName); addr == nil {
		return fmt.Errorf("fee collector module account (%s) has not been set", types.FeeCollectorName)
	}

	feePayer := feeTx.FeePayer()
	feeGranter := feeTx.FeeGranter()
	deductFeesFrom := feePayer

	// if feegranter set deduct fee from feegranter account.
	// this works with only when feegrant enabled.
	if feeGranter != nil {
		feeGranterAddr := sdk.AccAddress(feeGranter)

		if dfd.feegrantKeeper == nil {
			return sdkerrors.ErrInvalidRequest.Wrap("fee grants are not enabled")
		} else if !bytes.Equal(feeGranterAddr, feePayer) {
			err := dfd.feegrantKeeper.UseGrantedFees(ctx, feeGranterAddr, feePayer, fee, sdkTx.GetMsgs())
			if err != nil {
				return errorsmod.Wrapf(err, "%s does not allow to pay fees for %s", feeGranter, feePayer)
			}
		}

		deductFeesFrom = feeGranterAddr
	}

	deductFeesFromAcc := dfd.accountKeeper.GetAccount(ctx, deductFeesFrom)
	if deductFeesFromAcc == nil {
		return sdkerrors.ErrUnknownAddress.Wrapf("fee payer address: %s does not exist", deductFeesFrom)
	}

	// deduct the fees
	if !fee.IsZero() {
		err := evmkeeper.DeductFees(dfd.bankKeeper, ctx, deductFeesFromAcc, fee)
		if err != nil {
			return err
		}
	}
```

**File:** ante/cosmos/min_gas_price.go (L76-88)
```go
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
