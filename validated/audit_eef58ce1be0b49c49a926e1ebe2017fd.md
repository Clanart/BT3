Now I have all the code needed to make a definitive assessment. Let me trace the full path:

### Title
Legacy EIP-712 Type Schema Derived Exclusively from `msgs[0]` Omits Zero-Value Fields, Allowing Unsigned Field Values in `msgs[1..n]` to Execute — (File: `ethereum/eip712/eip712_legacy.go`)

---

### Summary

In the legacy EIP-712 signing path, the EIP-712 type schema is built by reflecting over `msgs[0]` only. Any field whose value is zero in `msgs[0]` is silently dropped from the schema. `LegacyValidatePayloadMessages` enforces only same-type and same-signer constraints — it never checks field-value equality across messages. A multi-message tx can therefore have `msgs[1..n]` carrying non-zero values for the omitted field F, while the EIP-712 hash is computed over a schema that does not include F at all. The `txBytes` passed into `LegacyWrapTxToTypedData` are serialised from **all** messages, so F's values for `msgs[1..n]` appear in the typed-data `Message` object but are invisible to the hash because F is absent from `Types`. A malicious dApp can exploit this to swap F's values in `msgs[1..n]` after the user signs without invalidating the signature.

---

### Finding Description

**Root cause 1 — zero-value field pruning in `legacyTraverseFields`** [1](#0-0) 

At line 229, any struct field whose reflected value is zero is skipped entirely. The comment says "it will not be present in the object", but this assumption is only valid for `msgs[0]`; it is silently extended to every message in the batch.

**Root cause 2 — type schema derived from `msgs[0]` only** [2](#0-1) 

`LegacyWrapTxToTypedData` receives `msgs[0]` as the sole source for type inference. The resulting `apitypes.Types` map therefore reflects only the fields that are non-zero in `msgs[0]`.

**Root cause 3 — `txBytes` encodes all messages** [3](#0-2) 

`legacytx.StdSignBytes` is called with the full `msgs` slice. The resulting JSON is unmarshalled into `txData` and placed verbatim in `typedData.Message`. F's values for `msgs[1..n]` are therefore present in the message payload but absent from the type schema, so `apitypes.TypedDataAndHash` ignores them when computing the hash.

**Root cause 4 — `LegacyValidatePayloadMessages` does not close the gap** [4](#0-3) 

The function only verifies that every message shares the same Amino type string and the same single signer. It performs no field-level comparison, so a batch where `msgs[0].F = ""` and `msgs[1].F = "attacker_value"` passes validation without issue.

---

### Impact Explanation

The EIP-712 hash the user signs does not commit to field F for any message in the batch. A malicious dApp acting as an intermediary can:

1. Present the user with a tx where `msgs[0].F = ""` (zero) and `msgs[1].F = value_A`.
2. Obtain the user's signature over the typed-data hash (which omits F).
3. Substitute `msgs[1].F = value_B` and submit the modified tx.
4. Signature verification passes because the hash is identical — F is not in the type schema.
5. The chain executes the tx with `value_B`, which the user never authorised.

This is a concrete EIP-712 authorization bypass enabling forged execution, matching the High impact category: *"EIP-712 authorization bypass enabling replay, forged execution, or unauthorized account/code mutation."*

The severity of the forged execution scales with what field F controls. For messages with optional string fields (e.g., `Metadata` on governance votes, `Memo`-equivalent fields on custom message types), the attacker can silently alter those values. For any message type where a field that influences fund routing or authorisation can be zero in `msgs[0]` but non-zero in `msgs[1]`, the impact escalates to fund mis-routing.

---

### Likelihood Explanation

- The attacker must operate as a malicious dApp that constructs the tx and relays the user's signature — a realistic Web3 threat model.
- The precondition (`msgs[0].F = zero`) is satisfiable for any message type that has at least one optional field (string, pointer, slice, or struct with a zero default). Many Cosmos SDK message types carry such fields (governance metadata, IBC memo, optional timeout, etc.).
- `LegacyEip712SigVerificationDecorator` is marked deprecated but remains fully functional and reachable via public tx submission on any chain that has not removed it from the ante chain.
- No chain-level privilege is required; the attacker submits a normal signed tx.

---

### Recommendation

1. **Derive the type schema from the union of all messages**, not only `msgs[0]`. Walk every message in the batch and merge their non-zero fields into the type map before computing the hash.
2. **Remove or invert the zero-value skip**: instead of omitting zero-value fields from the schema, always include every declared struct field. The EIP-712 encoder will encode zero values as their canonical zero representation, which is safe and deterministic.
3. **Add a field-equality check in `LegacyValidatePayloadMessages`**: reject any batch where `msgs[i]` has a field present (non-zero) that `msgs[0]` does not, until the schema derivation is fixed.

---

### Proof of Concept

```
1. Choose MsgVote (fields: ProposalId uint64, Voter string, Option VoteOption, Metadata string).
   Metadata is optional and defaults to "".

2. Construct tx with two MsgVote messages (same Voter = alice):
     msgs[0]: ProposalId=1, Voter=alice, Option=YES, Metadata=""   ← Metadata is zero
     msgs[1]: ProposalId=2, Voter=alice, Option=YES, Metadata="X"  ← Metadata is non-zero

3. legacyTraverseFields walks msgs[0]:
     Metadata.IsZero() == true  →  Metadata is NOT added to the type schema.

4. txBytes = StdSignBytes(..., [msgs[0], msgs[1]], ...)
   txData["msgs"][1]["value"]["metadata"] = "X"   ← present in Message
   but Types["MsgValue"] has no "metadata" entry  ← absent from schema

5. apitypes.TypedDataAndHash ignores "metadata" when hashing → hash H.

6. Alice signs H.

7. Attacker replaces msgs[1].Metadata = "Y" and recomputes txBytes.
   New txData["msgs"][1]["value"]["metadata"] = "Y"
   Types["MsgValue"] still has no "metadata" entry → same hash H.

8. Submit modified tx with Alice's original signature → passes VerifySignature.
   Chain executes with Metadata="Y", which Alice never authorised.
```

The same construction applies to any Cosmos SDK message type where at least one field is optional (zero-able) in `msgs[0]` while carrying a meaningful non-zero value in `msgs[1..n]`.

### Citations

**File:** ethereum/eip712/eip712_legacy.go (L228-231)
```go
		// If field is an empty value, do not include in types, since it will not be present in the object
		if field.IsZero() {
			continue
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

**File:** ante/cosmos/eip712.go (L248-251)
```go
		typedData, err := eip712.LegacyWrapTxToTypedData(ethermintCodec, extOpt.TypedDataChainID, msgs[0], txBytes, feeDelegation)
		if err != nil {
			return errorsmod.Wrap(err, "failed to create EIP-712 typed data from tx")
		}
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
