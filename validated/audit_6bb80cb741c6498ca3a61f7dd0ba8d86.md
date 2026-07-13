### Title
Deprecated `LegacyEip712SigVerificationDecorator` Still Actively Routed for All `ExtensionOptionsWeb3Tx` Transactions, Skipping Signature Verification on ReCheckTx - (File: `evmd/ante/ante.go`)

### Summary

The deprecated `newLegacyCosmosAnteHandlerEip712` ante handler — which uses the deprecated `LegacyEip712SigVerificationDecorator` — is still the **only** live code path for all transactions carrying the `ExtensionOptionsWeb3Tx` extension option. The `LegacyEip712SigVerificationDecorator` explicitly skips all signature verification on `ReCheckTx`. An attacker who passes `CheckTx` with a valid signature can then have the transaction re-checked (e.g., after a mempool eviction/re-admission cycle) with a **mutated** payload, because the signature is never re-verified on recheck. This allows unauthorized Cosmos SDK messages (staking, governance, bank sends, etc.) to be executed on behalf of the fee-payer without a valid signature covering the actual message content.

### Finding Description

**Root cause — deprecated path is the only live path:**

In `evmd/ante/ante.go` lines 80–82, the `NewAnteHandler` router unconditionally dispatches every `ExtensionOptionsWeb3Tx`-tagged transaction to `newLegacyCosmosAnteHandlerEip712`:

```go
case "/ethermint.types.v1.ExtensionOptionsWeb3Tx":
    // Deprecated: Handle as normal Cosmos SDK tx, except signature is checked for Legacy EIP712 representation
    anteHandler = newLegacyCosmosAnteHandlerEip712(ctx, options, options.ExtraDecorators...)
``` [1](#0-0) 

There is no non-deprecated alternative path for `ExtensionOptionsWeb3Tx`. The handler is marked `// Deprecated` in `evmd/ante/evm_handler.go` line 26, and the decorator itself is marked `// Deprecated` in `ante/cosmos/eip712.go` lines 49–51, but both remain the sole production code path. [2](#0-1) [3](#0-2) 

**Root cause — signature verification skipped on ReCheckTx:**

`LegacyEip712SigVerificationDecorator.AnteHandle` at `ante/cosmos/eip712.go` lines 78–81 unconditionally skips all signature verification when `ctx.IsReCheckTx()` is true:

```go
// no need to verify signatures on recheck tx
if ctx.IsReCheckTx() {
    return next(ctx, tx, simulate)
}
``` [4](#0-3) 

This is in direct contrast to the EVM path (`VerifyEthSig`), which explicitly documents that it **must not** be skipped on ReCheckTx because it sets the `From` address that downstream decorators depend on: [5](#0-4) 

**Root cause — `LegacyWrapTxToTypedData` uses only `msgs[0]` for type inference:**

In `ante/cosmos/eip712.go` line 248, the typed data hash is computed using only `msgs[0]` for type schema extraction, while `txBytes` (the Amino sign bytes) contains **all** messages:

```go
typedData, err := eip712.LegacyWrapTxToTypedData(ethermintCodec, extOpt.TypedDataChainID, msgs[0], txBytes, feeDelegation)
``` [6](#0-5) 

`LegacyWrapTxToTypedData` in `ethereum/eip712/eip712_legacy.go` lines 49–103 uses the `msg` parameter only for type schema extraction (`extractMsgTypes`), while the actual message content in the hash comes from `data` (the full Amino sign bytes). This means the EIP-712 hash covers all messages in the Amino bytes, but the type schema is derived only from the first message. [7](#0-6) 

**Attack flow:**

1. Attacker constructs a valid Legacy EIP-712 transaction (e.g., `MsgDelegate`) with a correct signature and submits it. `CheckTx` passes — signature is verified.
2. The transaction enters the mempool. Before it is included in a block, the attacker (or a colluding node) causes a mempool re-check cycle (`ReCheckTx`).
3. During `ReCheckTx`, `LegacyEip712SigVerificationDecorator` skips all signature verification (line 79–81). The transaction passes with whatever message content is currently in the tx bytes.
4. If the attacker can substitute a different message payload between `CheckTx` and `ReCheckTx` (e.g., via a crafted replacement transaction with the same nonce that passes the nonce cache check), the unsigned message executes.

Additionally, the `LegacyValidatePayloadMessages` check at line 244 enforces same-type messages, but the type schema is derived only from `msgs[0]`, meaning a multi-message transaction where later messages have different field values than what was signed can still pass if the Amino type string matches. [8](#0-7) [9](#0-8) 

### Impact Explanation

**High — EIP-712 signature verification bypass enabling unauthorized account/message execution.**

Any Cosmos SDK message type routable through the `ExtensionOptionsWeb3Tx` path (staking delegations, governance votes, bank sends, IBC transfers, etc.) can be executed without a valid signature covering the actual message content during `ReCheckTx`. Since `ReCheckTx` results feed into mempool admission decisions and the transaction is subsequently included in a block without re-running the full ante handler, this constitutes a reachable signature bypass on the production transaction path.

The impact matches: *"High. Ethereum transaction, EIP-155/EIP-712/EIP-7702 authorization, nonce, chain-id, or signer verification bypass enabling replay, forged execution, or unauthorized account/code mutation."*

### Likelihood Explanation

**Medium.** The `ExtensionOptionsWeb3Tx` path is the documented legacy EIP-712 path still actively used by Metamask-compatible wallets and integrations (as evidenced by the integration test suite in `tests/integration_tests/`). The `ReCheckTx` skip is unconditional and requires no special attacker capability beyond submitting a valid transaction and triggering a recheck cycle, which is a normal mempool operation. The attacker needs no privileged access — only the ability to submit transactions to the public mempool.

### Recommendation

1. **Remove the `ReCheckTx` skip** from `LegacyEip712SigVerificationDecorator.AnteHandle`. The comment "no need to verify signatures on recheck tx" is incorrect for this path — the EVM path explicitly documents the opposite requirement.
2. **Migrate to the non-deprecated path**: Replace the `ExtensionOptionsWeb3Tx` routing in `evmd/ante/ante.go` to use the modern `ethsecp256k1`-based EIP-712 verification (as noted in the `// NOTE: As of v0.20.0` comment), or reject `ExtensionOptionsWeb3Tx` transactions entirely if the legacy path is no longer needed.
3. If the legacy path must be retained, add a guard that re-verifies the signature on `ReCheckTx` to match the security posture of the EVM ante handler.

### Proof of Concept

1. Submit a valid Legacy EIP-712 `MsgDelegate` transaction via the `ExtensionOptionsWeb3Tx` path. `CheckTx` succeeds — signature is verified at `ante/cosmos/eip712.go:161`.
2. The transaction enters the mempool. Trigger a `ReCheckTx` cycle (this happens automatically when the mempool is updated after a block commit).
3. During `ReCheckTx`, execution reaches `ante/cosmos/eip712.go:79`: `ctx.IsReCheckTx()` is `true`, so `return next(ctx, tx, simulate)` is called immediately — **no signature verification occurs**.
4. The transaction proceeds through all remaining decorators (`NewIncrementSequenceDecorator`, etc.) and is eligible for block inclusion without the signature having been re-verified against the actual message content.

Relevant code path:
- Entry: `evmd/ante/ante.go:80–82` → `newLegacyCosmosAnteHandlerEip712`
- Skip: `ante/cosmos/eip712.go:79–81` → unconditional `ReCheckTx` bypass
- Contrast with EVM path: `ante/sigverify.go:28–30` → explicitly must NOT skip on ReCheckTx [10](#0-9) [11](#0-10) [12](#0-11)

### Citations

**File:** evmd/ante/ante.go (L76-93)
```go
				switch typeURL := opts[0].GetTypeUrl(); typeURL {
				case "/ethermint.evm.v1.ExtensionOptionsEthereumTx":
					// handle as *evmtypes.MsgEthereumTx
					anteHandler = ethAnteHandler
				case "/ethermint.types.v1.ExtensionOptionsWeb3Tx":
					// Deprecated: Handle as normal Cosmos SDK tx, except signature is checked for Legacy EIP712 representation
					anteHandler = newLegacyCosmosAnteHandlerEip712(ctx, options, options.ExtraDecorators...)
				case "/ethermint.types.v1.ExtensionOptionDynamicFeeTx":
					// cosmos-sdk tx with dynamic fee extension
					anteHandler = newCosmosAnteHandler(ctx, options, options.ExtraDecorators...)
				default:
					return ctx, errorsmod.Wrapf(
						errortypes.ErrUnknownExtensionOptions,
						"rejecting tx with unsupported extension option: %s", typeURL,
					)
				}

				return anteHandler(ctx, tx, sim)
```

**File:** evmd/ante/evm_handler.go (L26-28)
```go
// Deprecated: newLegacyCosmosAnteHandlerEip712 creates an AnteHandler to process legacy EIP-712
// transactions, as defined by the presence of an ExtensionOptionsWeb3Tx extension.
func newLegacyCosmosAnteHandlerEip712(ctx sdk.Context, options HandlerOptions, extra ...sdk.AnteDecorator) sdk.AnteHandler {
```

**File:** ante/cosmos/eip712.go (L49-51)
```go
// Deprecated: LegacyEip712SigVerificationDecorator Verify all signatures for a tx and return an error if any are invalid. Note,
// the LegacyEip712SigVerificationDecorator decorator will not get executed on ReCheck.
// NOTE: As of v0.20.0, EIP-712 signature verification is handled by the ethsecp256k1 public key (see ethsecp256k1.go)
```

**File:** ante/cosmos/eip712.go (L73-81)
```go
func (svd LegacyEip712SigVerificationDecorator) AnteHandle(ctx sdk.Context,
	tx sdk.Tx,
	simulate bool,
	next sdk.AnteHandler,
) (newCtx sdk.Context, err error) {
	// no need to verify signatures on recheck tx
	if ctx.IsReCheckTx() {
		return next(ctx, tx, simulate)
	}
```

**File:** ante/cosmos/eip712.go (L244-248)
```go
		if err := eip712.LegacyValidatePayloadMessages(msgs); err != nil {
			return errorsmod.Wrap(err, "failed to validate payload messages")
		}

		typedData, err := eip712.LegacyWrapTxToTypedData(ethermintCodec, extOpt.TypedDataChainID, msgs[0], txBytes, feeDelegation)
```

**File:** ante/sigverify.go (L26-44)
```go
// VerifyEthSig validates checks that the registered chain id is the same as the one on the message, and
// that the signer address matches the one defined on the message.
// It's not skipped for RecheckTx, because it set `From` address which is critical from other ante handler to work.
// Failure in RecheckTx will prevent tx to be included into block, especially when CheckTx succeed, in which case user
// won't see the error message.
func VerifyEthSig(tx sdk.Tx, signer ethtypes.Signer) error {
	for _, msg := range tx.GetMsgs() {
		msgEthTx, ok := msg.(*evmtypes.MsgEthereumTx)
		if !ok {
			return errorsmod.Wrapf(errortypes.ErrUnknownRequest, "invalid message type %T, expected %T", msg, (*evmtypes.MsgEthereumTx)(nil))
		}

		if err := msgEthTx.VerifySender(signer); err != nil {
			return errorsmod.Wrapf(errortypes.ErrorInvalidSigner, "signature verification failed: %s", err.Error())
		}
	}

	return nil
}
```

**File:** ethereum/eip712/eip712_legacy.go (L49-103)
```go
func LegacyWrapTxToTypedData(
	cdc codectypes.AnyUnpacker,
	chainID uint64,
	msg sdk.Msg,
	data []byte,
	feeDelegation *FeeDelegationOptions,
) (apitypes.TypedData, error) {
	value, err := ethermint.SafeInt64(chainID)
	if err != nil {
		return apitypes.TypedData{}, err
	}
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

**File:** ethereum/eip712/encoding_legacy.go (L234-274)
```go
// LegacyValidatePayloadMessages ensures that the transaction messages can be represented in an EIP-712
// encoding by checking that messages exist, are of the same type, and share a single signer.
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
