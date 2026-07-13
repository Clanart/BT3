### Title
EIP-712 `timeout_height` Exclusion Enables Replay of Expired Legacy Cosmos Transactions — (`ethereum/eip712/eip712_legacy.go`, `ante/cosmos/eip712.go`)

---

### Summary

The legacy EIP-712 Cosmos ante handler computes the EIP-712 hash over a `Tx` type schema that explicitly omits `timeout_height`, while simultaneously passing `tx.GetTimeoutHeight()` into the Amino sign bytes that seed the message payload. Because EIP-712 hashing ignores message fields absent from the type schema, any change to `timeout_height` produces an identical hash. An attacker who intercepts a broadcast-but-unexecuted transaction that carried a non-zero `timeout_height` can strip the expiry, substitute a future block height, and resubmit — the signature still verifies and the transaction executes.

---

### Finding Description

**Step 1 — `timeout_height` is present in the Amino message but absent from the EIP-712 type schema.**

`VerifySignature` in `ante/cosmos/eip712.go` builds the Amino JSON bytes with the live `timeout_height`:

```go
txBytes := legacytx.StdSignBytes(
    signerData.ChainID,
    signerData.AccountNumber,
    signerData.Sequence,
    tx.GetTimeoutHeight(),   // ← included in JSON payload
    legacytx.StdFee{Amount: tx.GetFee(), Gas: tx.GetGas()},
    msgs, tx.GetMemo(),
)
``` [1](#0-0) 

Those bytes are then passed verbatim as the `data` argument to `LegacyWrapTxToTypedData`, which unmarshals them into the EIP-712 `Message` map. [2](#0-1) 

However, the `"Tx"` type definition in `extractMsgTypes` explicitly omits `timeout_height`:

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
``` [3](#0-2) 

The same omission exists in the non-legacy `createEIP712Types`: [4](#0-3) 

Per EIP-712, `encodeData` only hashes fields declared in the type schema. A field present in the message but absent from the schema is silently ignored. Therefore `timeout_height=0` and `timeout_height=9999` produce an identical `sigHash`.

**Step 2 — The legacy ante handler is wired into the production chain.**

`newLegacyCosmosAnteHandlerEip712` (marked deprecated but still present and callable) installs `NewLegacyEip712SigVerificationDecorator` after `NewTxTimeoutHeightDecorator`:

```go
authante.NewTxTimeoutHeightDecorator(),
...
cosmos.NewLegacyEip712SigVerificationDecorator(options.AccountKeeper, options.SignModeHandler),
``` [5](#0-4) 

**Step 3 — The Protobuf path has a guard; the Amino path does not.**

`decodeProtobufSignDoc` (and its legacy counterpart) explicitly rejects any sign doc with `TimeoutHeight != 0`:

```go
if body.TimeoutHeight != 0 || ... {
    return apitypes.TypedData{}, errors.New(
        "body contains unsupported fields: TimeoutHeight, ...")
}
``` [6](#0-5) [7](#0-6) 

No equivalent guard exists in the Amino path (`legacyDecodeAminoSignDoc` / `VerifySignature`). The Amino path accepts any `timeout_height` value and the hash is unaffected.

---

### Impact Explanation

An attacker who observes a broadcast EIP-712 Cosmos transaction with `timeout_height=N` that was never included in a block (e.g., it was dropped from the mempool after block N) can:

1. Reconstruct the raw transaction bytes.
2. Replace `timeout_height=N` with `timeout_height=M` where `M > current_block`.
3. Resubmit. `TxTimeoutHeightDecorator` passes. The EIP-712 hash is identical to the original. The signature verifies. The transaction executes.

Because the victim's sequence number was never incremented (the original tx never landed), the replayed tx is accepted and any fund transfer, delegation, or other Cosmos message it contained executes without the victim's renewed consent. This satisfies the **High** impact criterion: EIP-712 authorization bypass enabling replay / unauthorized account mutation.

---

### Likelihood Explanation

- Requires no privileged access; any observer of the p2p mempool or public RPC can capture the original transaction.
- The victim must have used a non-zero `timeout_height` and the tx must have expired without executing — a common pattern for time-bounded operations.
- The attacker only needs to modify one field in the serialized tx body and rebroadcast; no cryptographic material is needed.
- The `newLegacyCosmosAnteHandlerEip712` path is marked deprecated but remains in production code and is callable.

---

### Recommendation

1. **Add a guard in `VerifySignature`**: reject any EIP-712 legacy Amino tx where `tx.GetTimeoutHeight() != 0`, mirroring the Protobuf path guard in `decodeProtobufSignDoc`.
2. **Restore `timeout_height` in the `"Tx"` type schema** for both `eip712_legacy.go` and `types.go`, and ensure the Amino JSON always serialises it (even when zero, as a string `"0"`), so the field is bound in the hash.
3. Remove or gate `newLegacyCosmosAnteHandlerEip712` behind a compile-time or runtime flag if it is no longer intended for production use.

---

### Proof of Concept

```go
// Pseudo-test: same sig verifies for two different timeout_heights
txA := buildLegacyEIP712Tx(timeoutHeight=500, sequence=3)  // expired
sig := victimSign(txA)  // EIP-712 hash H

txB := cloneTx(txA, timeoutHeight=99999)  // future block
hashB := computeEIP712Hash(txB)           // also H — timeout_height not in schema

assert(hashB == computeEIP712Hash(txA))   // passes
assert(ecrecover(hashB, sig) == victim)   // passes → replay succeeds
```

The invariant "all fields affecting tx validity must be bound in the EIP-712 signed hash" is violated: `timeout_height` controls whether `TxTimeoutHeightDecorator` accepts the tx, yet it is not committed to in the signature.

### Citations

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

**File:** evmd/ante/evm_handler.go (L46-56)
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
