### Title
Unauthenticated `timeout_height` in Legacy EIP-712 Signature Verification Allows Validity-Window Bypass - (File: `ethereum/eip712/eip712_legacy.go`)

### Summary

The legacy EIP-712 signature verification path (`LegacyEip712SigVerificationDecorator`) builds `txBytes` that include `timeout_height`, but the EIP-712 typed data type schema deliberately omits `timeout_height` from the `Tx` struct definition. Because EIP-712 hashing only covers fields declared in the type schema, `timeout_height` is never incorporated into the signed digest. An attacker who obtains a signed legacy EIP-712 transaction can freely alter `timeout_height` without invalidating the 65-byte `FeePayerSig`, defeating the user's intended transaction expiry window.

### Finding Description

**Root cause — three-file chain:**

**Step 1 — `txBytes` include `timeout_height`.**
In `ante/cosmos/eip712.go` `VerifySignature`, the Amino sign-doc bytes are built with:

```go
txBytes := legacytx.StdSignBytes(
    signerData.ChainID,
    signerData.AccountNumber,
    signerData.Sequence,
    tx.GetTimeoutHeight(),          // ← included in JSON
    legacytx.StdFee{...},
    msgs, tx.GetMemo(),
)
```

`legacytx.StdSignBytes` serialises `timeout_height` into the JSON when it is non-zero (the field carries `omitempty`).

**Step 2 — `LegacyWrapTxToTypedData` omits `timeout_height` from the type schema.**
In `ethereum/eip712/eip712_legacy.go`, `extractMsgTypes` defines the `Tx` struct as:

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

The full `txData` map (which contains `timeout_height` when non-zero) is placed in `typedData.Message`, but EIP-712 hashing (`TypedDataAndHash`) only encodes fields that appear in the type schema. `timeout_height` is therefore silently dropped from the digest.

**Step 3 — Signature verification succeeds regardless of `timeout_height`.**
Back in `ante/cosmos/eip712.go`:

```go
typedData, err := eip712.LegacyWrapTxToTypedData(ethermintCodec, extOpt.TypedDataChainID, msgs[0], txBytes, feeDelegation)
sigHash, _, err := apitypes.TypedDataAndHash(typedData)
...
if !ethcrypto.VerifySignature(pubKey.Bytes(), sigHash, feePayerSig[:len(feePayerSig)-1]) {
    return errorsmod.Wrap(errortypes.ErrorInvalidSigner, "unable to verify signer signature of EIP712 typed data")
}
```

`sigHash` is computed without `timeout_height`, so any value of `timeout_height` in the Cosmos transaction produces the same `sigHash`. The signature check passes unconditionally with respect to `timeout_height`.

**Step 4 — `TxTimeoutHeightDecorator` enforces the (now-attacker-controlled) value.**
`newLegacyCosmosAnteHandlerEip712` in `evmd/ante/evm_handler.go` includes `authante.NewTxTimeoutHeightDecorator()` in the ante chain. This decorator rejects the transaction if `block_height > timeout_height`. Because `timeout_height` is not authenticated, an attacker can set it to `0` (no expiry) or any arbitrary value, and the decorator will enforce the tampered value.

### Impact Explanation

A user who signs a legacy EIP-712 Cosmos transaction with `timeout_height = N` intends the transaction to be invalid after block `N`. An attacker who intercepts the signed transaction (e.g., from the public mempool, a relayer, or a broadcast that was never included) can:

1. **Remove the expiry** — set `timeout_height` to `0`. The `TxTimeoutHeightDecorator` then imposes no deadline, and the transaction can be submitted and executed arbitrarily late. For time-sensitive operations (token swaps at a specific price, governance votes, time-locked transfers), this allows execution after the user believed the authorization had expired.
2. **Shorten the window** — set `timeout_height` to a past block to force a valid transaction to fail, enabling griefing.

This is a direct EIP-712 signer-verification bypass: the signer's intent about the validity window is not cryptographically bound to the signature, matching the "High — EIP-712 authorization… signer verification bypass enabling… forged execution" impact category.

### Likelihood Explanation

- The legacy EIP-712 path (`ExtensionOptionsWeb3Tx`) is the standard path for MetaMask and Web3 wallet users submitting Cosmos transactions.
- `timeout_height` is a standard Cosmos SDK field that users and dApps set for time-sensitive operations.
- The attacker only needs to observe the signed transaction in the mempool or receive it from a relayer before it is included in a block — a realistic, unprivileged position.
- No key material, governance access, or validator collusion is required.

### Recommendation

Include `timeout_height` in the `Tx` EIP-712 type schema in `LegacyWrapTxToTypedData` and ensure the field is populated from `txData` before hashing:

```go
"Tx": {
    {Name: "account_number",  Type: "string"},
    {Name: "chain_id",        Type: "string"},
    {Name: "fee",             Type: "Fee"},
    {Name: "memo",            Type: "string"},
    {Name: "msgs",            Type: "Msg[]"},
    {Name: "sequence",        Type: "string"},
    {Name: "timeout_height",  Type: "string"},  // ← restore
},
```

Ensure `legacytx.StdSignBytes` always serialises `timeout_height` (even when zero, to avoid ambiguity), or normalise the field to `"0"` before hashing so the type schema entry is always populated.

### Proof of Concept

1. User constructs a legacy EIP-712 Cosmos transaction (e.g., `MsgSend`) with `timeout_height = 500` and signs it with MetaMask, producing `FeePayerSig` over the EIP-712 hash that does **not** include `timeout_height`.
2. The transaction is broadcast but not included before block 500.
3. Attacker intercepts the serialised Cosmos transaction from the mempool.
4. Attacker sets `timeout_height = 0` in the transaction body. The `FeePayerSig` bytes are unchanged.
5. Attacker resubmits at block 600. `VerifySignature` in `ante/cosmos/eip712.go` recomputes `sigHash` via `LegacyWrapTxToTypedData` — `timeout_height` is absent from the type schema, so `sigHash` is identical to the one the user signed. Signature verification passes.
6. `TxTimeoutHeightDecorator` sees `timeout_height = 0` and imposes no deadline. The transaction executes, transferring funds the user believed were no longer at risk.

**Relevant code locations:**

- `ante/cosmos/eip712.go` — `VerifySignature`, lines 197–207 (builds `txBytes` with `tx.GetTimeoutHeight()`) and lines 248–256 (computes `sigHash` without `timeout_height`)
- `ethereum/eip712/eip712_legacy.go` — `extractMsgTypes`, lines 129–138 (`timeout_height` commented out of `Tx` type schema)
- `evmd/ante/evm_handler.go` — `newLegacyCosmosAnteHandlerEip712`, line 46 (`TxTimeoutHeightDecorator` enforces the unauthenticated value) [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

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

**File:** evmd/ante/evm_handler.go (L39-57)
```go
	decorators := make([]sdk.AnteDecorator, 0, 15+len(extra))
	decorators = append(decorators,
		cosmos.RejectMessagesDecorator{}, // reject MsgEthereumTxs
		// disable the Msg types that cannot be included on an authz.MsgExec msgs field
		cosmos.NewAuthzLimiterDecorator(options.DisabledAuthzMsgs),
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
