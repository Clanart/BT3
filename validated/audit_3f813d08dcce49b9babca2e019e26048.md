I need to trace the full legacy EIP-712 signing and verification path to determine whether `timeout_height` omission in the type schema creates a real exploitable collision.

**Key code paths examined:**

1. `ante/cosmos/eip712.go` `VerifySignature` — builds `txBytes` via `legacytx.StdSignBytes(... tx.GetTimeoutHeight() ...)`, then passes those bytes to `eip712.LegacyWrapTxToTypedData`.
2. `ethereum/eip712/eip712_legacy.go` `extractMsgTypes` — defines the `"Tx"` EIP-712 type schema **without** `timeout_height` (explicitly commented out).
3. `ethereum/eip712/encoding_legacy.go` `legacyDecodeProtobufSignDoc` — **does** guard against `body.TimeoutHeight != 0` when called from `LegacyGetEIP712BytesForMsg` (the `PubKey.VerifySignature` fallback path).
4. `evmd/ante/evm_handler.go` `newLegacyCosmosAnteHandlerEip712` — the ante chain that uses `LegacyEip712SigVerificationDecorator`.

**Tracing the collision claim:**

In the `LegacyEip712SigVerificationDecorator.VerifySignature` path:

- `txBytes` from `StdSignBytes` **does** include `"timeout_height"` in the JSON.
- `LegacyWrapTxToTypedData` JSON-unmarshals `txBytes` into `txData` (the EIP-712 message map), so `timeout_height` is present in the message data.
- However, the `"Tx"` type schema in `extractMsgTypes` does **not** declare `timeout_height` as a field.
- Per EIP-712 encoding rules, fields present in the message but absent from the type definition are **silently ignored** during `encodeData` / `TypedDataAndHash`.

**Result**: Two transactions T1 (`timeout_height=0`) and T2 (`timeout_height=N`) that are otherwise identical produce **the same EIP-712 `sigHash`** via `LegacyWrapTxToTypedData`. T1's `FeePayerSig` will pass `Ecrecover` verification for T2.

**Is this actually exploitable?**

The attacker **cannot** change `msgs` or `fee` — those fields ARE in the type schema and affect the hash. The only mutable field is `timeout_height`.

The concrete attack:
1. Victim signs T1 with `timeout_height = 100` (intending the tx to expire at block 100).
2. T1 is broadcast but not included before block 100.
3. After block 100, `authante.NewTxTimeoutHeightDecorator` would reject T1.
4. Attacker crafts T2 identical to T1 but with `timeout_height = 0` (no expiry). The Cosmos SDK signature field is empty (required by the legacy EIP-712 path), so the attacker can freely modify the tx body.
5. T2 produces the same EIP-712 hash as T1 → T1's `FeePayerSig` verifies for T2.
6. T2 passes `TxTimeoutHeightDecorator` (height=0 means no timeout) and passes `LegacyEip712SigVerificationDecorator`.
7. T2 executes the victim's messages after the intended expiry.

**Guard analysis:**

- The `legacyDecodeProtobufSignDoc` guard (`body.TimeoutHeight != 0 → error`) only applies to the `LegacyGetEIP712BytesForMsg` / `PubKey.VerifySignature` fallback path — **not** to the `LegacyEip712SigVerificationDecorator` path, which calls `LegacyWrapTxToTypedData` directly.
- The `LegacyEip712SigVerificationDecorator` path has **no** equivalent guard.

**Conclusion:**

The vulnerability is real but its scope is limited to `timeout_height` manipulation — the attacker cannot change msgs or fees. The impact is bypassing the user's intended tx expiry, allowing execution of a transaction the user believed had expired. This is a signature coverage gap (a field the user signed over is not covered by the EIP-712 hash), constituting a verifiable EIP-712 authorization bypass.

---

### Title
Legacy EIP-712 `timeout_height` Not Covered by Type Schema Allows Expiry Bypass — (`ethereum/eip712/eip712_legacy.go`, `ante/cosmos/eip712.go`)

### Summary
The `"Tx"` EIP-712 type schema in the legacy path omits `timeout_height`, so the field is present in the signed message data but excluded from the EIP-712 hash. An attacker can strip or alter `timeout_height` on a signed legacy EIP-712 transaction without invalidating the signature, bypassing the user's intended transaction expiry.

### Finding Description
In `extractMsgTypes` (`ethereum/eip712/eip712_legacy.go`), the `"Tx"` type definition explicitly omits `timeout_height`:

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

Meanwhile, `VerifySignature` in `ante/cosmos/eip712.go` builds `txBytes` via `legacytx.StdSignBytes` which **does** include `timeout_height` in the JSON, then passes those bytes directly to `LegacyWrapTxToTypedData`:

```go
txBytes := legacytx.StdSignBytes(
    signerData.ChainID, signerData.AccountNumber, signerData.Sequence,
    tx.GetTimeoutHeight(),   // ← included in JSON
    legacytx.StdFee{...}, msgs, tx.GetMemo(),
)
...
typedData, err := eip712.LegacyWrapTxToTypedData(ethermintCodec, extOpt.TypedDataChainID, msgs[0], txBytes, feeDelegation)
``` [2](#0-1) 

`LegacyWrapTxToTypedData` JSON-unmarshals `txBytes` into the EIP-712 message map, so `timeout_height` is present in `typedData.Message` but absent from `typedData.Types["Tx"]`. Per EIP-712 encoding, absent-from-schema fields are silently dropped during `encodeData`. The resulting `sigHash` is identical for any two transactions that differ only in `timeout_height`. [3](#0-2) 

The guard that rejects non-zero `TimeoutHeight` exists only in `legacyDecodeProtobufSignDoc` (the `LegacyGetEIP712BytesForMsg` / `PubKey.VerifySignature` fallback path), not in the `LegacyEip712SigVerificationDecorator` path: [4](#0-3) 

The `LegacyEip712SigVerificationDecorator` is wired into the production ante chain via `newLegacyCosmosAnteHandlerEip712`: [5](#0-4) 

### Impact Explanation
An attacker who observes a broadcast legacy EIP-712 transaction T1 with `timeout_height = N` can craft T2 with `timeout_height = 0`, reuse T1's `FeePayerSig`, and submit T2 after block N. T2 will pass both `TxTimeoutHeightDecorator` (height=0 means no expiry) and `LegacyEip712SigVerificationDecorator` (same EIP-712 hash), executing the victim's messages after the intended expiry. The attacker cannot change msgs or fees (those fields are in the schema and affect the hash), so the impact is limited to bypassing the user's `timeout_height` expiry intent.

### Likelihood Explanation
Requires the legacy EIP-712 ante path to be active (it is, via `newLegacyCosmosAnteHandlerEip712`), and requires the victim to have used a non-zero `timeout_height`. The attacker only needs to observe the mempool and submit a modified tx — no privileged access required.

### Recommendation
Add `timeout_height` back to the `"Tx"` type definition in `extractMsgTypes`, or add an explicit rejection of non-zero `timeout_height` in `VerifySignature` (mirroring the guard already present in `legacyDecodeProtobufSignDoc`).

### Proof of Concept
1. Sign a legacy EIP-712 tx T1 with `timeout_height = 100` using `LegacyWrapTxToTypedData` → obtain `sigHash1`.
2. Build T2 identical to T1 but with `timeout_height = 0` → compute `sigHash2` via `LegacyWrapTxToTypedData`.
3. Assert `sigHash1 == sigHash2` (they will be equal because `timeout_height` is absent from the type schema).
4. Submit T2 with T1's `FeePayerSig` after block 100 — it will be accepted and executed.

### Citations

**File:** ethereum/eip712/eip712_legacy.go (L60-102)
```go
	txData := make(map[string]interface{})

	if err := json.Unmarshal(data, &txData); err != nil {
		return apitypes.TypedData{}, errorsmod.Wrap(errortypes.ErrJSONUnmarshal, "failed to JSON unmarshal data")
	}

	domain := apitypes.TypedDataDomain{
		Name:              "Cosmos Web3",
		Version:           "1.0.0",
		ChainId:           math.NewHexOrDecimal256(value),
		VerifyingContract: "cosmos",
		Salt:              "0",
	}

	msgTypes, err := extractMsgTypes(cdc, "MsgValue", msg)
	if err != nil {
		return apitypes.TypedData{}, err
	}

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

**File:** ethereum/eip712/encoding_legacy.go (L157-165)
```go
	// Until support for these fields is added, throw an error at their presence
	if body.TimeoutHeight != 0 ||
		body.GetTimeoutTimestamp() != nil ||
		len(body.ExtensionOptions) != 0 ||
		len(body.NonCriticalExtensionOptions) != 0 {
		return apitypes.TypedData{}, errors.New(
			"body contains unsupported fields: TimeoutHeight, TimeoutTimestamp, ExtensionOptions, or NonCriticalExtensionOptions",
		)
	}
```

**File:** evmd/ante/evm_handler.go (L46-57)
```go
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
