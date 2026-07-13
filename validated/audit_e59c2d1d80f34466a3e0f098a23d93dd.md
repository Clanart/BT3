### Title
Legacy EIP-712 `Tx` Type Schema Omits `timeout_height` — Signed Cosmos Transactions Can Be Revived After Intended Expiry - (File: `ethereum/eip712/eip712_legacy.go`)

---

### Summary

`LegacyWrapTxToTypedData` builds an EIP-712 `TypedData` object whose `Tx` type schema deliberately omits `timeout_height`. Because EIP-712 hashing only covers fields declared in the schema, any `timeout_height` value present in the raw JSON sign doc is silently excluded from the hash. A user who signs a Cosmos transaction with a non-zero `timeout_height` (intending it to expire at a specific block) produces a signature that is equally valid for the same transaction with `timeout_height=0`. An attacker who obtains the broadcast signed transaction bytes can strip the expiry and submit the transaction after the user-intended deadline, causing unauthorized execution of a transaction the user believed was cancelled.

---

### Finding Description

In `ethereum/eip712/eip712_legacy.go`, `LegacyWrapTxToTypedData` (lines 49–103) constructs the EIP-712 `TypedData` object by:

1. JSON-unmarshaling the raw sign-doc bytes into `txData` (a `map[string]interface{}`).
2. Building the `Tx` type schema via `extractMsgTypes`.
3. Setting `Message: txData` in the returned `TypedData`.

The `Tx` type schema (lines 129–138) explicitly omits `timeout_height`:

```go
"Tx": {
    {Name: "account_number", Type: "string"},
    {Name: "chain_id",       Type: "string"},
    {Name: "fee",            Type: "Fee"},
    {Name: "memo",           Type: "string"},
    {Name: "msgs",           Type: "Msg[]"},
    {Name: "sequence",       Type: "string"},
    // Note timeout_height was removed because it was not getting filled with the legacyTx
    // {Name: "timeout_height", Type: "string"},
},
``` [1](#0-0) 

The EIP-712 hashing algorithm (`apitypes.TypedDataAndHash`) only hashes fields declared in the type schema. Since `timeout_height` is absent from the schema, it is silently excluded from the hash even when present in `txData`.

This function is called from three paths, all of which pass a sign-doc that may contain a non-zero `timeout_height`:

**Path 1 — `legacyDecodeAminoSignDoc`** passes the raw `signDocBytes` directly:

```go
typedData, err := LegacyWrapTxToTypedData(
    protoCodec, chainID.Uint64(), msg,
    signDocBytes,   // raw attacker-supplied Amino JSON
    feeDelegation,
)
``` [2](#0-1) 

**Path 2 — `legacyDecodeProtobufSignDoc`** passes `signBytes` built from `legacytx.StdSignBytes(...)` which includes `body.TimeoutHeight`:

```go
signBytes := legacytx.StdSignBytes(
    signDoc.ChainId, signDoc.AccountNumber,
    signerInfo.Sequence,
    body.TimeoutHeight,   // non-zero value included in JSON, not in schema
    *stdFee, msgs, body.Memo,
)
``` [3](#0-2) 

**Path 3 — `VerifySignature` in `ante/cosmos/eip712.go`** passes `txBytes` built from `tx.GetTimeoutHeight()`:

```go
txBytes := legacytx.StdSignBytes(
    signerData.ChainID, signerData.AccountNumber,
    signerData.Sequence,
    tx.GetTimeoutHeight(),   // actual tx timeout_height, not hashed
    legacytx.StdFee{Amount: tx.GetFee(), Gas: tx.GetGas()},
    msgs, tx.GetMemo(),
)
...
typedData, err := eip712.LegacyWrapTxToTypedData(
    ethermintCodec, extOpt.TypedDataChainID, msgs[0], txBytes, feeDelegation)
``` [4](#0-3) 

In all three paths, if `timeout_height` is non-zero it appears in the JSON but is excluded from the EIP-712 hash.

By contrast, the non-legacy Protobuf path in `encoding.go` explicitly rejects `TimeoutHeight != 0`:

```go
if body.TimeoutHeight != 0 ||
    body.GetTimeoutTimestamp() != nil || ... {
    return apitypes.TypedData{}, errors.New(
        "body contains unsupported fields: TimeoutHeight, ...")
}
``` [5](#0-4) 

The legacy path has no equivalent guard, leaving the hash non-binding over `timeout_height`.

---

### Impact Explanation

A user who signs a Cosmos transaction with `timeout_height=X` via the legacy EIP-712 path produces a signature that is equally valid for the same transaction with `timeout_height=0`. An attacker who observes the broadcast signed transaction bytes can:

1. Modify the transaction to set `timeout_height=0`.
2. Submit the modified transaction after block X.
3. `VerifySignature` reconstructs the sign doc with `timeout_height=0`; the EIP-712 hash is identical to the one the user signed (since `timeout_height` is not in the schema), so signature verification passes.
4. The Cosmos SDK `Tx

### Citations

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

**File:** ethereum/eip712/encoding_legacy.go (L120-126)
```go
	typedData, err := LegacyWrapTxToTypedData(
		protoCodec,
		chainID.Uint64(),
		msg,
		signDocBytes,
		feeDelegation,
	)
```

**File:** ethereum/eip712/encoding_legacy.go (L210-218)
```go
	signBytes := legacytx.StdSignBytes( //nolint:staticcheck
		signDoc.ChainId,
		signDoc.AccountNumber,
		signerInfo.Sequence,
		body.TimeoutHeight,
		*stdFee,
		msgs,
		body.Memo,
	)
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

**File:** ethereum/eip712/encoding.go (L157-165)
```go
	// Until support for these fields is added, throw an error at their presence.
	if body.TimeoutHeight != 0 ||
		body.GetTimeoutTimestamp() != nil ||
		len(body.ExtensionOptions) != 0 ||
		len(body.NonCriticalExtensionOptions) != 0 {
		return apitypes.TypedData{}, errors.New(
			"body contains unsupported fields: TimeoutHeight, TimeoutTimestamp, ExtensionOptions, or NonCriticalExtensionOptions",
		)
	}
```
