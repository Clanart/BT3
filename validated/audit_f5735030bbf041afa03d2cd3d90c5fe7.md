[1](#0-0)

### Citations

**File:** ante/evm/fee_checker.go (L60-101)
```go
	baseFee := feemarketParams.BaseFee
	// if baseFee is nil because it is disabled
	// or not found, consider it as 0
	// so the DynamicFeeTx logic can be applied
	if baseFee.IsNil() {
		baseFee = sdkmath.LegacyZeroDec()
	}

	// default to `MaxInt64` when there's no extension option.
	maxPriorityPrice := sdkmath.LegacyNewDec(math.MaxInt64)

	// get the priority tip cap from the extension option.
	if hasExtOptsTx, ok := feeTx.(authante.HasExtensionOptionsTx); ok {
		for _, opt := range hasExtOptsTx.GetExtensionOptions() {
			if extOpt, ok := opt.GetCachedValue().(*cosmosevmtypes.ExtensionOptionDynamicFeeTx); ok {
				maxPriorityPrice = extOpt.MaxPriorityPrice
				if maxPriorityPrice.IsNil() {
					maxPriorityPrice = sdkmath.LegacyZeroDec()
				}
				break
			}
		}
	}

	// priority fee cannot be negative
	if maxPriorityPrice.IsNegative() {
		return nil, 0, errorsmod.Wrapf(errortypes.ErrInsufficientFee, "max priority price cannot be negative")
	}

	gas := sdkmath.NewIntFromUint64(feeTx.GetGas())

	if gas.IsZero() {
		return nil, 0, errorsmod.Wrap(errortypes.ErrInvalidRequest, "gas cannot be zero")
	}

	feeCoins := feeTx.GetFee()
	feeAmtDec := sdkmath.LegacyNewDecFromInt(feeCoins.AmountOfNoDenomValidation(denom))

	feeCap := feeAmtDec.QuoInt(gas)
	if feeCap.LT(baseFee) {
		return nil, 0, errorsmod.Wrapf(errortypes.ErrInsufficientFee, "gas prices too low, got: %s%s required: %s%s. Please retry using a higher gas price or a higher fee", feeCap, denom, baseFee, denom)
	}
```
