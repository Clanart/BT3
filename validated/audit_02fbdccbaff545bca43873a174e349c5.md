### Title
Legacy EIP-712 `AuthInfo.Fee.Payer` Not Validated Against `ExtensionOptionsWeb3Tx.FeePayer` Enables Unauthorized Fee Theft — (File: `ante/cosmos/eip712.go`)

---

### Summary

In the legacy EIP-712 Cosmos transaction ante handler, the fee deduction decorator uses `AuthInfo.Fee.Payer` to determine who pays the transaction fee, while the EIP-712 signature verification only validates `ExtensionOptionsWeb3Tx.FeePayer`. No check enforces that these two fields match. An unprivileged attacker can set `AuthInfo.Fee.Payer` to any victim address and have the victim's funds deducted as fees, while the attacker's own EIP-712 signature (over a typed data containing `fee.feePayer = attacker_address`) passes verification.

---

### Finding Description

The legacy EIP-712 ante handler chain is assembled in `evmd/ante/evm_handler.go`:

```
authante.NewDeductFeeDecorator(...)          // step 9 — deducts from AuthInfo.Fee.Payer
...
cosmos.NewLegacyEip712SigVerificationDecorator(...)  // step 13 — verifies ExtensionOptionsWeb3Tx.FeePayer
``` [1](#0-0) 

**Fee deduction path** (`authante.NewDeductFeeDecorator`): calls `feeTx.FeePayer()`, which returns `AuthInfo.Fee.Payer` when that field is non-empty, and deducts the fee from that address with no further authorization check. [2](#0-1) 

**Signature verification path** (`VerifySignature`): reads `extOpt.FeePayer` from `ExtensionOptionsWeb3Tx`, injects it as `fee.feePayer` into the EIP-712 typed data, and verifies the signature against that address. It never reads or compares `AuthInfo.Fee.Payer`. [3](#0-2) 

The EIP-712 typed data is constructed in `LegacyWrapTxToTypedData` by patching `feeDelegation.FeePayer` into the fee object — this value comes exclusively from `extOpt.FeePayer`, not from `AuthInfo.Fee.Payer`: [4](#0-3) 

The two fields are

### Citations

**File:** evmd/ante/evm_handler.go (L50-57)
```go
		authante.NewDeductFeeDecorator(options.AccountKeeper, options.BankKeeper, options.FeegrantKeeper, txFeeChecker),
		// SetPubKeyDecorator must be called before all signature verification decorators
		authante.NewSetPubKeyDecorator(options.AccountKeeper),
		authante.NewValidateSigCountDecorator(options.AccountKeeper),
		authante.NewSigGasConsumeDecorator(options.AccountKeeper, options.SigGasConsumer),
		// Note: signature verification uses EIP instead of the cosmos signature validator
		cosmos.NewLegacyEip712SigVerificationDecorator(options.AccountKeeper, options.SignModeHandler),
		authante.NewIncrementSequenceDecorator(options.AccountKeeper),
```

**File:** ante/evm/nativefee.go (L83-115)
```go
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

**File:** ante/cosmos/eip712.go (L232-248)
```go
		if len(extOpt.FeePayer) == 0 {
			return errorsmod.Wrap(errortypes.ErrUnknownExtensionOptions, "no feePayer on ExtensionOptionsWeb3Tx")
		}
		feePayer, err := sdk.AccAddressFromBech32(extOpt.FeePayer)
		if err != nil {
			return errorsmod.Wrap(err, "failed to parse feePayer from ExtensionOptionsWeb3Tx")
		}

		feeDelegation := &eip712.FeeDelegationOptions{
			FeePayer: feePayer,
		}

		if err := eip712.LegacyValidatePayloadMessages(msgs); err != nil {
			return errorsmod.Wrap(err, "failed to validate payload messages")
		}

		typedData, err := eip712.LegacyWrapTxToTypedData(ethermintCodec, extOpt.TypedDataChainID, msgs[0], txBytes, feeDelegation)
```

**File:** ethereum/eip712/eip712_legacy.go (L79-93)
```go
	if feeDelegation != nil {
		feeInfo, ok := txData["fee"].(map[string]interface{})
		if !ok {
			return apitypes.TypedData{}, errorsmod.Wrap(errortypes.ErrInvalidType, "cannot parse fee from tx data")
		}

		feeInfo["feePayer"] = feeDelegation.FeePayer.String()

		// also patching msgTypes to include feePayer
		msgTypes["Fee"] = []apitypes.Type{
			{Name: "feePayer", Type: "string"},
			{Name: "amount", Type: "Coin[]"},
			{Name: "gas", Type: "string"},
		}
	}
```
