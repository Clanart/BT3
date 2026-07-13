Let me check the ante handler setup and how `StdSignBytes` handles `timeout_height` in JSON output.

The legacy EIP-712 path is **active** in production. `newLegacyCosmosAnteHandlerEip712` is invoked for any tx carrying an `ExtensionOptionsWeb3Tx` extension option, and it includes `cosmos.NewLegacyEip712SigVerificationDecorator`.

The core facts are:

**1. `timeout_height` is deliberately excluded from the EIP-712 type definition.**

In `extractMsgTypes`, the `Tx` type is built without `timeout_height`:

```go
"Tx": {
    {Name: "account_number", Type: "string"},
    {Name: "chain_id", Type: "string"},
    {Name: "fee", Type: "Fee"},
    {Name: "memo", Type: "string"},
    {Name: "msgs", Type: "Msg[]"},
    {Name: "sequence", Type: "string"},
    // Note timeout_height was removed because it was not getting filled with the legacyTx
    // {Name: "timeout_height", Type: "string"},
},
``` [1](#0-0) 

**2. `StdSignBytes` IS called with `tx.GetTimeoutHeight()`**, so the raw JSON bytes passed to `LegacyWrapTxToTypedData` contain `timeout_height` when non-zero. [2](#0-1) 

**3. `LegacyWrapTxToTypedData` JSON-unmarshals those bytes into `txData`** (the EIP-712 Message), but because `timeout_height` is absent from the `Tx` type definition, EIP-712's `encodeData` ignores it entirely when computing the struct hash. [3](#0-2) 

**4. `TxTimeoutHeightDecorator` IS present** in the legacy ante chain and enforces the height check — but it reads `timeout_height` from the **tx body**, not from the signature. [4](#0-3) 

**The mismatch**: The EIP-712 sig hash is identical for a tx with `timeout_height=100` and one with `timeout_height=0`. An attacker who intercepts a signed tx (from mempool broadcast, RPC, etc.) can zero out `timeout_height` in the protobuf body, reserialize, and resubmit. The `FeePayerSig` in `ExtensionOptionsWeb3Tx` remains valid because it was computed over a hash that never committed to `timeout_height`. The `TxTimeoutHeightDecorator` then passes (0 = no timeout), and the tx executes at any future height.

The sequence number is covered by the hash, so this only works if the original tx was never included (e.g., it expired in the mempool before being picked up). That is precisely the scenario where `timeout_height` is meaningful to the user.

---

### Title
Legacy EIP-712 Signature Does Not Commit to `timeout_height`, Enabling Replay of Expired Transactions — (`ethereum/eip712/eip712_legacy.go`)

### Summary
The `Tx` type definition used by `LegacyWrapTxToTypedData` intentionally omits `timeout_height`. Because EIP-712 hashing only covers fields declared in the type schema, the signature over a tx with `timeout_height=N` is identical to one with `timeout_height=0`. An attacker can strip `timeout_height` from any intercepted signed legacy EIP-712 tx and resubmit it indefinitely, bypassing the user's intended expiry.

### Finding Description
In `ethereum/eip712/eip712_legacy.go`, `extractMsgTypes` builds the `Tx` EIP-712 type without `timeout_height` (the field is commented out at line 137). [5](#0-4) 

In `ante/cosmos/eip712.go`, `VerifySignature` calls `legacytx.StdSignBytes` with `tx.GetTimeoutHeight()`, producing JSON that includes `timeout_height` when non-zero. This JSON is passed as `data` to `LegacyWrapTxToTypedData`. [6](#0-5) 

`LegacyWrapTxToTypedData` unmarshals the JSON into `txData` (the EIP-712 Message), but since `timeout_height` is absent from the `Tx` type definition, `apitypes.TypedDataAndHash` ignores it. The resulting `sigHash` is the same regardless of `timeout_height`. [7](#0-6) 

The legacy ante handler chain includes `authante.NewTxTimeoutHeightDecorator()` (which reads `timeout_height` from the tx body) and `cosmos.NewLegacyEip712SigVerificationDecorator` (which verifies the EIP-712 sig). These two checks are decoupled: the height check uses the mutable tx body field, while the sig check uses a hash that ignores it. [8](#0-7) 

### Impact Explanation
A user who signs a legacy EIP-712 tx with `timeout_height=100` intends the tx to be invalid after block 100. An attacker can modify the tx body to set `timeout_height=0`, keeping the `FeePayerSig` valid, and submit the tx at any future height. The `TxTimeoutHeightDecorator` passes (0 = no expiry), and the tx executes. This is a signature coverage bypass enabling unauthorized replay — matching the **High** impact category: "EIP-712 authorization bypass enabling replay or forged execution."

### Likelihood Explanation
The legacy EIP-712 path is still reachable via any tx with `ExtensionOptionsWeb3Tx`. [9](#0-8)  Signed txs are visible in the mempool. The modification requires only protobuf deserialization, field zeroing, and reserialization — no privileged access needed. The attack is limited to txs that expired before inclusion (the sequence guard prevents double-spend of already-executed txs), but that is exactly the scenario where `timeout_height` is relied upon.

### Recommendation
Add `timeout_height` back to the `Tx` EIP-712 type definition in `extractMsgTypes`, and ensure `LegacyWrapTxToTypedData` always populates it (defaulting to `"0"` when absent). This makes the EIP-712 hash commit to `timeout_height`, so any post-signing modification invalidates the signature.

### Proof of Concept
1. Construct a legacy EIP-712 tx with `timeout_height=100`, sign it, obtain `FeePayerSig`.
2. Deserialize the tx protobuf; set `TxBody.TimeoutHeight = 0`; reserialize.
3. Submit the modified tx at block height 200.
4. Observe: `TxTimeoutHeightDecorator` passes (height 0 = no expiry); `LegacyEip712SigVerificationDecorator` passes (EIP-712 hash unchanged); tx executes.
5. Confirm: the EIP-712 hash of the original tx (`timeout_height=100`) equals the hash of the modified tx (`timeout_height=0`) because `timeout_height` is absent from the `Tx` type schema.

### Citations

**File:** ethereum/eip712/eip712_legacy.go (L60-63)
```go
	txData := make(map[string]interface{})

	if err := json.Unmarshal(data, &txData); err != nil {
		return apitypes.TypedData{}, errorsmod.Wrap(errortypes.ErrJSONUnmarshal, "failed to JSON unmarshal data")
```

**File:** ethereum/eip712/eip712_legacy.go (L95-102)
```go
	typedData := apitypes.TypedData{
		Types:       msgTypes,
		PrimaryType: "Tx",
		Domain:      domain,
		Message:     txData,
	}

	return typedData, nil
```

**File:** ethereum/eip712/eip712_legacy.go (L129-138)
```go
		"Tx": {
			{Name: "account_number", Type: "string"},
			{Name: "chain_id", Type: "string"},
			{Name: "fee", Type: "Fee"},
			{Name: "memo", Type: "string"},
			{Name: "msgs", Type: "Msg[]"},
			{Name: "sequence", Type: "string"},
			// Note timeout_height was removed because it was not getting filled with the legacyTx
			// {Name: "timeout_height", Type: "string"},
		},
```

**File:** ante/cosmos/eip712.go (L197-248)
```go
		txBytes := legacytx.StdSignBytes( //nolint:staticcheck
			signerData.ChainID,
			signerData.AccountNumber,
			signerData.Sequence,
			tx.GetTimeoutHeight(),
			legacytx.StdFee{
				Amount: tx.GetFee(),
				Gas:    tx.GetGas(),
			},
			msgs, tx.GetMemo(),
		)

		signerChainID, err := ethermint.ParseChainID(signerData.ChainID)
		if err != nil {
			return errorsmod.Wrapf(err, "failed to parse chain-id: %s", signerData.ChainID)
		}

		txWithExtensions, ok := tx.(authante.HasExtensionOptionsTx)
		if !ok {
			return errorsmod.Wrap(errortypes.ErrUnknownExtensionOptions, "tx doesnt contain any extensions")
		}
		opts := txWithExtensions.GetExtensionOptions()
		if len(opts) != 1 {
			return errorsmod.Wrap(errortypes.ErrUnknownExtensionOptions, "tx doesnt contain expected amount of extension options")
		}

		extOpt, ok := opts[0].GetCachedValue().(*ethermint.ExtensionOptionsWeb3Tx)
		if !ok {
			return errorsmod.Wrap(errortypes.ErrUnknownExtensionOptions, "unknown extension option")
		}

		if extOpt.TypedDataChainID != signerChainID.Uint64() {
			return errorsmod.Wrap(errortypes.ErrInvalidChainID, "invalid chain-id")
		}

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

**File:** evmd/ante/evm_handler.go (L40-57)
```go
	decorators = append(decorators,
		cosmos.RejectMessagesDecorator{}, // reject MsgEthereumTxs
		// disable the Msg types that cannot be included on an authz.MsgExec msgs field
		cosmos.NewAuthzLimiterDecorator(options.DisabledAuthzMsgs),
		authante.NewSetUpContextDecorator(),
		authante.NewValidateBasicDecorator(),
		authante.NewTxTimeoutHeightDecorator(),
		cosmos.NewMinGasPriceDecorator(options.FeeMarketKeeper, evmDenom, &feemarketParams),
		authante.NewValidateMemoDecorator(options.AccountKeeper),
		authante.NewConsumeGasForTxSizeDecorator(options.AccountKeeper),
		authante.NewDeductFeeDecorator(options.AccountKeeper, options.BankKeeper, options.FeegrantKeeper, txFeeChecker),
		// SetPubKeyDecorator must be called before all signature verification decorators
		authante.NewSetPubKeyDecorator(options.AccountKeeper),
		authante.NewValidateSigCountDecorator(options.AccountKeeper),
		authante.NewSigGasConsumeDecorator(options.AccountKeeper, options.SigGasConsumer),
		// Note: signature verification uses EIP instead of the cosmos signature validator
		cosmos.NewLegacyEip712SigVerificationDecorator(options.AccountKeeper, options.SignModeHandler),
		authante.NewIncrementSequenceDecorator(options.AccountKeeper),
```

**File:** evmd/ante/ante.go (L80-82)
```go
				case "/ethermint.types.v1.ExtensionOptionsWeb3Tx":
					// Deprecated: Handle as normal Cosmos SDK tx, except signature is checked for Legacy EIP712 representation
					anteHandler = newLegacyCosmosAnteHandlerEip712(ctx, options, options.ExtraDecorators...)
```
