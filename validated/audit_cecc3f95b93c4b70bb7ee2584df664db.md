### Title
EVM DeliverTx sender-identity bypass via forged `MsgEVMTransaction.Derived` field allows transaction sender impersonation and fund theft - (File: app/ante/evm_delivertx.go)

### Summary
The OXID eShop bug allowed an attacker to impersonate a user because the SSO code trusted an identity field (`email`) embedded in a client-supplied authentication token instead of cryptographically re-deriving the identity from the actual credential. The `sei-chain` EVM ante pipeline has the same trust pattern: `MsgEVMTransaction` carries an optional `Derived` field (`SenderEVMAddr`, `SenderSeiAddr`, `PubKey`) that is supposed to be an internal cache populated *after* the ante handler verifies the embedded Ethereum ECDSA signature. In `EvmDeliverHandleSignatures` (DeliverTx path), if `msg.Derived != nil` the function skips signature verification entirely and blindly trusts the attacker-supplied `SenderEVMAddr`/`SenderSeiAddr` as the transaction's sender identity. [1](#0-0) 

### Finding Description
`MsgEVMTransaction` is a plain protobuf message with a client-settable `Derived` field; `GetSigners`/`GetSignBytes` on it panic because Cosmos-level signature checks are intentionally skipped for EVM messages, and identity is meant to come solely from the embedded Ethereum-signed payload in `Data`. [2](#0-1) [3](#0-2) 

The only guard against a forged `Derived` is in `ValidateBasic`/`EvmStatelessChecks`, which reject the message only when `Derived != nil && Derived.PubKey == nil`. Setting any non-nil `PubKey` (it need not correspond to the claimed addresses) satisfies this check. [4](#0-3) [5](#0-4) 

During `CheckTx`, `EvmCheckTxAnte` never reads `msg.Derived` at all — it always calls `CheckAndDecodeSignature` to recover the real signer from the embedded ECDSA signature, so the tx will be admitted to the mempool under the attacker's own (real) identity, paying real fees from the real account. [6](#0-5) 

However, at `DeliverTx` time, `EvmDeliverHandleSignatures` takes a completely different branch: if `msg.Derived != nil` it uses `msg.Derived.SenderEVMAddr` / `msg.Derived.SenderSeiAddr` / `msg.Derived.PubKey` directly — with no re-verification against the actual signature bytes in `txData` — and immediately calls `AssociateAddress` with these attacker-controlled values. [1](#0-0) 

Because `Derived` is part of the serialized `sdk.Tx` bytes that travel unchanged from the original broadcast through the mempool into the block, an attacker can construct a `MsgEVMTransaction` where:
- `Data` contains any Ethereum transaction validly signed with the attacker's own throwaway key (satisfies `EvmStatelessChecks` and lets CheckTx accept/relay the tx under the attacker's real identity/fees/nonce), and
- `Derived` is forged with `SenderEVMAddr`/`SenderSeiAddr` set to a victim account and `PubKey` set to any non-nil value (e.g. the attacker's own pubkey).

At `DeliverTx`, the forged `Derived` is trusted as-is, and this identity subsequently flows into the rest of the pipeline (`DecorateContext` sets `ctx.WithEVMSenderAddress`/`WithSeiSenderAddress` from these values, and `x/evm/ante/preprocess.go` reads `derived.SenderSeiAddr`/`derived.SenderEVMAddr` directly for association, fee charging, and EVM execution's `msg.sender`). [7](#0-6) [8](#0-7) 

This is structurally the same class of bug as the reported OXID eShop CVE: an identity claim embedded in an attacker-controlled token/message is trusted for authentication/authorization instead of being cryptographically re-derived from a verified signature.

### Impact Explanation
If exploitable end-to-end (see Likelihood/uncertainty below), this allows an unprivileged attacker to make the chain execute an arbitrary EVM transaction (value transfer, contract call, etc.) as if it were sent by any victim account — without that victim's private key. Because gas fees, nonce increment, and the EVM `msg.sender` all derive from the forged `Derived.SenderEVMAddr`/`SenderSeiAddr`, this constitutes unauthorized fund transfer/spend from the victim's balance and unauthorized-as-victim contract interaction, i.e. direct fund loss and complete authentication bypass for the EVM transaction path.

### Likelihood Explanation
The forged `Derived` field survives serialization/deserialization from the original broadcast tx into the block (it is a normal protobuf field of `MsgEVMTransaction`), and `CheckTx` does not need to use or validate it (it always independently re-derives identity from the real signature for mempool admission), so an honest node's mempool would accept and relay such a transaction without needing a colluding validator. I was not able to fully verify, within tool constraints, the internal implementation of `AssociateAddress` (only its call sites were found) to confirm it performs no cross-check between the supplied `PubKey` and the supplied `SenderEVMAddr`/`SenderSeiAddr`; if `AssociateAddress` (or a keeper-level invariant elsewhere in the ante/EVM pipeline not covered by my search) rejects a mismatched/unrelated pubkey-vs-address combination, or if `Derived` is stripped/recomputed somewhere between mempool relay and `DeliverTx` decoding that I did not locate, this would block the exploit. This uncertainty should be resolved by inspecting `AssociateAddress`'s definition and the full tx decode path from mempool to `DeliverTx`.

### Recommendation
In `EvmDeliverHandleSignatures` (and any other consumer of `msg.Derived`), never trust a `Derived` value that did not originate from the node's own `CheckAndDecodeSignature` call within the same ante pipeline execution. Concretely:
- Do not accept a pre-populated `Derived` field from an externally submitted transaction at all for the primary (non-`AssociateTx`, non-internal) EVM transaction path; always recompute `evmAddr`/`seiAddr`/`pubkey` from the embedded Ethereum signature in `txData` during `DeliverTx`, mirroring what `EvmCheckTxAnte` already does.
- If a cached `Derived` value must be trusted for performance, verify that `PubKey`, `SenderEVMAddr`, and `SenderSeiAddr` are mutually consistent (i.e., `SenderEVMAddr == PubkeyToEVMAddress(PubKey)` and `SenderSeiAddr == PubkeyBytesToSeiPubKey(PubKey).Address()`) and that `PubKey` actually corresponds to a valid signature recovery over the transaction's signed hash, rather than accepting the three fields independently.

### Proof of Concept
1. Attacker generates a throwaway secp256k1 key `K_attacker` and builds a valid, self-consistent Ethereum transaction `ethTx` (e.g., a 0-value call or minimal transfer) signed with `K_attacker`, satisfying `EvmStatelessChecks` (correct chain ID, gas, etc.).
2. Attacker wraps `ethTx` into `MsgEVMTransaction.Data`, and additionally sets `MsgEVMTransaction.Derived = &derived.Derived{ SenderEVMAddr: <victimEVMAddr>, SenderSeiAddr: <victimSeiAddr>, PubKey: <K_attacker's own pubkey, any non-nil value>, Version: derived.London, IsAssociate: false }`.
3. Attacker broadcasts the resulting `sdk.Tx`. `EvmCheckTxAnte` on any node ignores `Derived`, recovers the real signer (`K_attacker`) from `ethTx`, and admits the tx to the mempool charging fees to the attacker's own account — the tx looks legitimate and gets relayed/included in a block by an honest proposer.
4. During `DeliverTx`, `EvmStatelessChecks` passes (`Derived.PubKey != nil`), and `EvmDeliverHandleSignatures` takes the `msg.Derived != nil` branch, adopting `victimEVMAddr`/`victimSeiAddr` as the transaction's sender without checking them against `ethTx`'s actual signature. Fee charging, nonce increment, and EVM execution proceed with the victim treated as `msg.sender`, letting the attacker drive EVM state transitions (e.g. asset transfers) attributed to and funded by the victim. [9](#0-8)

### Citations

**File:** app/ante/evm_delivertx.go (L19-53)
```go
func EvmDeliverTxAnte(
	ctx sdk.Context,
	txConfig client.TxConfig,
	tx sdk.Tx,
	upgradeKeeper *upgradekeeper.Keeper,
	ek *evmkeeper.Keeper,
) (returnCtx sdk.Context, returnErr error) {
	ctx = ctx.WithDeliverTxCallback(func(sdk.Context) {})
	chainID := ek.ChainID(ctx)
	if err := EvmStatelessChecks(ctx, tx, chainID); err != nil {
		return ctx, err
	}
	msg := tx.GetMsgs()[0].(*evmtypes.MsgEVMTransaction)
	txData, _ := evmtypes.UnpackTxData(msg.Data) // cached and validated
	ctx = ctx.WithGasMeter(sdk.NewInfiniteGasMeterWithMultiplier(ctx))
	if atx, ok := txData.(*ethtx.AssociateTx); ok {
		return HandleAssociateTx(ctx, ek, atx, false)
	}
	etx := ethtypes.NewTx(txData.AsEthereumData())
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

**File:** app/ante/evm_delivertx.go (L56-90)
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

**File:** x/evm/types/message_evm_transaction.go (L36-42)
```go
func (msg *MsgEVMTransaction) GetSigners() []sdk.AccAddress {
	panic("signer should be accessed on EVM transaction level")
}

func (msg *MsgEVMTransaction) GetSignBytes() []byte {
	panic("sign bytes should be accessed on EVM transaction level")
}
```

**File:** x/evm/types/message_evm_transaction.go (L44-47)
```go
func (msg *MsgEVMTransaction) ValidateBasic() error {
	if msg.Derived != nil && msg.Derived.PubKey == nil {
		return sdkerrors.ErrInvalidPubKey
	}
```

**File:** giga/deps/xevm/types/tx.pb.go (L36-39)
```go
type MsgEVMTransaction struct {
	Data    *types.Any                                               `protobuf:"bytes,1,opt,name=data,proto3" json:"data,omitempty"`
	Derived *github_com_sei_protocol_sei_chain_x_evm_derived.Derived `protobuf:"bytes,2,opt,name=derived,proto3,customtype=github.com/sei-protocol/sei-chain/giga/deps/xevm/derived.Derived" json:"derived,omitempty"`
}
```

**File:** app/ante/evm_checktx.go (L52-56)
```go
	etx := ethtypes.NewTx(txData.AsEthereumData())
	evmAddr, seiAddr, seiPubkey, version, err := CheckAndDecodeSignature(ctx, txData, chainID, false)
	if err != nil {
		return ctx, err
	}
```

**File:** app/ante/evm_checktx.go (L93-96)
```go
	if msg.Derived != nil && msg.Derived.PubKey == nil {
		// this means the message has `Derived` set from the outside, in which case we should reject
		return sdkerrors.ErrInvalidPubKey
	}
```

**File:** x/evm/ante/preprocess.go (L58-72)
```go
func (p *EVMPreprocessDecorator) AnteHandle(ctx sdk.Context, tx sdk.Tx, simulate bool, next sdk.AnteHandler) (sdk.Context, error) {
	msg := evmtypes.MustGetEVMTransactionMessage(tx)
	if err := Preprocess(ctx, msg, p.evmKeeper.ChainID(ctx), p.evmKeeper.EthBlockTestConfig.Enabled); err != nil {
		return ctx, err
	}

	// use infinite gas meter for EVM transaction because EVM handles gas checking from within
	ctx = ctx.WithGasMeter(sdk.NewInfiniteGasMeterWithMultiplier(ctx))

	derived := msg.Derived
	seiAddr := derived.SenderSeiAddr
	evmAddr := derived.SenderEVMAddr
	ctx.EventManager().EmitEvent(sdk.NewEvent(evmtypes.EventTypeSigner,
		sdk.NewAttribute(evmtypes.AttributeKeyEvmAddress, evmAddr.Hex()),
		sdk.NewAttribute(evmtypes.AttributeKeySeiAddress, seiAddr.String())))
```
