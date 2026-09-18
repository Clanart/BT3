### Title
Externally-supplied `MsgEVMTransaction.Derived` field bypasses ECDSA sender verification, allowing sender impersonation and unauthorized address association - ([File: x/evm/ante/preprocess.go])

### Summary
`MsgEVMTransaction` carries a `Derived` field (`SenderEVMAddr`, `SenderSeiAddr`, `PubKey`, `Version`, `IsAssociate`) that is supposed to be computed internally by the ante pipeline via ECDSA signature recovery. However, every entry point that consumes this field only checks that `Derived.PubKey != nil` — none of them verify that the pubkey/addresses in `Derived` are actually consistent with the embedded Ethereum transaction's signature (V/R/S). If an attacker submits raw tx bytes with `Derived` already populated, this short-circuits all cryptographic sender recovery.

### Finding Description
`PreprocessUnpacked` treats a non-nil `Derived` as "already preprocessed" and returns immediately, performing no cross-check between `Derived.PubKey`/`SenderEVMAddr`/`SenderSeiAddr` and the actual ECDSA signature on the transaction: [1](#0-0) 

The same trust-without-verification pattern exists in the CheckTx entry point (`EvmStatelessChecks`) and the DeliverTx entry point (`EvmDeliverHandleSignatures`), both of which skip `CheckAndDecodeSignature` (the function that actually recovers the sender from the eth tx's raw signature) whenever `msg.Derived != nil`: [2](#0-1) [3](#0-2) 

Downstream, `EVMSigVerifyDecorator` reads the sender address directly from `Derived.SenderEVMAddr` — the value the attacker controls — and uses it to set the EVM sender context and validate nonce/fee, again without ever verifying the tx's ECDSA signature recovers to that address: [4](#0-3) 

Once accepted, `AssociateAddress`/`AssociateAddresses` will write an address mapping (`SetAddressMapping`) and migrate balances between the attacker-declared `evmAddr`/`seiAddr` pair, and may set the target account's stored pubkey if one is not already present: [5](#0-4) 

This mirrors the GoTrue bug class exactly: an identity/account-linkage object (`Derived`, i.e., the Sei↔EVM "user" association) is trusted and persisted based on attacker-supplied metadata rather than cryptographic proof of ownership.

### Impact Explanation
If reachable (see caveat below), this would allow an unprivileged party to:
- Claim to be the sender of a transaction while declaring an arbitrary victim's `SenderEVMAddr`/`SenderSeiAddr`, causing `EVMSigVerifyDecorator` to set `ctx.EVMSenderAddress()` to the victim, gate nonce/fee checks against the victim's account, and have the EVM's `msg.sender` be the victim during execution — i.e., unauthorized transaction execution / fund transfer on behalf of another account.
- Force-create or corrupt the Sei↔EVM address mapping (`SetAddressMapping`) and trigger `MigrateBalance`, which moves funds between the direct-cast address and the declared Sei address, potentially misdirecting funds or creating account state under a pubkey the victim never authorized.

This would qualify as a valid finding under "unauthorized transfer" / fund loss criteria.

### Likelihood Explanation
**Uncertain — likely not exploitable as described, and I could not fully verify the mitigating control.** Cosmos SDK/CometBFT typically decodes an incoming transaction into an `sdk.Tx` via `TxConfig.TxDecoder`, which unmarshals `Any`-wrapped `sdk.Msg`s from the raw protobuf bytes signed by the client. If the standard decoder path is used to construct `MsgEVMTransaction` from wire bytes for every submitted tx (CheckTx/DeliverTx), then yes, `Derived` is a normal proto field and nothing prevents a raw client from populating it in the submitted bytes, since `ValidateBasic` only checks `PubKey != nil`, not consistency: [6](#0-5) 

However, I could not locate and verify (within the available tool budget) whether there is an additional, separate check elsewhere in the RPC/mempool ingestion path (e.g., in `evmrpc` transaction submission, or a decoder-level guard) that strips or rejects a pre-populated `Derived` field before it ever reaches `PreprocessUnpacked`/`EvmStatelessChecks`. Given how heavily this specific codebase has been hardened against sender/association spoofing (e.g., the EIP-7702 authority-association fix and its dedicated regression test), it is plausible such a guard exists elsewhere but was not surfaced by my searches. Because I cannot confirm the presence or absence of this guard with certainty, I cannot assert this is a live, reachable vulnerability — it should be treated as a candidate that requires the actual raw tx-ingestion path to be traced end-to-end (from JSON-RPC / CheckTx `RequestCheckTxV2` handling down to `MsgEVMTransaction` construction) to determine if `Derived` can, in practice, be attacker-supplied on the wire.

### Recommendation
Regardless of whether the current ingestion path prevents this today, the code should not rely on that external invariant. Add a defense-in-depth check in `PreprocessUnpacked` (and mirrored in `EvmStatelessChecks`/`EvmDeliverHandleSignatures`) that, whenever `msg.Derived` is non-nil, independently recovers the sender via `CheckAndDecodeSignature`/`RecoverSenderFromEthTx` and asserts the recovered `evmAddr`/`seiAddr`/`pubkey` exactly match `Derived.SenderEVMAddr`/`SenderSeiAddr`/`PubKey`, rejecting the transaction otherwise. This closes the gap even if no ingestion-layer guard currently exists or if one is later removed/refactored.

### Proof of Concept
Not executable without confirming the raw ingestion path; conceptually:
1. Construct a `MsgEVMTransaction` with `Data` containing an arbitrary/self-signed Ethereum tx.
2. Populate `Derived` with `SenderEVMAddr`/`SenderSeiAddr` set to a victim's addresses and `PubKey` set to any non-nil pubkey bytes (need not correspond to the victim's real key).
3. Submit via the raw tx-broadcast path (bypassing the normal SDK client that would leave `Derived` nil).
4. If the decoder does not strip/reject `Derived`, `PreprocessUnpacked` accepts it as "already preprocessed" [1](#0-0) , `EVMSigVerifyDecorator` treats `Derived.SenderEVMAddr` as the authenticated sender [7](#0-6) , and the tx executes as the victim.

Given the unresolved uncertainty about the ingestion-layer guard, this should be verified with a live Devin session tracing the exact tx-decode path before treating it as a confirmed, exploitable finding.

### Citations

**File:** x/evm/ante/preprocess.go (L170-178)
```go
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

**File:** app/ante/evm_checktx.go (L90-96)
```go
	if err := msg.ValidateBasic(); err != nil {
		return err
	}
	if msg.Derived != nil && msg.Derived.PubKey == nil {
		// this means the message has `Derived` set from the outside, in which case we should reject
		return sdkerrors.ErrInvalidPubKey
	}
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

**File:** x/evm/ante/sig.go (L30-42)
```go
func (svd *EVMSigVerifyDecorator) AnteHandle(ctx sdk.Context, tx sdk.Tx, simulate bool, next sdk.AnteHandler) (sdk.Context, error) {
	ethTx, _ := types.MustGetEVMTransactionMessage(tx).AsTransaction()

	evmAddr := types.MustGetEVMTransactionMessage(tx).Derived.SenderEVMAddr

	nextNonce := svd.evmKeeper.GetNonce(ctx, evmAddr)
	txNonce := ethTx.Nonce()

	// set EVM properties
	ctx = ctx.WithIsEVM(true)
	ctx = ctx.WithEVMNonce(txNonce)
	ctx = ctx.WithEVMSenderAddress(evmAddr)
	ctx = ctx.WithSeiSenderAddress(types.MustGetEVMTransactionMessage(tx).Derived.SenderSeiAddr)
```

**File:** utils/helpers/associate.go (L34-55)
```go
func (p AssociationHelper) AssociateAddresses(ctx sdk.Context, seiAddr sdk.AccAddress, evmAddr common.Address, pubkey cryptotypes.PubKey, migrateUseiOnly bool) error {
	castAddr := sdk.AccAddress(evmAddr[:])
	if !castAddr.Equals(seiAddr) && p.accountKeeper.GetAccount(ctx, seiAddr) == nil {
		castAcc := p.accountKeeper.GetAccount(ctx, castAddr)
		castBaseAcc, ok := castAcc.(*authtypes.BaseAccount)
		if ok && castBaseAcc.GetPubKey() == nil && p.bankKeeper.LockedCoins(ctx, castAddr).IsZero() {
			p.accountKeeper.SetAccount(ctx, authtypes.NewBaseAccount(seiAddr, pubkey, castBaseAcc.GetAccountNumber(), castBaseAcc.GetSequence()))
		}
	}
	p.evmKeeper.SetAddressMapping(ctx, seiAddr, evmAddr)
	acc := p.accountKeeper.GetAccount(ctx, seiAddr)
	if acc == nil {
		acc = p.accountKeeper.NewAccountWithAddress(ctx, seiAddr)
	}
	if acc.GetPubKey() == nil {
		if err := acc.SetPubKey(pubkey); err != nil {
			return err
		}
		p.accountKeeper.SetAccount(ctx, acc)
	}
	return p.MigrateBalance(ctx, evmAddr, seiAddr, migrateUseiOnly)
}
```

**File:** x/evm/types/message_evm_transaction.go (L44-56)
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
```
