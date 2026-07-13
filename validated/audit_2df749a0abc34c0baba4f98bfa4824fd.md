### Title
Legacy EIP-712 Signature Does Not Commit to `timeout_height`, Enabling Deadline-Stripping Replay - (File: `ante/cosmos/eip712.go`, `ethereum/eip712/eip712_legacy.go`, `ethereum/eip712/types.go`)

### Summary
The EIP-712 typed-data schema used for legacy Cosmos EIP-712 transactions deliberately omits `timeout_height` from the `"Tx"` struct type definition. Because EIP-712 `hashStruct` only hashes fields declared in the type schema, a non-zero `timeout_height` present in the Amino sign-doc JSON is silently discarded during hash computation. An attacker who observes a signed legacy EIP-712 transaction in the mempool can strip its `timeout_height` (set it to `0`), reuse the original `FeePayerSig`, and submit the modified transaction after the user's intended deadline — bypassing the user's expiry protection entirely.

### Finding Description

**Root cause — schema omission:**

In `ethereum/eip712/types.go` the `"Tx"` EIP-712 type is defined as:

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

The identical omission exists in `ethereum/eip712/eip712_legacy.go`:

```go
"Tx": {
    ...
    {Name: "sequence", Type: "string"},
    // Note timeout_height was removed because it was not getting filled with the legacyTx
    // {Name: "timeout_height", Type: "string"},
},
```

**Signing path — `timeout_height` is present in the data but absent from the schema:**

In `ante/cosmos/eip712.go` `VerifySignature`, the Amino sign-bytes are built with the actual `timeout_height`:

```go
txBytes := legacytx.StdSignBytes(
    signerData.ChainID,
    signerData.AccountNumber,
    signerData.Sequence,
    tx.GetTimeoutHeight(),   // ← included in JSON when non-zero
    legacytx.StdFee{...},
    msgs, tx.GetMemo(),
)
```

These bytes are then passed to `LegacyWrapTxToTypedData`, which JSON-unmarshals them into `txData`. The resulting map contains `"timeout_height"` when it is non-zero. However, because `"timeout_height"` is not declared in the `"Tx"` EIP-712 type, `apitypes.TypedDataAndHash` silently ignores it when computing `sigHash`. The signature therefore commits to the same hash regardless of whether `timeout_height` is `0` or `1000`.

**Attack path:**

1. User constructs a legacy EIP-712 Cosmos tx (e.g., `bank.MsgSend`) with `timeout_height = 1000` and signs it, producing `extOpt.FeePayerSig`.
2. The tx enters the mempool. `authante.NewTxTimeoutHeightDecorator()` (present in `newLegacyCosmosAnteHandlerEip712`) enforces the deadline and the tx is rejected after block 1000.
3. Attacker copies the tx, sets `timeout_height = 0` (no expiry), keeps the same `FeePayerSig`.
4. In `VerifySignature`, `txBytes` is rebuilt with `timeout_height = 0`. The EIP-712 hash is identical to the original (since `timeout_height` was never part of the schema). `VerifySignature` succeeds.
5. `TxTimeoutHeightDecorator` passes (0 means no expiry). The tx executes after the user's intended deadline.

**Why the Protobuf guard does not help here:**

`decodeProtobufSignDoc` in `ethereum/eip712/encoding.go` does reject `body.TimeoutHeight != 0`, but that guard is on the `GetEIP712BytesForMsg` / `LegacyGetEIP712BytesForMsg` code path used by `verifySignatureAsEIP712` in `crypto/ethsecp256k1/ethsecp256k1.go`. The `VerifySignature` function in `ante/cosmos/eip712.go` calls `LegacyWrapTxToTypedData` directly with Amino-encoded `txBytes` and never invokes that guard.

### Impact Explanation
An attacker can replay any signed legacy EIP-712 Cosmos transaction past its user-intended `timeout_height` deadline. Affected message types include `bank.MsgSend` (unauthorized fund transfers), `staking.MsgDelegate`/`MsgUndelegate`, and `gov.MsgVote`. This constitutes unauthorized execution of Cosmos bank/staking/governance messages beyond the signer's intended authorization window — matching the **High** impact category: "EIP-712 authorization… signer verification bypass enabling replay, forged execution, or unauthorized account/code mutation."

### Likelihood Explanation
The legacy EIP-712 path (`ExtensionOptionsWeb3Tx`) is deprecated but remains fully active in the ante handler. Any user who sets `timeout_height` on a legacy EIP-712 Cosmos tx (e.g., to protect a time-sensitive governance vote or a conditional transfer) is vulnerable. MEV bots routinely monitor the mempool for signed transactions; stripping `timeout_height` and resubmitting requires only modifying one field in the Cosmos tx wrapper while reusing the existing `FeePayerSig`. No privileged access is required.

### Recommendation
Add `timeout_height` to the `"Tx"` EIP-712 type schema in both `ethereum/eip712/types.go` and `ethereum/eip712/eip712_legacy.go`:

```go
"Tx": {
    {Name: "account_number",  Type: "string"},
    {Name: "chain_id",        Type: "string"},
    {Name: "fee",             Type: "Fee"},
    {Name: "memo",            Type: "string"},
    {Name: "sequence",        Type: "string"},
    {Name: "timeout_height",  Type: "string"},  // ADD THIS
},
```

Alternatively, add an explicit check in `VerifySignature` (analogous to the Protobuf path) that rejects any legacy EIP-712 tx with `tx.GetTimeoutHeight() != 0` until the field is properly committed to in the schema.

### Proof of Concept

1. **User signs** a legacy EIP-712 `bank.MsgSend` with `timeout_height = 1000`. The EIP-712 hash is computed over the `"Tx"` struct, which does not include `timeout_height`. The resulting `FeePayerSig` is `S`.

2. **Attacker observes** the signed tx in the mempool before block 1000. The tx is not included (e.g., due to low priority or deliberate delay).

3. **After block 1000**, attacker constructs a new Cosmos tx with identical fields but `timeout_height = 0`, and sets `extOpt.FeePayerSig = S`.

4. **In `VerifySignature`** (`ante/cosmos/eip712.go:197-207`):
   - `txBytes` is rebuilt with `timeout_height = 0` → `StdSignBytes` omits the field
   - `LegacyWrapTxToTypedData` produces the same `txData` structure (no `timeout_height` key)
   - `TypedDataAndHash` produces the same `sigHash` as step 1
   - `VerifySignature` succeeds with `S`

5. **`TxTimeoutHeightDecorator`** passes (0 = no expiry). The `MsgSend` executes, transferring funds the user intended to authorize only before block 1000. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4) [6](#0-5)

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

**File:** ante/cosmos/eip712.go (L248-256)
```go
		typedData, err := eip712.LegacyWrapTxToTypedData(ethermintCodec, extOpt.TypedDataChainID, msgs[0], txBytes, feeDelegation)
		if err != nil {
			return errorsmod.Wrap(err, "failed to create EIP-712 typed data from tx")
		}

		sigHash, _, err := apitypes.TypedDataAndHash(typedData)
		if err != nil {
			return err
		}
```

**File:** evmd/ante/evm_handler.go (L44-57)
```go
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
