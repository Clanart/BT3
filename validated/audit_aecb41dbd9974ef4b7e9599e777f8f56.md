### Title
Ante-handler trusts externally-supplied, signature-unverified `Derived` sender identity on `MsgEVMTransaction`, allowing sender-address spoofing - ([File: x/evm/ante/preprocess.go])

### Summary
`MsgEVMTransaction` carries a `Derived` field (`SenderEVMAddr`, `SenderSeiAddr`, `PubKey`, `Version`, `IsAssociate`) that is a serialized protobuf field of the message, not a value computed exclusively server-side after unmarshalling. `ValidateBasic` and the preprocessing logic treat a message whose `Derived` is already non-nil (with a non-nil `PubKey`) as "already preprocessed" and skip signature recovery entirely, trusting the caller-supplied sender identity without ever verifying that the embedded `PubKey`/addresses were actually derived from the transaction's ECDSA signature.

### Finding Description
The bug class in the H2O report is: a handler builds a critical value (an upstream request) from a struct field that was never properly initialized/validated for a malformed/unusual input, and downstream code trusts that field as if it had gone through the normal validation path.

The Sei analog is structurally similar: the EVM ante pipeline computes the transaction sender identity (`Derived.SenderEVMAddr`, `Derived.SenderSeiAddr`, `Derived.PubKey`) from ECDSA signature recovery in `PreprocessUnpacked`. However, if `msg.Derived` is already non-nil when the message arrives (i.e., supplied directly by the submitter inside the wire-encoded `MsgEVMTransaction`, since `Derived` is a protobuf field on the message), the function short-circuits and skips signature recovery entirely, only checking that `PubKey` is non-nil - not that it is cryptographically consistent with the transaction signature or the claimed addresses: [1](#0-0) 

The same trust-without-verification pattern is duplicated in the DeliverTx path: [2](#0-1) 

And `ValidateBasic` (called from `CheckTx`/mempool ingestion) only requires that `PubKey` be non-nil, never checking it against a signature: [3](#0-2) 

Because `Derived` is a serialized field of `MsgEVMTransaction` (confirmed present with a protobuf tag in `x/evm/types/tx.pb.go` and `giga/deps/xevm/types/tx.pb.go`), an unprivileged submitter can hand-craft a transaction bytes blob in which `Data` contains a validly-signed-looking (or even malformed) `TxData`, but `Derived` is pre-populated with:
- `SenderEVMAddr` / `SenderSeiAddr` set to any address the attacker wants to impersonate,
- `PubKey` set to an arbitrary non-nil public key (not required to match the claimed addresses or the tx signature),
- `Version` / `IsAssociate` chosen to route to the desired code path.

Once through `ValidateBasic`, both `EVMPreprocessDecorator.AnteHandle` (CheckTx/Deliver ante router path) and `EvmDeliverTxAnte`/`EvmDeliverHandleSignatures` (deliver path) accept this forged identity as authoritative and proceed to `AssociateAddress`/state execution using the attacker-chosen `SenderEVMAddr`/`SenderSeiAddr`, without ever re-deriving the sender from `ethtx.RawSignatureValues` via `helpers.GetAddresses`.

### Impact Explanation
If exploitable end-to-end, this allows a transaction sender to impersonate an arbitrary Sei/EVM address for purposes of nonce/fee/state execution logic downstream of preprocessing, since the rest of the pipeline (`EVMSigVerifyDecorator`, `EVMFeeCheckDecorator`, `GasDecorator`, `BasicDecorator`) reads `msg.Derived.SenderEVMAddr` as ground truth (e.g., `x/evm/ante/sig.go:33`, `x/evm/ante/fee.go:71`) rather than re-verifying the ECDSA signature against the claimed address. This is a classic "unauthorized transfer/impersonation via forged sender identity" class bug — potentially enabling spending from or acting as another account without their private key, which is fund-loss-class impact.

### Likelihood Explanation
Reachability requires only that an unprivileged client submit a raw `MsgEVMTransaction` with `Derived` pre-populated — this is possible with any RPC/gRPC broadcast path that accepts a serialized `sdk.Tx`; no special privilege is required. The likelihood of practical exploitation depends on downstream code (not fully traced here) still requiring a valid signature check elsewhere (e.g., `EVMSigVerifyDecorator` checks nonce but not signature; the actual ECDSA verification appears to occur only inside the "not already preprocessed" branch of `PreprocessUnpacked`). Given the explicit `// already preprocessed` comment and short-circuit, this looks like an intentional optimization for internal re-entrant calls (e.g., `Preprocess` called twice for the same tx object within a single process, or by the Giga executor) rather than a control that assumes `Derived` can never come from the wire. Whether the ante/mempool/consensus decode path zeroes or rejects an externally-populated `Derived` before it reaches `ValidateBasic`/`PreprocessUnpacked` could not be fully confirmed from the available index; a Devin session with full repo access should trace `sdk.Tx` decoding (`TxDecoder`) to confirm whether `Derived` survives decode of an attacker-supplied binary transaction.

### Recommendation
- Never trust a `Derived` value that originates from wire-decoded transaction data. Only allow `Derived` to be set by the preprocessing decorator itself; strip/ignore any `Derived` value already present in a transaction that entered from an external RPC/gRPC/CheckTx boundary, or always re-verify `PubKey`/`SenderEVMAddr`/`SenderSeiAddr` against a fresh ECDSA recovery over the transaction signature regardless of whether `Derived` is already set.
- If the "already preprocessed" fast path is required for internal re-entrancy (e.g., CheckTx → DeliverTx reusing the same in-memory message pointer within one process), gate it on an unexported/non-serializable flag or object identity rather than on the mere presence of a non-nil `Derived`/`PubKey` in the deserialized message.
- Add a decoder-level rule (in the `TxDecoder`/`UnpackInterfaces` path) that rejects any `MsgEVMTransaction` arriving with a non-nil `Derived` field from the wire.

### Proof of Concept
Conceptual (pending confirmation that `Derived` survives the transaction decode path used by `CheckTx`):
1. Craft `TxData` for a legitimate/attacker-controlled EVM transaction.
2. Build a `MsgEVMTransaction{ Data: txDataAny, Derived: &derived.Derived{ SenderEVMAddr: <victimEVMAddr>, SenderSeiAddr: <victimSeiAddr>, PubKey: &secp256k1.PubKey{Key: <attacker-chosen bytes>}, Version: derived.Cancun, IsAssociate: false } }`.
3. Wrap in a `sdk.Tx`, encode, and broadcast via the standard broadcast endpoint.
4. Observe `ValidateBasic` (`x/evm/types/message_evm_transaction.go:44-47`) accept it because `PubKey != nil`.
5. Observe `PreprocessUnpacked` (`x/evm/ante/preprocess.go:171-178`) / `EvmDeliverHandleSignatures` (`app/ante/evm_delivertx.go:57-71`) skip signature recovery and use the forged `SenderEVMAddr`/`SenderSeiAddr` for subsequent association, fee, and nonce processing. [4](#0-3) [2](#0-1) [5](#0-4)

### Citations

**File:** x/evm/ante/preprocess.go (L163-178)
```go
// stateless
func Preprocess(ctx sdk.Context, msgEVMTransaction *evmtypes.MsgEVMTransaction, chainID *big.Int, isBlockTest bool) error {
	return PreprocessUnpacked(ctx, msgEVMTransaction, chainID, isBlockTest, nil)
}

// PreprocessUnpacked does the same thing as Preprocess but accepts already unpacked txData to save computation
// if txData is nil, it will unpack from msgEVMTransaction.Data.
func PreprocessUnpacked(ctx sdk.Context, msgEVMTransaction *evmtypes.MsgEVMTransaction, chainID *big.Int, isBlockTest bool, txData ethtx.TxData) error {
	if msgEVMTransaction.Derived != nil {
		if msgEVMTransaction.Derived.PubKey == nil {
			// this means the message has `Derived` set from the outside, in which case we should reject
			return sdkerrors.ErrInvalidPubKey
		}
		// already preprocessed
		return nil
	}
```

**File:** app/ante/evm_delivertx.go (L56-71)
```go
func EvmDeliverHandleSignatures(ctx sdk.Context, ek *evmkeeper.Keeper, txData ethtx.TxData, chainID *big.Int, msg *evmtypes.MsgEVMTransaction) (common.Address, sdk.AccAddress, derived.SignerVersion, error) {
	if msg.Derived != nil {
		if msg.Derived.PubKey == nil {
			return common.Address{}, nil, 0, sdkerrors.ErrInvalidPubKey
		}
		evmAddr := msg.Derived.SenderEVMAddr
		seiAddr := msg.Derived.SenderSeiAddr
		version := msg.Derived.Version
		if err := AssociateAddress(ctx, ek, evmAddr, seiAddr, msg.Derived.PubKey); err != nil {
			return evmAddr, seiAddr, version, err
		}
		if ek.EthReplayConfig.Enabled {
			ek.PrepareReplayedAddr(ctx, evmAddr)
		}
		return evmAddr, seiAddr, version, nil
	}
```

**File:** x/evm/types/message_evm_transaction.go (L44-65)
```go
func (msg *MsgEVMTransaction) ValidateBasic() error {
	if msg.Derived != nil && msg.Derived.PubKey == nil {
		return sdkerrors.ErrInvalidPubKey
	}
	txData, err := UnpackTxData(msg.Data)
	if err != nil {
		return err
	}
	if _, ok := txData.(*ethtx.AssociateTx); !ok {
		if err := txData.Validate(); err != nil {
			return err
		}
	}
	amsg, isAssociate := msg.GetAssociateTx()
	if isAssociate {
		if len(amsg.CustomMessage) > MaxAssociateCustomMessageLength {
			return sdkerrors.Wrapf(sdkerrors.ErrTxTooLarge, "custom message can have at most 64 characters")
		}
		return amsg.Validate()
	}
	return nil
}
```
