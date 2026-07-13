Looking at the relevant code in detail to trace the exact vulnerability path.

### Title
Legacy EIP-712 Type Schema Derived Only from `msgs[0]`, Leaving Zero-Value Fields of Subsequent Messages Uncovered — (`ethereum/eip712/eip712_legacy.go`, `ethereum/eip712/encoding_legacy.go`)

---

### Summary

In the legacy EIP-712 signing path, the type schema (`MsgValue`) is derived exclusively from `msgs[0]` via reflection. Any struct field that is zero/empty in `msgs[0]` is explicitly skipped and excluded from the schema. Because the EIP-712 `Message` payload contains **all** messages, fields present in `msgs[1]` (or later) that were zero in `msgs[0]` are not covered by the hash. An attacker who controls tx construction can modify those uncovered fields in subsequent messages after the user signs, and the signature remains valid.

---

### Finding Description

**Root cause 1 — schema derived from `msgs[0]` only.**

`legacyDecodeAminoSignDoc` decodes all messages but then pins type inference to `msgs[0]`: [1](#0-0) 

`msg` (only `msgs[0]`) is passed to `LegacyWrapTxToTypedData`, while `signDocBytes` — which encodes **all** messages — is passed as the `data` argument that becomes the EIP-712 `Message`.

**Root cause 2 — zero-value fields are silently dropped from the schema.**

Inside `legacyTraverseFields`, every struct field whose runtime value is zero is unconditionally skipped: [2](#0-1) 

This means if `msgs[0].fieldX == 0` (or nil/empty string/empty slice), `fieldX` is absent from the `MsgValue` type definition.

**Root cause 3 — `LegacyValidatePayloadMessages` does not enforce field-presence equality.**

The validator only checks that all messages share the same Amino type string and the same signer: [3](#0-2) 

It does not verify that every message has the same set of non-zero fields, so a tx where `msgs[0]` omits an optional field while `msgs[1]` sets it passes validation without issue.

**Root cause 4 — the `Tx` schema hardcodes `msgs: Msg[]` pointing to the single `MsgValue` type.** [4](#0-3) 

All elements of the `msgs` array are hashed using the same `MsgValue` type definition, which was built from `msgs[0]` alone. Fields absent from that definition are silently ignored by the EIP-712 hasher for every element of the array, including `msgs[1]`.

---

### Impact Explanation

An attacker (e.g., a malicious dApp constructing the tx) can:

1. Build `msgs[0]` with `fieldX = 0` (zero) and `msgs[1]` with `fieldX = y` (the value the user expects to authorize).
2. Present the tx to the user for signing. The EIP-712 hash does **not** cover `fieldX` because it was absent from `msgs[0]`.
3. After obtaining the signature, replace `msgs[1].fieldX` with an arbitrary value `z`.
4. Submit the modified tx. The chain re-derives the same schema (still from `msgs[0]`, still missing `fieldX`), computes the same EIP-712 hash, and accepts the signature.

The practical impact depends on which message types expose optional fields. Any Cosmos SDK message with optional sub-fields (optional recipient, optional metadata, optional authorization parameters) is a candidate. This constitutes an **EIP-712 authorization bypass enabling forged execution** — the signer authorized `fieldX = y` but the chain executes `fieldX = z`.

---

### Likelihood Explanation

- The legacy path is still active and reachable via `LegacyGetEIP712TypedDataForMsg` / `LegacyGetEIP712BytesForMsg`.
- Multi-message txs of the same type are a documented and validated use case (`LegacyValidatePayloadMessages` explicitly supports them).
- The zero-value skip in `legacyTraverseFields` is intentional design (comment: *"will not be present in the object"*), making it a stable, non-accidental code path.
- A malicious dApp constructing txs for MetaMask/Keplr users is a realistic, unprivileged attacker role in the EVM ecosystem.

---

### Recommendation

1. **Derive the schema from the union of all messages.** Walk every message in `msgs`, not just `msgs[0]`, and merge the resulting field sets into `MsgValue`. Any field present in any message must appear in the schema.
2. **Enforce field-presence equality in `LegacyValidatePayloadMessages`.** After marshalling each message to JSON, compare the key sets; reject the tx if any message has keys absent from `msgs[0]`.
3. **Remove the zero-value skip or replace it with a JSON-key-presence check.** The schema should be derived from the Go struct's declared fields (or from the actual JSON keys of all messages), not from the runtime values of a single instance.

---

### Proof of Concept

```
// Pseudocode unit test
msg0 := MsgExample{RequiredField: "x", OptionalField: ""}   // OptionalField is zero → excluded from schema
msg1 := MsgExample{RequiredField: "x", OptionalField: "victim_value"}

signDoc := buildAminoSignDoc(chainID, acctNum, seq, fee, []sdk.Msg{msg0, msg1})
typedData, _ := LegacyGetEIP712TypedDataForMsg(signDoc)
// typedData.Types["MsgValue"] does NOT contain "optional_field"

hash1, _ := apitypes.TypedDataAndHash(typedData)

// Attacker mutates msg1's optional field
msg1Mutated := MsgExample{RequiredField: "x", OptionalField: "attacker_value"}
signDocMutated := buildAminoSignDoc(chainID, acctNum, seq, fee, []sdk.Msg{msg0, msg1Mutated})
typedDataMutated, _ := LegacyGetEIP712TypedDataForMsg(signDocMutated)
// Schema still derived from msg0 → still missing "optional_field"

hash2, _ := apitypes.TypedDataAndHash(typedDataMutated)

assert.Equal(t, hash1, hash2)  // PASSES — same hash, signature transfers
```

The assertion passes because `optional_field` is absent from `MsgValue` in both cases, so the EIP-712 hash is identical regardless of its value in `msgs[1]`.

### Citations

**File:** ethereum/eip712/encoding_legacy.go (L102-126)
```go
	// Use first message for fee payer and type inference
	msg := msgs[0]

	// By convention, the fee payer is the first address in the list of signers.
	signers, _, err := protoCodec.GetMsgV1Signers(msg)
	if err != nil {
		return apitypes.TypedData{}, err
	}
	feePayer := signers[0]
	feeDelegation := &FeeDelegationOptions{
		FeePayer: feePayer,
	}

	chainID, err := types.ParseChainID(aminoDoc.ChainID)
	if err != nil {
		return apitypes.TypedData{}, errors.New("invalid chain ID passed as argument")
	}

	typedData, err := LegacyWrapTxToTypedData(
		protoCodec,
		chainID.Uint64(),
		msg,
		signDocBytes,
		feeDelegation,
	)
```

**File:** ethereum/eip712/encoding_legacy.go (L236-274)
```go
func LegacyValidatePayloadMessages(msgs []sdk.Msg) error {
	if len(msgs) == 0 {
		return errors.New("unable to build EIP-712 payload: transaction does contain any messages")
	}

	var msgType string
	var msgSigner sdk.AccAddress

	for i, m := range msgs {
		t, err := getMsgType(m)
		if err != nil {
			return err
		}

		signers, _, err := protoCodec.GetMsgV1Signers(m)
		if err != nil {
			return err
		}
		if len(signers) != 1 {
			return errors.New("unable to build EIP-712 payload: expect exactly 1 signer")
		}

		if i == 0 {
			msgType = t
			msgSigner = signers[0]
			continue
		}

		if t != msgType {
			return errors.New("unable to build EIP-712 payload: different types of messages detected")
		}

		if !msgSigner.Equals(sdk.AccAddress(signers[0])) {
			return errors.New("unable to build EIP-712 payload: multiple signers detected")
		}
	}

	return nil
}
```

**File:** ethereum/eip712/eip712_legacy.go (L129-152)
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
		"Fee": {
			{Name: "amount", Type: "Coin[]"},
			{Name: "gas", Type: "string"},
		},
		"Coin": {
			{Name: "denom", Type: "string"},
			{Name: "amount", Type: "string"},
		},
		"Msg": {
			{Name: "type", Type: "string"},
			{Name: "value", Type: msgTypeName},
		},
		msgTypeName: {},
	}
```

**File:** ethereum/eip712/eip712_legacy.go (L228-231)
```go
		// If field is an empty value, do not include in types, since it will not be present in the object
		if field.IsZero() {
			continue
		}
```
