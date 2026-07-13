### Title
`timeout_height` Excluded from EIP-712 Legacy Typed-Data Hash Allows Expiry Bypass - (File: `ethereum/eip712/eip712_legacy.go`, `ante/cosmos/eip712.go`)

### Summary

The legacy EIP-712 signature path in Ethermint omits `timeout_height` from the `Tx` type schema used to compute the EIP-712 struct hash. Because EIP-712 silently ignores message fields not declared in the type definition, a signed legacy EIP-712 Cosmos transaction's signature does not commit to its `timeout_height`. An unprivileged attacker who observes a signed transaction in the mempool can strip or alter `timeout_height` without invalidating the signature, causing a transaction the user intended to expire to be accepted and executed after the intended deadline.

### Finding Description

**Root cause — type schema omits `timeout_height`**

In `ethereum/eip712/eip712_legacy.go`, `extractMsgTypes` builds the EIP-712 `Tx` type definition. The comment explicitly acknowledges the omission:

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

The same omission exists in the newer `Tx` type schema in `ethereum/eip712/types.go`: [2](#0-1) 

**How the legacy verification path uses `timeout_height`**

In `ante/cosmos/eip712.go`, `VerifySignature` constructs the Amino sign bytes using the **actual** `timeout_height` from the submitted transaction:

```go
txBytes := legacytx.StdSignBytes(
    signerData.ChainID,
    signerData.AccountNumber,
    signerData.Sequence,
    tx.GetTimeoutHeight(),   // ← real timeout_height from the tx
    legacytx.StdFee{...},
    msgs, tx.GetMemo(),
)
``` [3](#0-2) 

Those bytes are then passed to `LegacyWrapTxToTypedData`, which unmarshals the JSON into a `txData` map. When `timeout_height` is non-zero, the Cosmos SDK's `StdSignBytes` includes it in the JSON (`"timeout_height": "N"`). The field lands in the `txData` map but, because it is absent from the `Tx` type schema, EIP-712's `encodeData` silently ignores it when computing the struct hash. [4](#0-3) 

**Contrast with the newer Protobuf path**

`decodeProtobufSignDoc` in `ethereum/eip712/encoding.go` explicitly rejects any transaction that sets `TimeoutHeight != 0`:

```go
if body.TimeoutHeight != 0 ||
    body.GetTimeoutTimestamp() != nil || ... {
    return apitypes.TypedData{}, errors.New(
        "body contains unsupported fields: TimeoutHeight, ...")
}
``` [5](#0-4) 

The same guard exists in `legacyDecodeProtobufSignDoc`: [6](#0-5) 

Neither guard exists in the `VerifySignature` / `LegacyWrapTxToTypedData` code path used by `LegacyEip712SigVerificationDecorator`. That path accepts any `timeout_height` value and silently drops it from the hash.

**Exploit flow**

1. User constructs a legacy EIP-712 Cosmos tx (with `ExtensionOptionsWeb3Tx`) setting `timeout_height = T` to ensure the tx expires at block `T`.
2. User signs and broadcasts the tx. It enters the public mempool.
3. The tx is not included before block `T` (e.g., due to congestion or deliberate delay).
4. Attacker copies the tx from the mempool and sets `timeout_height = 0` (no expiry).
5. Attacker resubmits the modified tx. `VerifySignature` recomputes the EIP-712 hash with `timeout_height = 0`; because `timeout_height` is not in the type schema, the hash is identical to the one the user signed. Signature verification passes.
6. The Cosmos SDK's `TxTimeoutHeightDecorator` sees `timeout_height = 0` and does not reject the tx.
7. The tx executes after the user's intended deadline.

### Impact Explanation

This is an EIP-712 authorization bypass. The `timeout_height` field — the only mechanism a legacy EIP-712 signer has to bound the validity window of their transaction — is not covered by the signature. An unprivileged attacker with mempool access can remove or extend the expiry of any signed legacy EIP-712 Cosmos transaction, causing it to execute at an arbitrary future block. Depending on the message type (e.g., `MsgSend`, governance votes, staking operations), this can result in unauthorized fund transfers or state mutations the user explicitly intended to prevent after a deadline.

This matches the allowed High impact: **EIP-712 authorization verification bypass enabling forged execution or unauthorized account/code mutation**.

### Likelihood Explanation

- Mempool transactions are publicly observable on any full node.
- The modification is trivial (set one integer field to zero).
- No special privileges, keys, or network position are required.
- The attack is only relevant when a user sets `timeout_height != 0` on a legacy EIP-712 tx, which is a supported and documented Cosmos SDK feature.

### Recommendation

Add an explicit check in `VerifySignature` (`ante/cosmos/eip712.go`) that rejects any legacy EIP-712 transaction whose `timeout_height` is non-zero, mirroring the guard already present in `decodeProtobufSignDoc` and `legacyDecodeProtobufSignDoc`:

```go
if tx.GetTimeoutHeight() != 0 {
    return errorsmod.Wrap(errortypes.ErrNotSupported,
        "timeout_height is not supported for legacy EIP-712 transactions")
}
```

Alternatively, add `timeout_height` to the `Tx` type schema in `extractMsgTypes` and ensure it is always serialized (even when zero) so the field is covered by the EIP-712 hash.

### Proof of Concept

1. Construct a `MsgSend` wrapped in a legacy EIP-712 Cosmos tx with `timeout_height = current_block + 5`.
2. Sign it with an EIP-712-compatible wallet; record the `FeePayerSig`.
3. Broadcast the tx; ensure it is not included within 5 blocks.
4. After block `current_block + 5`, clone the tx and set `timeout_height = 0`.
5. Submit the cloned tx. Observe that `LegacyEip712SigVerificationDecorator` accepts the signature and the `MsgSend` executes, despite the user's intended expiry having passed.

The signature acceptance is deterministic: `LegacyWrapTxToTypedData` produces the same `TypedData` hash for both `timeout_height = N` and `timeout_height = 0` because `timeout_height` is absent from the `Tx` type schema in `extractMsgTypes`. [1](#0-0) [7](#0-6)

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

**File:** ethereum/eip712/types.go (L74-81)
```go
		"Tx": {
			{Name: "account_number", Type: "string"},
			{Name: "chain_id", Type: "string"},
			{Name: "fee", Type: "Fee"},
			{Name: "memo", Type: "string"},
			{Name: "sequence", Type: "string"},
			// Note timeout_height was removed because it was not getting filled with the legacyTx
		},
```

**File:** ante/cosmos/eip712.go (L197-253)
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
		if err != nil {
			return errorsmod.Wrap(err, "failed to create EIP-712 typed data from tx")
		}

		sigHash, _, err := apitypes.TypedDataAndHash(typedData)
```

**File:** ethereum/eip712/encoding.go (L158-165)
```go
	if body.TimeoutHeight != 0 ||
		body.GetTimeoutTimestamp() != nil ||
		len(body.ExtensionOptions) != 0 ||
		len(body.NonCriticalExtensionOptions) != 0 {
		return apitypes.TypedData{}, errors.New(
			"body contains unsupported fields: TimeoutHeight, TimeoutTimestamp, ExtensionOptions, or NonCriticalExtensionOptions",
		)
	}
```

**File:** ethereum/eip712/encoding_legacy.go (L158-165)
```go
	if body.TimeoutHeight != 0 ||
		body.GetTimeoutTimestamp() != nil ||
		len(body.ExtensionOptions) != 0 ||
		len(body.NonCriticalExtensionOptions) != 0 {
		return apitypes.TypedData{}, errors.New(
			"body contains unsupported fields: TimeoutHeight, TimeoutTimestamp, ExtensionOptions, or NonCriticalExtensionOptions",
		)
	}
```
