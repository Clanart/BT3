### Title
Unbounded EIP-7702 authorization-list loop executes expensive signature recovery and KVStore reads before the intrinsic-gas check rejects the transaction - (File: x/evm/ante/preprocess.go)

### Summary
`EVMPreprocessDecorator.AnteHandle` iterates over every `SetCodeAuthorization` in an EIP-7702 `SetCodeTx` and performs full secp256k1 signature recovery plus multiple KVStore reads for each entry, before the `BasicDecorator` intrinsic-gas check (which scales with authorization count) has a chance to reject the transaction for insufficient declared gas.

### Finding Description
`EVMPreprocessDecorator.AnteHandle` runs `Preprocess`, switches the context to an infinite gas meter, and then calls `p.associateAuthorizationAuthorities(ctx, msg, associateHelper)`, which loops over `ethTx.SetCodeAuthorizations()` and, for each authorization, invokes `helpers.AuthorityToPreAssociate` [1](#0-0) . `AuthorityToPreAssociate` performs a full ECDSA recovery via `RecoverAddressesFromAuthorization` (RLP-encode, keccak256, `crypto.Ecrecover`) plus three keeper reads (`GetCode`, `GetNonce`, `GetEVMAddress`) per authorization [2](#0-1) .

This work happens under the just-installed infinite gas meter [3](#0-2) , so none of it is billed against the transaction's declared gas.

The decorator order in `app/ante.go` places `EVMPreprocessDecorator` **before** `BasicDecorator`, which is the decorator that performs the `core.IntrinsicGas(...)` check comparing `etx.Gas()` against the gas cost implied by `etx.SetCodeAuthorizations()` and rejects the tx if `etx.Gas() < intrGas`: [4](#0-3) [5](#0-4) 

Because the cheap, authorization-count-scaled rejection check in `BasicDecorator` runs only *after* the expensive per-authorization recovery/lookup loop in `EVMPreprocessDecorator`, an attacker can craft a `SetCodeTx` with a very large `AuthList` (each authorization is a small RLP tuple, so a transaction near the mempool's max tx size can carry many thousands of entries) and set `Gas()` to a small value. The transaction will still pass `EVMNoCosmosFieldsDecorator`/`ValidateBasic` and reach `EVMPreprocessDecorator`, which fully iterates and cryptographically verifies every authorization (and touches the KV store 3x per entry) before it is finally rejected by `BasicDecorator` for insufficient intrinsic gas — but only after all that CPU/IO cost has already been paid by the node.

Note the equivalent legacyabci `EvmCheckTxAnte` path performs `EvmStatelessChecks` (which includes the same `IntrinsicGas` check) *before* calling `AssociateAuthorizationAuthorities` [6](#0-5) , so that path is correctly ordered and not vulnerable — this reinforces that the ordering in `x/evm/ante/preprocess.go`/`app/ante.go` is the defective one, since the intended safe pattern (gas-check-before-loop) exists elsewhere in the codebase but was not applied consistently.

### Impact Explanation
Because `EVMPreprocessDecorator` runs in both `CheckTx` (mempool admission, executed independently by every full node, including on recheck) and `DeliverTx`, a single crafted transaction forces every node in the network to perform thousands of ECDSA recoveries and KV reads without paying commensurate gas/fees, since the loop is unmetered (infinite gas meter) and executes before the gas-sufficiency check that would otherwise reject the transaction cheaply. Submitted repeatedly/concurrently by an unprivileged EVM transaction sender, this can degrade validator/node performance and contribute to block-processing delay, matching the accepted "block delay beyond 2.5 seconds" / node-halt-adjacent DoS impact class.

### Likelihood Explanation
High reachability: any account able to submit an EIP-7702 `SetCodeTx` over the public EVM JSON-RPC surface can trigger this without any special privilege, association, or balance beyond what is needed to broadcast a transaction (the tx does not even need to succeed — it is rejected only after the expensive loop runs). Constructing a maximal `AuthList` is straightforward (repeating identical-structure authorization tuples, no need for them to be individually valid/distinct signers beyond form, since `AuthorityToPreAssociate` still fully evaluates/recovers each one before filtering).

### Recommendation
Reorder the EVM ante decorators so the intrinsic-gas / authorization-count bound check (`BasicDecorator`) runs before `EVMPreprocessDecorator`'s authorization-authority pre-association loop, mirroring the safe ordering already used in `app/ante/evm_checktx.go`'s `EvmStatelessChecks`-before-`AssociateAuthorizationAuthorities` pattern. Alternatively, cap `len(ethTx.SetCodeAuthorizations())` against a fixed sane bound and/or perform the `etx.Gas() < intrinsicGas` comparison inline at the very start of `associateAuthorizationAuthorities` before iterating.

### Proof of Concept
1. Craft a `SetCodeTx` with `Gas` set to the minimum non-SetCode intrinsic gas (e.g. 21000) and an `AuthList` containing as many `SetCodeAuthorization` tuples as fit under the mempool's max tx size (each tuple RLP-encodes to roughly 100+ bytes, so tens of thousands of entries are feasible in a several-hundred-KB transaction).
2. Submit the transaction to any node's public RPC.
3. The transaction reaches `EVMPreprocessDecorator.AnteHandle` → `associateAuthorizationAuthorities`, which iterates the full `AuthList`, calling `AuthorityToPreAssociate` (ECDSA recovery via `crypto.Ecrecover` + 3 keeper reads) for every entry [7](#0-6) .
4. Only after this full loop completes does control reach `BasicDecorator`, which computes `core.IntrinsicGas` and rejects the transaction for `etx.Gas() < intrGas` [5](#0-4) .
5. Repeating step 2 with multiple such transactions (which are cheap to construct and always get rejected, so cost the attacker minimal/no fees) forces disproportionate CPU/DB work on every node performing `CheckTx`/`ReCheckTx`/`DeliverTx`.

### Citations

**File:** x/evm/ante/preprocess.go (L58-65)
```go
func (p *EVMPreprocessDecorator) AnteHandle(ctx sdk.Context, tx sdk.Tx, simulate bool, next sdk.AnteHandler) (sdk.Context, error) {
	msg := evmtypes.MustGetEVMTransactionMessage(tx)
	if err := Preprocess(ctx, msg, p.evmKeeper.ChainID(ctx), p.evmKeeper.EthBlockTestConfig.Enabled); err != nil {
		return ctx, err
	}

	// use infinite gas meter for EVM transaction because EVM handles gas checking from within
	ctx = ctx.WithGasMeter(sdk.NewInfiniteGasMeterWithMultiplier(ctx))
```

**File:** x/evm/ante/preprocess.go (L103-146)
```go
	// EIP-7702 authorization authorities are distinct accounts from the tx sender, so the
	// sender association above does not cover them. Associate each authority to its true
	// (pubkey-derived) Sei address before EVM execution installs delegation code for it.
	// Otherwise SetCode creates a mutable direct-cast EVM->Sei mapping that a later
	// associatePubKey call can remap, orphaning any staking/distribution state created
	// under the direct-cast identity (which can then halt the chain via the distribution
	// validator-removal hook).
	p.associateAuthorizationAuthorities(ctx, msg, associateHelper)

	return next(ctx, tx, simulate)
}

// associateAuthorizationAuthorities associates every EIP-7702 authorization authority in
// the transaction with its true (pubkey-derived) Sei address, so that a subsequent
// SetCode for the authority does not create a mutable direct-cast mapping. It is
// best-effort: authorities with invalid signatures (which EVM execution would also skip)
// or that are already associated are left untouched, and an association failure skips
// only that authority rather than rejecting the transaction, which go-ethereum would
// still accept.
func (p *EVMPreprocessDecorator) associateAuthorizationAuthorities(ctx sdk.Context, msg *evmtypes.MsgEVMTransaction, associateHelper *helpers.AssociationHelper) {
	txData, err := evmtypes.UnpackTxData(msg.Data)
	if err != nil {
		return
	}
	setCodeTx, ok := txData.(*ethtx.SetCodeTx)
	if !ok {
		// Only SetCode (EIP-7702) transactions carry authorizations.
		return
	}
	ethTx := ethtypes.NewTx(setCodeTx.AsEthereumData())
	for _, auth := range ethTx.SetCodeAuthorizations() {
		// Only pre-associate authorities whose authorization the EVM will actually apply
		// (matching chain id, nonce, and account state). This prevents replaying a
		// publicly-visible authorization a user signed for another chain to force-associate
		// them, which the EVM would skip but which still recovers a valid authority.
		evmAddr, seiAddr, pubkey, ok := helpers.AuthorityToPreAssociate(ctx, p.evmKeeper, auth)
		if !ok {
			continue
		}
		cacheCtx, write := ctx.CacheContext()
		if err := associateHelper.AssociateAddresses(cacheCtx, seiAddr, evmAddr, pubkey, false); err == nil {
			write()
		}
	}
```

**File:** utils/helpers/address.go (L156-188)
```go
func AuthorityToPreAssociate(ctx sdk.Context, k AuthorizationStateReader, auth ethtypes.SetCodeAuthorization) (common.Address, sdk.AccAddress, cryptotypes.PubKey, bool) {
	// Chain ID must be null or match the local chain.
	if !auth.ChainID.IsZero() && auth.ChainID.CmpBig(k.ChainID(ctx)) != 0 {
		return common.Address{}, nil, nil, false
	}
	// Nonce must not overflow (EIP-2681).
	if auth.Nonce+1 < auth.Nonce {
		return common.Address{}, nil, nil, false
	}
	evmAddr, seiAddr, pubkey, err := RecoverAddressesFromAuthorization(auth)
	if err != nil {
		return common.Address{}, nil, nil, false
	}
	// Cross-check against go-ethereum's authoritative recovery so we only ever act on the
	// exact address SetCode would target during execution.
	if authAddr, aerr := auth.Authority(); aerr != nil || authAddr != evmAddr {
		return common.Address{}, nil, nil, false
	}
	// Authority must have no code, or only an existing delegation designator.
	if code := k.GetCode(ctx, evmAddr); len(code) != 0 {
		if _, ok := ethtypes.ParseDelegation(code); !ok {
			return common.Address{}, nil, nil, false
		}
	}
	// Authority account nonce must match the authorization nonce.
	if k.GetNonce(ctx, evmAddr) != auth.Nonce {
		return common.Address{}, nil, nil, false
	}
	// Already-associated authorities need no pre-association (and cannot be re-mapped).
	if _, associated := k.GetEVMAddress(ctx, seiAddr); associated {
		return common.Address{}, nil, nil, false
	}
	return evmAddr, seiAddr, pubkey, true
```

**File:** app/ante.go (L91-100)
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
```

**File:** x/evm/ante/basic.go (L51-57)
```go
	intrGas, err := core.IntrinsicGas(etx.Data(), etx.AccessList(), etx.SetCodeAuthorizations(), etx.To() == nil, true, true, true)
	if err != nil {
		return ctx, err
	}
	if etx.Gas() < intrGas {
		return ctx, core.ErrIntrinsicGas
	}
```

**File:** app/ante/evm_checktx.go (L42-65)
```go
	if err := EvmStatelessChecks(ctx, tx, chainID); err != nil {
		return ctx, err
	}
	msg := tx.GetMsgs()[0].(*evmtypes.MsgEVMTransaction)

	txData, _ := evmtypes.UnpackTxData(msg.Data) // cached and validated
	ctx = ctx.WithGasMeter(sdk.NewInfiniteGasMeterWithMultiplier(ctx))
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
	// Mirror the deliver-tx path and pre-associate EIP-7702 authorization authorities so the
	// mempool's check state matches how the transaction will execute. CheckTx runs on a
	// throwaway cache (its writes are never committed) and never applies authorizations, so
	// this cannot itself cause the direct-cast halt; it is here purely for mempool consistency
	// with EvmDeliverTxAnte.
	AssociateAuthorizationAuthorities(ctx, ek, etx)
```
