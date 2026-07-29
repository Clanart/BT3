## Finding: EIP-712 legacy multi-Msg signature verification derives the shared `Msg` type schema only from `msgs[0]`, allowing message content outside that schema to be excluded from the signed hash

### Summary
`VerifySignature` in `ante/cosmos/eip712.go` builds the EIP-712 hash for **all** cosmos messages of the tx but derives the shared per-array struct schema (`MsgValue`) from only `msgs[0]`. Because `apitypes.TypedDataAndHash` (go-ethereum) hashes array elements strictly according to the declared `Types` schema, any JSON fields present in `msgs[1..n]` that are not part of `msgs[0]`'s schema are silently excluded from the signed digest while still being executed by the state machine after the ante handler passes.

### Finding Description
`VerifySignature` computes `txBytes` from `legacytx.StdSignBytes(..., msgs, ...)` where `msgs := tx.GetMsgs()` — i.e., the **full** list of messages in the tx: [1](#0-0) 

It then builds the EIP-712 typed data by calling `LegacyWrapTxToTypedData(evmCodec, extOpt.TypedDataChainID, msgs[0], txBytes, feeDelegation)`, passing only the first message to derive the schema, while `txBytes` (containing every message's JSON) becomes the `Message` payload: [2](#0-1) 

Inside `LegacyWrapTxToTypedData`/`extractMsgTypes`, the `Tx` type declares `msgs` as `Msg[]`, and `Msg.value` is typed as a single `MsgValue`, whose fields are populated purely by reflecting over `msgs[0]`'s Go struct via `walkFields`/`legacyTraverseFields`: [3](#0-2) 

go-ethereum's EIP-712 array encoding applies the *same* element type (`MsgValue`) to every entry of the `msgs` array. When encoding an array element whose underlying JSON has fields not declared in `MsgValue`, those extra fields are never read and therefore never contribute to the hash pre-image; only fields whose names coincide with `msgs[0]`'s schema are committed. Consequently, if an attacker (e.g., a malicious dApp/frontend that constructs the tx a user signs) chooses `msgs[0]` such that its field names are a subset of a second, malicious message (`msgs[1]`, e.g. `bank.MsgSend` or an ERC20-transfer-style message) with matching primitive types, the extra/differing content of `msgs[1]` (destination address, amount, etc.) is not bound by the signature. The unprivileged code path is the ordinary tx submission → ante handler pipeline; no validator/relayer/admin control is needed.

Neither `VerifySignature` nor `extractMsgTypes`/`LegacyWrapTxToTypedData` verifies that all messages in the tx share the same concrete Go/proto type as `msgs[0]`, nor does it require that the `msgs` array elements are structurally checked against per-element derived types (only a single shared `MsgValue` typedef exists for the whole array).

### Impact Explanation
If exploitable, this allows unauthorized message content to execute post-ante without being cryptographically committed to by the fee payer's/signer's EIP-712 signature, enabling appended bank-send or ERC20-transfer messages to move funds the signer never authorized — a critical theft/unauthorized-extraction impact per the allowed impact gate.

### Likelihood Explanation
Exploitability is constrained by a real precondition: go-ethereum's `EncodeData`/`EncodePrimitiveValue` will error out if a field declared in `MsgValue` is *missing* from a given array element's JSON (type-assertion failure), so `msgs[0]` and `msgs[1..n]` must share matching field names/types for the shared fields while `msgs[1..n]` carries *additional* unmatched fields that get dropped. This requires the attacker to control (or dupe the signer into approving) a specific pairing of message types whose field names line up as a schema subset/superset — plausible given the number of registered Cosmos SDK/EVM message types with overlapping field names (`amount`, addresses, denoms, etc.), but it is not a trivial "any two messages" exploit. Additionally, `LegacyEip712SigVerificationDecorator`/`VerifySignature` is explicitly marked `Deprecated`, with a code comment stating that "As of v10, EIP-712 signature verification is handled by the ethsecp256k1 public key" — this repo's index does not let me confirm whether this legacy decorator is still wired into the live ante handler chain or has been superseded/removed from the active AnteHandler set, which materially affects real-world reachability. [4](#0-3) 

### Recommendation
Derive per-message EIP-712 struct types individually for each message in the array (e.g., `Msg0Value`, `Msg1Value`, ...) rather than a single shared `MsgValue` from `msgs[0]`, and reject the tx if any message's JSON content is not fully representable/committed by its derived type. Alternatively, restrict this legacy EIP-712 signing path to single-message transactions, or explicitly validate that every message is of the identical registered type/schema as `msgs[0]` before accepting the signature (mirroring, and hardening, the sibling `validatePayloadMessages` check used by `ethereum/eip712/encoding.go`, which currently only enforces single-signer, not schema completeness). [5](#0-4) 

### Proof of Concept
1. Craft `msgs[0]` as a message type whose fields (by JSON name and primitive type) are a subset of a target malicious message type, e.g. a message with a single string field named identically to a field in `bank.MsgSend`.
2. Set `msgs[1]` = malicious `bank.MsgSend` (or ERC20-transfer precompile call message) draining funds to attacker's address, sharing the matching field name(s) with `msgs[0]` but carrying additional fields (`to_address`, `amount`) not present in `msgs[0]`'s schema.
3. Get the signer to approve the resulting EIP-712 typed data (`Types` built from `extractMsgTypes(cdc, "MsgValue", msgs[0])`), observing that `Types["MsgValue"]` only contains `msgs[0]`'s fields.
4. Submit the tx through `LegacyEip712SigVerificationDecorator.AnteHandle` → `VerifySignature`; the recomputed `sigHash` matches because the extra `msgs[1]` fields were never part of the original or recomputed hash pre-image.
5. After ante passes, `next(ctx, tx, simulate)` executes both messages, including the unauthorized fund transfer in `msgs[1]`. [6](#0-5)

### Citations

**File:** ante/cosmos/eip712.go (L36-53)
```go
// Deprecated: LegacyEip712SigVerificationDecorator Verify all signatures for a tx and return an error if any are invalid. Note,
// the LegacyEip712SigVerificationDecorator decorator will not get executed on ReCheck.
// NOTE: As of v10, EIP-712 signature verification is handled by the ethsecp256k1 public key (see ethsecp256k1.go)
//
// CONTRACT: Pubkeys are set in context for all signers before this decorator runs
// CONTRACT: Tx must implement SigVerifiableTx interface
type LegacyEip712SigVerificationDecorator struct {
	ak anteinterfaces.AccountKeeper
}

// Deprecated: NewLegacyEip712SigVerificationDecorator creates a new LegacyEip712SigVerificationDecorator
func NewLegacyEip712SigVerificationDecorator(
	ak anteinterfaces.AccountKeeper,
) LegacyEip712SigVerificationDecorator {
	return LegacyEip712SigVerificationDecorator{
		ak: ak,
	}
}
```

**File:** ante/cosmos/eip712.go (L145-151)
```go
	if err := VerifySignature(pubKey, signerData, sig.Data, authSignTx); err != nil {
		errMsg := fmt.Errorf("signature verification failed; please verify account number (%d) and chain-id (%s): %w", accNum, chainID, err)
		return ctx, errorsmod.Wrap(errortypes.ErrUnauthorized, errMsg.Error())
	}

	return next(ctx, tx, simulate)
}
```

**File:** ante/cosmos/eip712.go (L175-190)
```go
		msgs := tx.GetMsgs()
		if len(msgs) == 0 {
			return errorsmod.Wrap(errortypes.ErrNoSignatures, "tx doesn't contain any msgs to verify signature")
		}

		txBytes := legacytx.StdSignBytes(
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

**File:** ante/cosmos/eip712.go (L227-235)
```go
		typedData, err := eip712.LegacyWrapTxToTypedData(evmCodec, extOpt.TypedDataChainID, msgs[0], txBytes, feeDelegation)
		if err != nil {
			return errorsmod.Wrap(err, "failed to create EIP-712 typed data from tx")
		}

		sigHash, _, err := apitypes.TypedDataAndHash(typedData)
		if err != nil {
			return err
		}
```

**File:** ethereum/eip712/eip712_legacy.go (L110-140)
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

	if err := walkFields(cdc, rootTypes, msgTypeName, msg); err != nil {
		return nil, err
	}

	return rootTypes, nil
}
```

**File:** ethereum/eip712/encoding.go (L200-229)
```go
// validatePayloadMessages ensures that the transaction messages can be represented in an EIP-712
// encoding by checking that messages exist and share a single signer.
func validatePayloadMessages(msgs []sdk.Msg) error {
	if len(msgs) == 0 {
		return errors.New("unable to build EIP-712 payload: transaction does contain any messages")
	}

	var msgSigner sdk.AccAddress

	for i, m := range msgs {
		signers, _, err := protoCodec.GetMsgV1Signers(m)
		if err != nil {
			return fmt.Errorf("error getting signers. %w", err)
		}
		if len(signers) != 1 {
			return errors.New("unable to build EIP-712 payload: expect exactly 1 signer")
		}

		if i == 0 {
			msgSigner = signers[0]
			continue
		}

		if !msgSigner.Equals(sdk.AccAddress(signers[0])) {
			return errors.New("unable to build EIP-712 payload: multiple signers detected")
		}
	}

	return nil
}
```
