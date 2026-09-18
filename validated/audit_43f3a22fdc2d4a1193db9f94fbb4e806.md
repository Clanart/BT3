Based on my investigation, I found a strong structural analog to the reported bug class: a client-supplied "trust me, already verified" flag that lets the message sender bypass independent cryptographic verification, mirroring how `displayMessage` let the dapp bypass the wallet's independent display/verification step.

### Title
`MsgEVMTransaction.Derived` pre-population lets a submitter skip signature-based sender verification and impersonate any address - (File: x/evm/ante/preprocess.go)

### Summary
`MsgEVMTransaction` carries an optional `Derived` field (sender EVM/Sei address + pubkey + version) that is normally computed by the node from the embedded signed Ethereum transaction. Both the CheckTx-time preprocessing and DeliverTx-time signature handling treat a non-nil `Derived.PubKey` as proof that "the signature has already been verified," and skip re-deriving the sender from the actual ECDSA signature (V/R/S) over the transaction hash.

### Finding Description
In `PreprocessUnpacked`, the *only* validation performed when `msgEVMTransaction.Derived` is already set is a nil-PubKey check: [1](#0-0) 
If `Derived.PubKey` is non-nil, the function returns immediately with "already preprocessed" — it never verifies that this pubkey/address pair was actually derived from a valid ECDSA signature over the embedded `txData`.

The DeliverTx path repeats the same pattern: if `msg.Derived != nil`, it trusts `msg.Derived.SenderEVMAddr`/`SenderSeiAddr`/`PubKey` directly and calls `AssociateAddress` on them, again without ever running `CheckAndDecodeSignature` against the actual transaction bytes: [2](#0-1) 
Only in the `else` branch (when `Derived` is nil) does the code perform genuine signature recovery via `CheckAndDecodeSignature`: [3](#0-2) 

The resulting `evmAddr`/`seiAddr` from this trusted-but-unverified path are then used to drive nonce handling, fee charging, and the execution context for the transaction: [4](#0-3) [5](#0-4) 

The comment in `PreprocessUnpacked` — *"this means the message has `Derived` set from the outside, in which case we should reject"* — shows the developers were aware that `Derived` can be attacker-populated at the wire level (it is a field of the `MsgEVMTransaction` proto, checked in `ValidateBasic` which runs on freshly decoded transactions), but the guard they implemented only rejects the case where `PubKey` is nil, not the case where an attacker supplies a self-consistent but forged `PubKey`/`SenderEVMAddr`/`SenderSeiAddr` triple that was never actually used to sign the embedded transaction.

### Impact Explanation
If an attacker can construct a raw `MsgEVMTransaction` with a decodable (but not necessarily correctly-signed-by-the-claimed-sender) `txData`, and manually set `Derived` to claim an arbitrary victim `SenderEVMAddr`/`SenderSeiAddr` with a pubkey object, the ante pipeline will treat the transaction as if it were legitimately signed by that victim — driving nonce accounting, fee/gas debits, and (unless nullified by a downstream independent check I could not fully trace within the available tool budget) the EVM execution's sender context. This is the exact bug class described in the external report: a caller-supplied flag that suppresses an independent verification step that should always run, enabling unauthorized action on behalf of a party who never actually authorized it — here, potential unauthorized transfers/fund loss instead of just an unreviewed signing dialog.

### Likelihood Explanation
Reachability requires only crafting and broadcasting a single `MsgEVMTransaction` with the `Derived` field populated — no special privileges, validator access, or governance action needed, which is well within the scope of a single unprivileged transaction sender. The main residual uncertainty is whether some other, later component (e.g., the Giga executor or the actual `vm.Message`/`ethTx` sender assignment during state transition) independently re-derives the sender from `etx`'s real ECDSA signature and would reject a mismatch; I was not able to fully trace that path within my remaining tool budget, so this should be verified before treating the finding as conclusively exploitable.

### Recommendation
Remove the "trust if `PubKey != nil`" shortcut. Whenever `Derived` is present in a message that did not originate from this node's own preprocessing (i.e., whenever it arrives already set on a freshly decoded/received transaction), always re-derive `SenderEVMAddr`/`SenderSeiAddr`/`PubKey` from the actual signature over `txData` and compare against the claimed values, rejecting any mismatch — mirroring the report's recommendation to always independently verify/display the actual signed payload rather than trusting a caller-supplied claim about it.

### Proof of Concept
Conceptual PoC (not fully verified against the downstream EVM execution path due to tool-call budget limits):
1. Attacker crafts an `ethtx.TxData` payload (any decodable Ethereum tx type) — it does not need to be validly signed by the victim.
2. Attacker wraps it in a `MsgEVMTransaction` and manually sets `Derived = &derived.Derived{SenderEVMAddr: <victim EVM addr>, SenderSeiAddr: <victim Sei addr>, PubKey: <some non-nil pubkey object>, Version: <valid>, IsAssociate: false}` before serializing/broadcasting the transaction.
3. `ValidateBasic` only checks `Derived.PubKey != nil` [6](#0-5)  — passes.
4. `PreprocessUnpacked` sees `Derived != nil && Derived.PubKey != nil` and returns early as "already preprocessed" [1](#0-0)  — no actual signature recovery ever occurs.
5. `EvmDeliverHandleSignatures` in DeliverTx likewise trusts `msg.Derived` and calls `AssociateAddress`/proceeds using the victim's claimed identity [2](#0-1) .

Full confirmation that this results in on-chain fund loss (versus being neutralized by a later independent signature check in the state-transition/Giga executor) requires further code tracing that was not completed within this session's tool budget.

### Citations

**File:** x/evm/ante/preprocess.go (L171-178)
```go
	if msgEVMTransaction.Derived != nil {
		if msgEVMTransaction.Derived.PubKey == nil {
			// this means the message has `Derived` set from the outside, in which case we should reject
			return sdkerrors.ErrInvalidPubKey
		}
		// already preprocessed
		return nil
	}
```

**File:** app/ante/evm_delivertx.go (L38-53)
```go
	evmAddr, seiAddr, version, err := EvmDeliverHandleSignatures(ctx, ek, txData, chainID, msg)
	if err != nil {
		return ctx, err
	}
	// EIP-7702 authorization authorities are distinct accounts from the tx sender, so the
	// sender association above does not cover them. Pre-associate each authority the EVM will
	// apply to its true (pubkey-derived) Sei address before execution installs delegation code,
	// otherwise SetCode creates a mutable direct-cast mapping that a later associatePubKey can
	// remap, orphaning staking/distribution state (which can halt the chain via the distribution
	// validator-removal hook).
	AssociateAuthorizationAuthorities(ctx, ek, etx)
	ctx = DecorateNonceCallback(ctx, ek, evmAddr, etx.Nonce())
	if err := EvmDeliverChargeFees(ctx, ek, upgradeKeeper, txData, etx, msg, version, evmAddr); err != nil {
		return ctx, err
	}
	return DecorateContext(ctx, ek, tx, txData, etx, evmAddr, seiAddr), nil
```

**File:** app/ante/evm_delivertx.go (L56-70)
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
```

**File:** app/ante/evm_delivertx.go (L73-90)
```go
	evmAddr, seiAddr, seiPubkey, version, err := CheckAndDecodeSignature(ctx, txData, chainID, ek.EthBlockTestConfig.Enabled)
	if err != nil {
		return evmAddr, seiAddr, version, err
	}
	if err := AssociateAddress(ctx, ek, evmAddr, seiAddr, seiPubkey); err != nil {
		return evmAddr, seiAddr, version, err
	}
	if ek.EthReplayConfig.Enabled {
		ek.PrepareReplayedAddr(ctx, evmAddr)
	}
	msg.Derived = &derived.Derived{
		SenderEVMAddr: evmAddr,
		SenderSeiAddr: seiAddr,
		PubKey:        &secp256k1.PubKey{Key: seiPubkey.Bytes()},
		Version:       version,
		IsAssociate:   false,
	}
	return evmAddr, seiAddr, version, nil
```

**File:** x/evm/ante/basic.go (L29-41)
```go
	if msg.Derived != nil && !gl.k.EthReplayConfig.Enabled && !gl.k.EthBlockTestConfig.Enabled {
		startingNonce := gl.k.GetNonce(ctx, msg.Derived.SenderEVMAddr)
		txNonce := etx.Nonce()
		if !ctx.IsCheckTx() && !ctx.IsReCheckTx() && startingNonce == txNonce {
			ctx = ctx.WithDeliverTxCallback(func(callCtx sdk.Context) {
				// bump nonce if it is for some reason not incremented (e.g. ante failure)
				if gl.k.GetNonce(callCtx, msg.Derived.SenderEVMAddr) == startingNonce {
					gl.k.SetNonce(callCtx, msg.Derived.SenderEVMAddr, startingNonce+1)
					gl.k.SetNonceBumped(callCtx)
				}
			})
		}
	}
```

**File:** x/evm/types/message_evm_transaction.go (L44-48)
```go
func (msg *MsgEVMTransaction) ValidateBasic() error {
	if msg.Derived != nil && msg.Derived.PubKey == nil {
		return sdkerrors.ErrInvalidPubKey
	}
	txData, err := UnpackTxData(msg.Data)
```
