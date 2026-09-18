### Title
Unbounded recursive JSON decoding in `MustSortJSON` allows stack-overflow DoS via `SIGN_MODE_LEGACY_AMINO_JSON` signature verification of Wasm/CosmWasm messages - (File: sei-cosmos/types/utils.go)

### Summary
`sdk.SortJSON`/`sdk.MustSortJSON` decode attacker-controlled JSON into an untyped `interface{}` with the stdlib `encoding/json` decoder, which recurses once per nesting level with no depth limit. [1](#0-0)  This function is invoked by `StdSignBytes`, which is the code path used by `SIGN_MODE_LEGACY_AMINO_JSON` signature verification for every transaction that uses that sign mode. [2](#0-1) [3](#0-2)  `StdSignBytes` calls `legacyMsg.GetSignBytes()` on every message in the tx, and for CosmWasm messages (`MsgExecuteContract`, `MsgInstantiateContract`, etc.) this serializes a `RawContractMessage` field whose `ValidateBasic()` only checks `json.Valid()` — it performs no bound on nesting depth. [4](#0-3)  The bytes produced (including the raw, attacker-supplied Wasm execute-message JSON) are then run through `sdk.MustSortJSON`, hitting the unbounded recursive decode. [5](#0-4) 

### Finding Description
The vulnerability class matches CVE-2025-5302: JSON decoded recursively into a generic/untyped structure with no depth guard, causing the Go runtime to overflow the goroutine stack. In sei-chain this reachable sink is `SortJSON`:
```go
func SortJSON(toSortJSON []byte) ([]byte, error) {
	var c interface{}
	err := json.Unmarshal(toSortJSON, &c)   // unbounded recursion for nested arrays/objects
	...
}
``` [6](#0-5) 

`MustSortJSON` panics rather than returning the error, and is called from `StdSignBytes`, the function that computes the canonical bytes that must be signed/verified for `SIGN_MODE_LEGACY_AMINO_JSON`:
```go
return sdk.MustSortJSON(bz)
``` [2](#0-1) 

`StdSignBytes` is reached from both the legacy amino tx handler and the protobuf-wrapped `signModeLegacyAminoJSONHandler.GetSignBytes` used for `SignMode_SIGN_MODE_LEGACY_AMINO_JSON`, which is a supported sign mode in this fork (confirmed by dedicated test coverage for it, e.g. amino sign-doc canonical bytes for EVM claim messages). [3](#0-2) [7](#0-6) 

Signature verification (`authsigning.VerifySignature`) calls `handler.GetSignBytes(...)` before comparing the resulting bytes against the provided signature bytes — meaning the crash occurs regardless of whether the signature itself is valid:
```go
signBytes, err := handler.GetSignBytes(data.SignMode, signerData, tx)
``` [8](#0-7) 

This is invoked from the ante-handler's signature-verification step for every incoming transaction, on both `CheckTx` (mempool admission) and `DeliverTx`:
```go
err = authsigning.VerifySignature(pubKey, signerData, sig.Data, txConfig.SignModeHandler(), tx)
``` [9](#0-8) 

Before reaching signature verification, the ante pipeline runs `ValidateBasic()` on all messages, but for a Wasm execute/instantiate message this only validates that the embedded JSON is syntactically valid — it never bounds nesting depth:
```go
func (r *RawContractMessage) ValidateBasic() error {
	if r == nil { return ErrEmpty }
	if !json.Valid(*r) { return ErrInvalid }
	return nil
}
``` [10](#0-9) 

Thus an attacker can craft a transaction containing a `MsgExecuteContract` (or similar Wasm message) whose `Msg` field is syntactically valid but nested tens of thousands of array/object levels deep (e.g. `[[[[...]]]]`), select `SIGN_MODE_LEGACY_AMINO_JSON`, and broadcast it. It passes `ValidateBasic`, reaches `SigVerificationDecorator`/`CheckSignatures`, and crashes via unbounded recursion in `json.Unmarshal(..., &interface{})` inside `SortJSON`.

Note: The codebase shows the sei-chain team has already hardened several *other* JSON-decoding surfaces against exactly this bug class — e.g. `maxMuxTracerNestingDepth` for debug tracer JSON [11](#0-10) , `maxArrayDepth`/`maxKeyDepth` for TOML config parsing [12](#0-11) , and `maxDumpDepth` for reflect-based config dumping [13](#0-12) . `SortJSON`/`MustSortJSON` in `sei-cosmos/types/utils.go`, however, has no such guard, and it sits directly on the amino signature-verification hot path for every transaction.

### Impact Explanation
A stack overflow from unbounded recursive decoding into `interface{}` in Go is a fatal runtime error (`runtime: goroutine stack exceeds ... -bytes limit` → `fatal error: stack overflow`), which is **not recoverable** via `defer/recover()`. It terminates the entire process. Since `VerifySignature`/`GetSignBytes` runs inside `CheckTx` on every full node that admits the transaction to its mempool, and again in `DeliverTx` on every validator when the block is processed, a single crafted transaction can crash mempool-accepting full nodes and, if it makes it into a block, crash every validator executing `DeliverTx` — a chain-wide halt. This satisfies the "validator halt" / "block delay beyond 2.5 seconds" impact bar.

### Likelihood Explanation
Reachable by any unprivileged transaction sender: only requires constructing a standard `MsgExecuteContract` (or other Wasm tx message with a `RawContractMessage`/JSON payload) with a deeply nested `msg` JSON body and selecting `SIGN_MODE_LEGACY_AMINO_JSON` when building/broadcasting the transaction — no special permissions, contract deployment, or prior state are needed. No signature validity is required for the crash to occur, since the crash happens while *computing* the expected sign bytes, before the signature comparison. This makes the likelihood high, bounded mainly by whether `SIGN_MODE_LEGACY_AMINO_JSON` is enabled for the target network's `TxConfig` (confirmed supported and exercised by tests for other message types in this repository).

### Recommendation
Add a nesting-depth bound before/inside `SortJSON`, mirroring the pattern already used elsewhere in the codebase (`maxArrayDepth` in `config/seitoml/file.go`, `maxMuxTracerNestingDepth` in `evmrpc/tracers.go`), e.g. use a depth-limited decoder (`json.Decoder.Token()`-based walk with an explicit depth counter) instead of `json.Unmarshal(bz, &interface{})`, or reject any `RawContractMessage`/message JSON blob whose nesting exceeds a small fixed limit (e.g. 32–64) in `ValidateBasic()` before it can reach `GetSignBytes`/`MustSortJSON`. Apply the same depth check universally to any code path that decodes user-controlled JSON into `interface{}`/`map[string]interface{}` without a fixed target struct.

### Proof of Concept
1. Construct a JSON string `payload` consisting of ~50,000 nested arrays, e.g. Python: `payload = ("[" * 50000) + ("]" * 50000)`.
2. Build a `MsgExecuteContract` (sei-wasmd) with `Msg: []byte(payload)` (this passes `RawContractMessage.ValidateBasic()` because `json.Valid` returns true for balanced nested arrays).
3. Build a transaction with this message and sign it using `SIGN_MODE_LEGACY_AMINO_JSON` (signature content is irrelevant — even a garbage/invalid signature works).
4. Broadcast the transaction to a full node's RPC (`broadcast_tx_sync`/`broadcast_tx_async`).
5. During ante-handler signature verification, `authsigning.VerifySignature` → `handler.GetSignBytes` → `legacytx.StdSignBytes` → `msg.GetSignBytes()` → `sdk.MustSortJSON(...)` → `json.Unmarshal(bz, &interface{})` recurses one stack frame per nesting level, exhausting the goroutine stack and triggering `fatal error: stack overflow`, crashing the node process.

### Citations

**File:** sei-cosmos/types/utils.go (L24-50)
```go
// SortedJSON takes any JSON and returns it sorted by keys. Also, all white-spaces
// are removed.
// This method can be used to canonicalize JSON to be returned by GetSignBytes,
// e.g. for the ledger integration.
// If the passed JSON isn't valid it will return an error.
func SortJSON(toSortJSON []byte) ([]byte, error) {
	var c interface{}
	err := json.Unmarshal(toSortJSON, &c)
	if err != nil {
		return nil, err
	}
	js, err := json.Marshal(c)
	if err != nil {
		return nil, err
	}
	return js, nil
}

// MustSortJSON is like SortJSON but panic if an error occurs, e.g., if
// the passed JSON isn't valid.
func MustSortJSON(toSortJSON []byte) []byte {
	js, err := SortJSON(toSortJSON)
	if err != nil {
		panic(err)
	}
	return js
}
```

**File:** sei-cosmos/x/auth/legacy/legacytx/stdsign.go (L52-78)
```go
// StdSignBytes returns the bytes to sign for a transaction.
func StdSignBytes(chainID string, accnum, sequence, timeout uint64, fee StdFee, msgs []sdk.Msg, memo string) []byte {
	msgsBytes := make([]json.RawMessage, 0, len(msgs))
	for _, msg := range msgs {
		legacyMsg, ok := msg.(LegacyMsg)
		if !ok {
			panic(fmt.Errorf("expected %T when using amino JSON", (*LegacyMsg)(nil)))
		}

		msgsBytes = append(msgsBytes, json.RawMessage(legacyMsg.GetSignBytes()))
	}

	bz, err := legacy.Cdc.MarshalAsJSON(StdSignDoc{
		AccountNumber: accnum,
		ChainID:       chainID,
		Fee:           json.RawMessage(fee.Bytes()),
		Memo:          memo,
		Msgs:          msgsBytes,
		Sequence:      sequence,
		TimeoutHeight: timeout,
	})
	if err != nil {
		panic(err)
	}

	return sdk.MustSortJSON(bz)
}
```

**File:** sei-cosmos/x/auth/tx/legacy_amino_json.go (L29-54)
```go
func (s signModeLegacyAminoJSONHandler) GetSignBytes(mode signingtypes.SignMode, data signing.SignerData, tx sdk.Tx) ([]byte, error) {
	if mode != signingtypes.SignMode_SIGN_MODE_LEGACY_AMINO_JSON {
		return nil, fmt.Errorf("expected %s, got %s", signingtypes.SignMode_SIGN_MODE_LEGACY_AMINO_JSON, mode)
	}

	protoTx, ok := tx.(*wrapper)
	if !ok {
		return nil, fmt.Errorf("can only handle a protobuf Tx, got %T", tx)
	}

	if protoTx.txBodyHasUnknownNonCriticals {
		return nil, sdkerrors.Wrap(sdkerrors.ErrInvalidRequest, aminoNonCriticalFieldsError)
	}

	body := protoTx.tx.Body

	if len(body.ExtensionOptions) != 0 || len(body.NonCriticalExtensionOptions) != 0 {
		return nil, sdkerrors.Wrap(sdkerrors.ErrInvalidRequest, "SIGN_MODE_LEGACY_AMINO_JSON does not support protobuf extension options.")
	}

	return legacytx.StdSignBytes(
		data.ChainID, data.AccountNumber, data.Sequence, protoTx.GetTimeoutHeight(),
		legacytx.StdFee{Amount: protoTx.GetFee(), Gas: protoTx.GetGas()},
		tx.GetMsgs(), protoTx.GetMemo(),
	), nil
}
```

**File:** sei-wasmd/x/wasm/types/tx.go (L12-37)
```go
// RawContractMessage defines a json message that is sent or returned by a wasm contract.
// This type can hold any type of bytes. Until validateBasic is called there should not be
// any assumptions made that the data is valid syntax or semantic.
type RawContractMessage []byte

func (r RawContractMessage) MarshalJSON() ([]byte, error) {
	return json.RawMessage(r).MarshalJSON()
}

func (r *RawContractMessage) UnmarshalJSON(b []byte) error {
	if r == nil {
		return errors.New("unmarshalJSON on nil pointer")
	}
	*r = append((*r)[0:0], b...)
	return nil
}

func (r *RawContractMessage) ValidateBasic() error {
	if r == nil {
		return ErrEmpty
	}
	if !json.Valid(*r) {
		return ErrInvalid
	}
	return nil
}
```

**File:** precompiles/solo/solo_test.go (L386-411)
```go
// TestClaimAminoSignDocCanonicalBytes locks the exact SIGN_MODE_LEGACY_AMINO_JSON
// sign-doc bytes the chain computes for claim txs. Wallet integrations must
// reproduce these bytes byte-for-byte for signatures to verify, so any diff here
// is a breaking change for amino signers (e.g. Ledger) even if Go-side tests
// still pass. Notable encoding facts locked in:
//   - msgs are wrapped as {"type":"evm/MsgClaim(Specific)","value":{...}}
//   - asset_type serializes as a JSON number, and zero values (asset_type
//     TYPEUNKNOWN, empty denom/contract_address) are omitted entirely
//   - account_number, sequence, and gas serialize as strings; a nil fee amount
//     normalizes to []; memo is always present
func TestClaimAminoSignDocCanonicalBytes(t *testing.T) {
	txConfig := testkeeper.EVMTestApp.GetTxConfig()
	aminoMode := signing.SignMode_SIGN_MODE_LEGACY_AMINO_JSON
	claimee := sdk.AccAddress([]byte("claimee_____________"))
	claimer := common.HexToAddress("0x0102030405060708090A0B0C0D0E0F1011121314")
	cw20 := sdk.AccAddress([]byte("cw20________________"))
	cw721 := sdk.AccAddress([]byte("cw721_______________"))
	signerData := authsigning.SignerData{ChainID: "sei-test", AccountNumber: 7, Sequence: 3}

	tb := txConfig.NewTxBuilder()
	require.NoError(t, tb.SetMsgs(evmtypes.NewMsgClaim(claimee, claimer)))
	bz, err := txConfig.SignModeHandler().GetSignBytes(aminoMode, signerData, tb.GetTx())
	require.NoError(t, err)
	require.Equal(t,
		`{"account_number":"7","chain_id":"sei-test","fee":{"amount":[],"gas":"0"},"memo":"","msgs":[{"type":"evm/MsgClaim","value":{"claimer":"0x0102030405060708090a0B0c0d0e0f1011121314","sender":"sei1vdkxz6tdv4j47h6lta047h6lta047h6l9yjahw"}}],"sequence":"3"}`,
		string(bz))
```

**File:** sei-cosmos/x/auth/signing/verify.go (L14-24)
```go
func VerifySignature(pubKey cryptotypes.PubKey, signerData SignerData, sigData signing.SignatureData, handler SignModeHandler, tx sdk.Tx) error {
	switch data := sigData.(type) {
	case *signing.SingleSignatureData:
		signBytes, err := handler.GetSignBytes(data.SignMode, signerData, tx)
		if err != nil {
			return err
		}
		if !pubKey.VerifySignature(signBytes, data.Signature) {
			return fmt.Errorf("unable to verify single signer signature")
		}
		return nil
```

**File:** app/ante/cosmos_checktx.go (L503-503)
```go
		err = authsigning.VerifySignature(pubKey, signerData, sig.Data, txConfig.SignModeHandler(), tx)
```

**File:** evmrpc/tracers.go (L287-293)
```go
func validateMuxTraceConfig(raw json.RawMessage, allowed map[string]struct{}, allowJS bool, depth int) error {
	if len(raw) == 0 {
		return nil
	}
	if depth > maxMuxTracerNestingDepth {
		return fmt.Errorf("muxTracer nesting depth exceeds maximum of %d", maxMuxTracerNestingDepth)
	}
```

**File:** config/seitoml/file.go (L262-270)
```go
const (
	// maxFileBytes bounds the bytes Load will read. A file stating every declared key is a few tens
	// of kilobytes.
	maxFileBytes = 1 << 20
	// maxKeyDepth bounds the segments in one key. A setting is a section and a key inside it.
	maxKeyDepth = 8
	// maxArrayDepth bounds nesting inside a value. No setting here is a list of lists.
	maxArrayDepth = 8
)
```

**File:** testutil/configtest/dump.go (L11-14)
```go
// maxDumpDepth bounds recursion. Config structs are shallow, but a fuzzed TOML
// document can nest tables arbitrarily and reaches these functions as
// map[string]any, so the walk needs a floor it cannot fall through.
const maxDumpDepth = 24
```
