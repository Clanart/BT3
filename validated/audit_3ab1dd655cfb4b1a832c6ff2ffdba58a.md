### Title
`timeout_height` Silently Excluded from EIP-712 Type Schema Enables Signature Bypass for Amino-Signed Cosmos Transactions - (File: `ethereum/eip712/types.go`, `ethereum/eip712/eip712_legacy.go`)

### Summary

The EIP-712 `"Tx"` type schema in both the current and legacy encoding paths deliberately omits `timeout_height` from the type definition. For `SIGN_MODE_LEGACY_AMINO_JSON` transactions, the Amino sign-doc JSON carries `timeout_height` in the message payload, but because the field is absent from the type schema, EIP-712 hashing silently ignores it. An attacker who intercepts a signed transaction can strip or alter `timeout_height` without invalidating the signature, allowing a time-limited transaction to be replayed indefinitely.

### Finding Description

**Root cause — type schema omits `timeout_height`:**

In `ethereum/eip712/types.go`, `createEIP712Types` defines the `"Tx"` struct used for the current EIP-712 path:

```go
"Tx": {
    {Name: "account_number", Type: "string"},
    {Name: "chain_id",       Type: "string"},
    {Name: "fee",            Type: "Fee"},
    {Name: "memo",           Type: "string"},
    {Name: "sequence",       Type: "string"},
    // Note timeout_height was removed because it was not getting filled with the legacyTx
},
```

The same omission exists in the legacy path in `ethereum/eip712/eip712_legacy.go`, `extractMsgTypes`:

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
```

**How the Amino path reaches this code:**

`ethsecp256k1.PubKey.VerifySignature` calls `verifySignatureAsEIP712`, which calls `eip712.GetEIP712BytesForMsg(msg)` and, on failure, `eip712.LegacyGetEIP712BytesForMsg(msg)`. For `SIGN_MODE_LEGACY_AMINO_JSON`, `msg` is the Amino JSON sign-doc. `decodeAminoSignDoc` (in `encoding.go`) and `legacyDecodeAminoSignDoc` (in `encoding_legacy.go`) both accept this path **without any check on `timeout_height`**, unlike the Protobuf path which explicitly rejects `body.TimeoutHeight != 0`.

The Amino JSON sign-doc is produced by `legacytx.StdSignBytes`, which includes `"timeout_height": "N"` when `N > 0`. The message payload passed to `WrapTxToTypedData` / `LegacyWrapTxToTypedData` therefore contains `timeout_height` in the JSON map, but the type schema does not declare it. Per EIP-712, undeclared fields are excluded from the `typeHash` and `encodeData` computation, so the final hash is identical regardless of the `timeout_height` value.

**The ante handler also passes the live tx's `timeout_height` into the hash computation:**

In `ante/cosmos/eip712.go`, `VerifySignature` constructs `txBytes` from the submitted transaction:

```go
txBytes := legacytx.StdSignBytes(
    signerData.ChainID,
    signerData.AccountNumber,
    signerData.Sequence,
    tx.GetTimeoutHeight(),   // attacker-controlled value from submitted tx
    legacytx.StdFee{...},
    msgs, tx.GetMemo(),
)
```

Because `timeout_height` is not in the type schema, the EIP-712 hash produced from `txBytes` with `timeout_height = 0` is identical to the hash produced with `timeout_height = 100`. The signature check passes unconditionally for any `timeout_height` value.

### Impact Explanation

A user signs a Cosmos transaction via EIP-712 (Amino sign mode) with `timeout_height = H`, intending the transaction to be rejected by the chain if not included before block `H`. The EIP-712 signature does not commit to `H`. An attacker who observes the signed transaction (e.g., from the mempool or a broadcast relay) can resubmit it with `timeout_height = 0`, bypassing the expiry constraint. The Cosmos SDK `TxTimeoutHeightDecorator` will then accept the transaction at any future block height. This is a direct EIP-712 authorization bypass: the signer's intent (time-bounded validity) is silently stripped without invalidating the cryptographic proof.

### Likelihood Explanation

The `SIGN_MODE_LEGACY_AMINO_JSON` EIP-712 path is the standard signing mode for Cosmos wallets (Keplr, Metamask via Ethermint). Any user who sets `timeout_height` on an EIP-712-signed Cosmos transaction is affected. The attacker only needs to observe the broadcast transaction before it is included in a block — no privileged access is required. The Amino path has no guard against `timeout_height` (unlike the Protobuf path), making this reachable through normal transaction submission.

### Recommendation

1. Add `timeout_height` to the `"Tx"` type schema in both `ethereum/eip712/types.go` and `ethereum/eip712/eip712_legacy.go`:
   ```go
   {Name: "timeout_height", Type: "string"},
   ```
2. Ensure the message payload always populates `timeout_height` (as a string, defaulting to `"0"` when absent) so the field is consistently committed to in the hash.
3. Alternatively, mirror the Protobuf path's approach and reject Amino sign-docs that carry a non-zero `timeout_height` until full support is implemented, preventing silent bypass.

### Proof of Concept

1. User constructs a Cosmos `MsgSend` with `timeout_height = 100` and signs it via EIP-712 Amino (`SIGN_MODE_LEGACY_AMINO_JSON`). The Amino JSON sign-doc contains `"timeout_height": "100"`.
2. `ethsecp256k1.PubKey.verifySignatureAsEIP712` is called with these sign bytes. `decodeAminoSignDoc` succeeds; `createEIP712Types` produces a `"Tx"` schema without `timeout_height`. The EIP-712 hash `H1` is computed.
3. Attacker intercepts the signed transaction and sets `timeout_height = 0` before broadcasting. The Amino JSON sign-doc now omits `timeout_height` (due to `omitempty`).
4. On-chain, `verifySignatureAsEIP712` is called with the modified sign bytes. `decodeAminoSignDoc` succeeds; the same `"Tx"` schema is used. The EIP-712 hash `H2` is computed — `H1 == H2` because `timeout_height` is not in the schema.
5. `verifySignatureECDSA(H2, sig)` returns `true`. The transaction passes signature verification and is accepted at any block height, bypassing the user's `timeout_height = 100` constraint. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4) [6](#0-5)

### Citations

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

**File:** ethereum/eip712/encoding.go (L86-132)
```go
// decodeAminoSignDoc attempts to decode the provided sign doc (bytes) as an Amino payload
// and returns a signable EIP-712 TypedData object.
func decodeAminoSignDoc(signDocBytes []byte) (apitypes.TypedData, error) {
	// Ensure codecs have been initialized
	if err := validateCodecInit(); err != nil {
		return apitypes.TypedData{}, err
	}

	var aminoDoc legacytx.StdSignDoc
	if err := aminoCodec.UnmarshalJSON(signDocBytes, &aminoDoc); err != nil {
		return apitypes.TypedData{}, err
	}

	var fees legacytx.StdFee
	if err := aminoCodec.UnmarshalJSON(aminoDoc.Fee, &fees); err != nil {
		return apitypes.TypedData{}, err
	}

	// Validate payload messages
	msgs := make([]sdk.Msg, len(aminoDoc.Msgs))
	for i, jsonMsg := range aminoDoc.Msgs {
		var m sdk.Msg
		if err := aminoCodec.UnmarshalJSON(jsonMsg, &m); err != nil {
			return apitypes.TypedData{}, fmt.Errorf("failed to unmarshal sign doc message: %w", err)
		}
		msgs[i] = m
	}

	if err := validatePayloadMessages(msgs); err != nil {
		return apitypes.TypedData{}, err
	}

	chainID, err := types.ParseChainID(aminoDoc.ChainID)
	if err != nil {
		return apitypes.TypedData{}, errors.New("invalid chain ID passed as argument")
	}

	typedData, err := WrapTxToTypedData(
		chainID.Uint64(),
		signDocBytes,
	)
	if err != nil {
		return apitypes.TypedData{}, fmt.Errorf("could not convert to EIP712 representation: %w", err)
	}

	return typedData, nil
}
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

**File:** crypto/ethsecp256k1/ethsecp256k1.go (L233-250)
```go
func (pubKey PubKey) verifySignatureAsEIP712(msg, sig []byte) bool {
	eip712Bytes, err := eip712.GetEIP712BytesForMsg(msg)
	if err != nil {
		return false
	}

	if pubKey.verifySignatureECDSA(eip712Bytes, sig) {
		return true
	}

	// Try verifying the signature using the legacy EIP-712 encoding
	legacyEIP712Bytes, err := eip712.LegacyGetEIP712BytesForMsg(msg)
	if err != nil {
		return false
	}

	return pubKey.verifySignatureECDSA(legacyEIP712Bytes, sig)
}
```

**File:** ante/cosmos/eip712.go (L197-207)
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
```
