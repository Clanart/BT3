### Title
Improper Authentication via Trusted `Derived` Sender Field in EVM DeliverTx Ante Pipeline — Signature Verification Downgrade ([File: app/ante/evm_delivertx.go])

### Summary
The external advisory describes a PKCE downgrade in Spring Authorization Server: a client can skip the strong proof-of-possession check (PKCE) at one stage of a two-stage flow because a later stage trusts a claim ("this exchange was already verified") instead of re-verifying it. The sei-chain EVM ante pipeline has a structurally identical pattern: `MsgEVMTransaction.Derived` is treated by the `DeliverTx` ante path as proof that ECDSA sender recovery was "already done," and if this field is non-nil the actual signature verification is skipped entirely, in contrast to the `CheckTx` path, which always performs full ECDSA recovery.

### Finding Description
In the `CheckTx` ante path, the sender is always derived from the embedded Ethereum transaction's real signature via `CheckAndDecodeSignature`, regardless of any other field on the message: [1](#0-0) 

In the `DeliverTx` ante path, however, `EvmDeliverHandleSignatures` first checks whether `msg.Derived` is already populated, and if so, unconditionally trusts the `SenderEVMAddr`, `SenderSeiAddr`, and `PubKey` it contains — without recovering or checking them against the actual signature on the inner Ethereum transaction: [2](#0-1) 

Only when `msg.Derived` is nil does the code fall back to real signature recovery via `CheckAndDecodeSignature` and then populate `msg.Derived` for later reuse: [3](#0-2) 

The router that wires these paths together shows the same asymmetry across `CheckTx`/`DeliverTx`/tracer ante chains, and the accompanying comment acknowledges the `Derived`-based short-circuit exists so that "the mempool's check state matches how the transaction will execute" for `AssociateTx`, implying `Derived` is meant purely as an internal cache of a prior verification result: [4](#0-3) 

The root of the issue is the same class as the PKCE downgrade: the code conflates "a claim that verification occurred" with "verification occurred." `CheckTx` never writes into `msg.Derived`, so the only way `msg.Derived` can be non-nil the first time `EvmDeliverHandleSignatures` runs on a given tx is if that field is present in the transaction as submitted/decoded (it is a first-class field on `MsgEVMTransaction`, per `x/evm/types/tx.pb.go`). If `Derived` is part of the wire-decodable protobuf message (as its presence in the generated `tx.pb.go` code suggests) rather than a strictly ephemeral, non-serialized, per-process annotation, an attacker can construct a `MsgEVMTransaction` whose inner Ethereum transaction is validly signed by the attacker's own key (so it passes `CheckTx`'s real signature recovery, fee, and nonce checks against the attacker's own account) but whose `Derived.SenderEVMAddr`/`SenderSeiAddr`/`PubKey` claim an entirely different (victim) account. Because `EvmDeliverHandleSignatures` trusts `Derived` outright when non-nil, `DeliverTx`/`FinalizeBlock` execution (run independently by every validating node, not just a proposer) would then execute and account for the transaction as if it were authenticated by the victim's key, `AssociateAddress`-ing an unverified pubkey/address pair and using the victim's nonce/balance context — never re-checking that the claimed identity matches the actual ECDSA signer.

### Impact Explanation
If exploitable as described, this allows an unprivileged transaction sender to impersonate an arbitrary Sei/EVM address pair during `DeliverTx` execution without possessing that account's private key, since the authoritative signature check is skipped whenever `Derived` is already populated. This directly maps to the "unauthorized transfer via precompile or pointer" and "fund loss" impact categories in the validation criteria, because EVM message execution, nonce consumption, and address association would proceed under a forged identity rather than the cryptographically verified one.

### Likelihood Explanation
I could not fully confirm, within the available index, whether `Derived` is genuinely wire-serializable/attacker-settable on submission (i.e., decoded straight from client-submitted bytes) versus being restricted so that it can only be set internally by trusted ante-handler code before any external tx bytes are parsed (e.g., via a nullable/transient proto annotation, or because the RPC/tx-decoding layer strips or rejects a pre-populated `Derived` field before `CheckTx` ever sees it). The comments in `app/ante.go` suggest `Derived` is intended purely as an internal short-circuit cache tied to `AssociateTx` handling, which would reduce (but given the general `EvmDeliverHandleSignatures` code path, not eliminate) the attack surface. This uncertainty should be resolved by inspecting `x/evm/types/tx.pb.go`'s marshal/unmarshal logic for `MsgEVMTransaction.Derived` and the RPC/mempool ingestion code that constructs `MsgEVMTransaction` from raw client-submitted Ethereum tx bytes, to determine conclusively whether `Derived` can carry attacker-supplied values into `DeliverTx`.

### Recommendation
Regardless of the wire-format question, `EvmDeliverHandleSignatures` should not trust `msg.Derived` as authoritative for security-critical sender determination unless it can prove the field was populated internally by the same trusted process for this exact tx, and it should never accept `Derived` values as decoded directly from external tx bytes. At minimum, when `msg.Derived != nil`, the code should re-derive the sender from the actual ECDSA signature on the inner Ethereum transaction and reject the transaction if the claimed `Derived` fields do not match the recovered result, mirroring the always-verify behavior already present in the `CheckTx` path (`app/ante/evm_checktx.go`).

### Proof of Concept
Conceptual (pending confirmation that `Derived` is wire-decodable):
1. Attacker signs a valid Ethereum transaction (e.g., an EVM call moving funds or invoking a pointer/precompile) with their own private key, so it recovers correctly to the attacker's own EVM/Sei address.
2. Attacker constructs the corresponding `MsgEVMTransaction` and manually sets `Derived.SenderEVMAddr` / `Derived.SenderSeiAddr` / `Derived.PubKey` to a victim's address/pubkey instead of leaving it unset.
3. Broadcast the tx. `CheckTx` (`app/ante/evm_checktx.go:53`) ignores `Derived` and validates against the attacker's real account, so the tx is admitted to the mempool.
4. When included in a block, every validating node's `DeliverTx`/`FinalizeBlock` calls `EvmDeliverHandleSignatures` (`app/ante/evm_delivertx.go:56-71`), sees `msg.Derived != nil`, and executes/accounts for the transaction as though it were authenticated by the victim's key — skipping ECDSA verification entirely. [2](#0-1) [5](#0-4)

### Citations

**File:** app/ante/evm_checktx.go (L49-59)
```go
	if atx, ok := txData.(*ethtx.AssociateTx); ok {
		return HandleAssociateTx(ctx, ek, atx, true)
	}
	etx := ethtypes.NewTx(txData.AsEthereumData())
	evmAddr, seiAddr, seiPubkey, version, err := CheckAndDecodeSignature(ctx, txData, chainID, false)
	if err != nil {
		return ctx, err
	}
	if err := AssociateAddress(ctx, ek, evmAddr, seiAddr, seiPubkey); err != nil {
		return ctx, err
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

**File:** app/ante/evm_delivertx.go (L73-91)
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
}
```

**File:** app/ante.go (L91-101)
```go
	evmAnteDecorators := []sdk.AnteDecorator{
		// NOTE: NewEVMNoCosmosFieldsDecorator must come first to prevent writing state to chain without being charged.
		// E.g. EVMPreprocessDecorator may short-circuit all the later ante handlers if AssociateTx and ignore NewEVMNoCosmosFieldsDecorator.
		evmante.NewEVMNoCosmosFieldsDecorator(),
		evmante.NewEVMPreprocessDecorator(options.EVMKeeper, options.EVMKeeper.AccountKeeper()),
		evmante.NewBasicDecorator(options.EVMKeeper),
		evmante.NewEVMFeeCheckDecorator(options.EVMKeeper, options.UpgradeKeeper),
		evmante.NewEVMSigVerifyDecorator(options.EVMKeeper, options.LatestCtxGetter),
		evmante.NewGasDecorator(options.EVMKeeper),
	}
	evmAnteHandler := sdk.ChainAnteDecorators(evmAnteDecorators...)
```
