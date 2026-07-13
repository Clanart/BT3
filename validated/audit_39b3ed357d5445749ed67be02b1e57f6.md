### Title
`timeout_height` Excluded from Legacy EIP-712 Typed-Data Hash Enables Signature Reuse Across Transaction Expiry Boundaries — (`ethereum/eip712/eip712_legacy.go`)

---

### Summary

The `timeout_height` field is intentionally omitted from the EIP-712 `Tx` type schema used by the legacy EIP-712 signing path (`LegacyEip712SigVerificationDecorator`). Because EIP-712 hashing only covers fields declared in the type schema, a user's signature over a transaction with `timeout_height = N` is cryptographically identical to a signature over the same transaction with `timeout_height = 0`. Any party that receives the signed transaction can strip the expiry and replay it after the intended deadline, with the signature still passing verification.

---

### Finding Description

**Root cause — type schema omits `timeout_height`**

`extractMsgTypes` in `ethereum/eip712/eip712_legacy.go` hard-codes the `Tx` EIP-712 type without `timeout_height`:

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

The same omission exists in the newer `createEIP712Types` path:

```go
"Tx": {
    {Name: "account_number", Type: "string"},
    ...
    {Name: "sequence", Type: "string"},
    // Note timeout_height was removed because it was not getting filled with the legacyTx
},
``` [2](#0-1) 

**How the ante handler feeds `timeout_height` into the hash computation — and why it has no effect**

`VerifySignature` in `ante/cosmos/eip712.go` calls `legacytx.StdSignBytes` with the submitted transaction's `timeout_height`:

```go
txBytes := legacytx.StdSignBytes(
    signerData.ChainID,
    signerData.AccountNumber,
    signerData.Sequence,
    tx.GetTimeoutHeight(),   // ← present in the Amino JSON bytes …
    legacytx.StdFee{...},
    msgs, tx.GetMemo(),
)
``` [3](#0-2) 

Those bytes are then passed directly to `LegacyWrapTxToTypedData`:

```go
typedData, err := eip712.LegacyWrapTxToTypedData(
    ethermintCodec, extOpt.TypedDataChainID, msgs[0], txBytes, feeDelegation)
``` [4](#0-3) 

`LegacyWrapTxToTypedData` unmarshals the Amino JSON into a raw `map[string]interface{}` and uses it as the EIP-712 `Message`. Because EIP-712 hashing iterates only over fields declared in the type schema, and `timeout_height` is absent from the schema, the field is silently ignored during hash computation regardless of its value in the JSON. [5](#0-4) 

**The legacy ante handler is still active in production**

`newLegacyCosmosAnteHandlerEip712` is invoked for every transaction carrying an `ExtensionOptionsWeb3Tx` extension option:

```go
case "/ethermint.types.v1.ExtensionOptionsWeb3Tx":
    anteHandler = newLegacyCosmosAnteHandlerEip712(ctx, options, ...)
``` [6](#0-5) 

It installs `LegacyEip712SigVerificationDecorator` as the signature-verification step: [7](#0-6) 

**Contrast with the newer path**

`decodeProtobufSignDoc` (used by `ethsecp256k1.PubKey.verifySignatureAsEIP712`) explicitly rejects any sign-doc that carries a non-zero `TimeoutHeight`:

```go
if body.TimeoutHeight != 0 || ... {
    return apitypes.TypedData{}, errors.New(
        "body contains unsupported fields: TimeoutHeight, ...")
}
``` [8](#0-7) 

The legacy ante-handler path has no equivalent guard, so a transaction with any `timeout_height` value is accepted and the field is simply dropped from the hash.

---

### Impact Explanation

Because `timeout_height` is not covered by the EIP-712 signature, a single signed transaction is simultaneously valid for:

- `timeout_height = N` (the user's intended expiry), and  
- `timeout_height = 0` (no expiry at all).

Any party that observes the signed transaction in the mempool can construct a second transaction that is byte-for-byte identical except for `timeout_height = 0`, attach the original signature, and broadcast it. `TxTimeoutHeightDecorator` will not reject it (zero means "no deadline"), and `LegacyEip712SigVerificationDecorator` will accept the signature. The transaction executes after the block height at which the user intended it to expire.

This is an EIP-712 authorization bypass enabling forged execution: the user's signature authorizes execution only within a specific block-height window, but that constraint is unenforceable because the window is not committed to in the signed hash.

---

### Likelihood Explanation

The attack requires no privileged access. Any observer of the public mempool can:

1. See a pending EIP-712 transaction with `timeout_height = N`.  
2. Reconstruct the same transaction with `timeout_height = 0`.  
3. Reuse the original `FeePayerSig` from `ExtensionOptionsWeb3Tx`.  
4. Broadcast the modified transaction at any future block.

The only precondition is that the user chose to set `timeout_height` — a standard practice for time-sensitive operations such as governance votes, DEX swaps, or delegation changes. The legacy EIP-712 path remains the default for Metamask/Web3 wallet users interacting with Cosmos SDK messages.

---

### Recommendation

Add `timeout_height` to the `Tx` type schema in both the legacy and current EIP-712 encoders:

```go
// ethereum/eip712/eip712_legacy.go — extractMsgTypes
"Tx": {
    {Name: "account_number", Type: "string"},
    {Name: "chain_id",       Type: "string"},
    {Name: "fee",            Type: "Fee"},
    {Name: "memo",           Type: "string"},
    {Name: "msgs",           Type: "Msg[]"},
    {Name: "sequence",       Type: "string"},
    {Name: "timeout_height", Type: "string"},  // ← add this
},
```

```go
// ethereum/eip712/types.go — createEIP712Types
"Tx": {
    ...
    {Name: "timeout_height", Type: "string"},  // ← add this
},
```

The Amino JSON produced by `legacytx.StdSignBytes` already serialises `timeout_height` when it is non-zero (via `omitempty`), so no changes to the signing or ante-handler code are needed — only the type schema must be updated to include the field so that it participates in the EIP-712 hash.

---

### Proof of Concept

```
1. Alice creates a Cosmos SDK MsgSend with timeout_height = 500 and signs it
   via the legacy EIP-712 path (ExtensionOptionsWeb3Tx).

2. The EIP-712 hash H is computed over {account_number, chain_id, fee, memo,
   msgs, sequence} — timeout_height is absent.

3. Alice broadcasts the transaction; it sits in the mempool.

4. Block 500 passes; TxTimeoutHeightDecorator begins rejecting Alice's original
   transaction.

5. Attacker copies the transaction, sets timeout_height = 0, keeps FeePayerSig
   unchanged, and broadcasts the modified transaction.

6. TxTimeoutHeightDecorator: timeout_height == 0 → no deadline → passes.

7. LegacyEip712SigVerificationDecorator:
   - Calls StdSignBytes with timeout_height = 0 → JSON omits the field.
   - Calls LegacyWrapTxToTypedData → EIP-712 hash H' computed.
   - H' == H (timeout_height was never in the schema).
   - ecrecover(H', sig) == Alice's address → passes.

8. MsgSend executes at block 600, long after Alice's intended deadline.
```

### Citations

**File:** ethereum/eip712/eip712_legacy.go (L60-103)
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
}
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

**File:** evmd/ante/ante.go (L80-83)
```go
				case "/ethermint.types.v1.ExtensionOptionsWeb3Tx":
					// Deprecated: Handle as normal Cosmos SDK tx, except signature is checked for Legacy EIP712 representation
					anteHandler = newLegacyCosmosAnteHandlerEip712(ctx, options, options.ExtraDecorators...)
				case "/ethermint.types.v1.ExtensionOptionDynamicFeeTx":
```

**File:** evmd/ante/evm_handler.go (L56-57)
```go
		cosmos.NewLegacyEip712SigVerificationDecorator(options.AccountKeeper, options.SignModeHandler),
		authante.NewIncrementSequenceDecorator(options.AccountKeeper),
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
