### Title
EIP-712 `Tx` Type Omits `timeout_height`, Enabling Replay of Expired Cosmos Transactions - (File: ethereum/eip712/types.go)

### Summary
The EIP-712 type definition for `Tx` in `ethereum/eip712/types.go` explicitly excludes `timeout_height`. Because `PubKey.VerifySignature` in `crypto/ethsecp256k1/ethsecp256k1.go` accepts a signature as valid if it verifies against the EIP-712 hash of the sign bytes, an unprivileged attacker can strip or modify the `timeout_height` field of any Cosmos SDK transaction signed with EIP-712 without invalidating the signature. This enables replay of transactions beyond the user's intended expiry block.

### Finding Description

**Root cause — `ethereum/eip712/types.go`**

The `Tx` struct type used to build the EIP-712 typed data is defined as:

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

`timeout_height` is intentionally absent. [1](#0-0) 

**How the sign bytes are produced — `ethereum/eip712/encoding.go`**

`decodeAminoSignDoc` calls `legacytx.StdSignBytes(...)` which serialises `timeout_height` into the Amino JSON sign doc, then passes those bytes to `WrapTxToTypedData`. Because `timeout_height` is absent from the `Tx` type definition, EIP-712's `hashStruct` ignores it when computing the domain-separated hash. [2](#0-1) 

The Protobuf path in `decodeProtobufSignDoc` already guards against this by rejecting `body.TimeoutHeight != 0`, but the Amino path has no such guard. [3](#0-2) 

**Triple-path verification — `crypto/ethsecp256k1/ethsecp256k1.go`**

`PubKey.VerifySignature` accepts a signature if it verifies against **any** of three representations: raw ECDSA, current EIP-712, or legacy EIP-712:

```go
func (pubKey PubKey) VerifySignature(msg, sig []byte) bool {
    return pubKey.verifySignatureECDSA(msg, sig) || pubKey.verifySignatureAsEIP712(msg, sig)
}
``` [4](#0-3) 

`verifySignatureAsEIP712` calls `GetEIP712BytesForMsg(msg)` (Amino path first) and then `LegacyGetEIP712BytesForMsg(msg)`: [5](#0-4) 

This function is invoked by the standard Cosmos SDK `SigVerificationDecorator` used in `newCosmosAnteHandler`: [6](#0-5)

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

**File:** crypto/ethsecp256k1/ethsecp256k1.go (L226-228)
```go
func (pubKey PubKey) VerifySignature(msg, sig []byte) bool {
	return pubKey.verifySignatureECDSA(msg, sig) || pubKey.verifySignatureAsEIP712(msg, sig)
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

**File:** evmd/ante/evm_handler.go (L205-207)
```go

```
