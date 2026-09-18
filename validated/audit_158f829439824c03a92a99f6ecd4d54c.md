Based on my investigation, I found a legitimate analog to the gas-exhaustion bug class in the EIP-7702 authorization pre-association loop.

### Title
Unmetered CPU-Intensive Loop Over EIP-7702 Authorization List in EVM Ante Handler Enables Gas Exhaustion / Block-Time DoS - (File: `x/evm/ante/preprocess.go`)

### Summary
The `EVMPreprocessDecorator.AnteHandle` calls `associateAuthorizationAuthorities`, which iterates over every EIP-7702 `SetCodeAuthorization` in a submitted transaction's authorization list and, for each one, performs an ECDSA signature recovery plus multiple state store reads (and a `CacheContext`/state write attempt) — all while the context's gas meter is an **infinite gas meter**, before the transaction's intrinsic-gas sufficiency for its authorization count is validated against the sender's actual paid gas.

### Finding Description
`EVMPreprocessDecorator.AnteHandle` sets an infinite gas meter and then unconditionally calls `p.associateAuthorizationAuthorities(ctx, msg, associateHelper)` [1](#0-0) , which loops over `ethTx.SetCodeAuthorizations()` and, for each entry, calls `helpers.AuthorityToPreAssociate` and potentially `associateHelper.AssociateAddresses` inside a fresh `ctx.CacheContext()` [2](#0-1) .

`AuthorityToPreAssociate` performs an ECDSA recovery (`RecoverAddressesFromAuthorization`, which does an RLP encode, Keccak256 hash, and `crypto.Ecrecover`) plus several keeper reads (`GetCode`, `GetNonce`, `GetEVMAddress`) for every authorization in the list [3](#0-2) .

Both the EVM CheckTx and DeliverTx ante paths independently repeat this identical unbounded work (`app/ante/evm_checktx.go` and `app/ante/evm_delivertx.go` both call `AssociateAuthorizationAuthorities`) [4](#0-3) [5](#0-4) .

Crucially, the intrinsic-gas check that accounts for authorization-list length (`core.IntrinsicGas(... etx.SetCodeAuthorizations() ...)`) happens in a *separate* ante decorator, `BasicDecorator`, and only rejects the transaction *after* the preprocessing decorator has already run the full authority-recovery loop [6](#0-5) . I was unable to find any explicit length cap on the `AuthList` field itself (only the standard EIP-7702 per-authorization gas cost is charged later at EVM execution time), meaning the number of authorizations a single transaction can carry is limited only by the node's/mempool's max transaction/tx-body byte size, not by a dedicated field-count limit. Since each authorization tuple is a small, fixed-size struct (chain id, address, nonce, v, r, s), a transaction close to the max tx-size limit could carry many thousands of such tuples, each triggering a full ECDSA recovery and multiple KV-store lookups during ante-handling — work that is performed by every validating node for both CheckTx (mempool admission) and DeliverTx (block execution), under a gas meter that does not charge for this computation.

### Impact Explanation
This allows a caller to submit an EVM `SetCode` (EIP-7702, type 4) transaction with a maximally-stuffed authorization list. Every full node processing the transaction — during mempool CheckTx and again during DeliverTx — must perform an unmetered, expensive loop of ECDSA recoveries and state reads before the transaction is ultimately rejected (if the accompanying gas limit is insufficient for intrinsic gas) or accepted. Because CheckTx runs on every node's mempool for every gossip hop, and DeliverTx runs for every validator during block execution, an attacker can cheaply cause disproportionate CPU consumption across the network, which is the same class of unbounded-loop-based resource exhaustion the source report describes (large recipient array driving unmetered/underpriced work in a single call). At sufficient authorization-list sizes this could push per-transaction or per-block processing time past acceptable bounds, risking delayed block production.

### Likelihood Explanation
Reachable by any unprivileged transaction sender — no special privileges are required to construct and broadcast an EIP-7702 SetCode transaction with an oversized authorization list; only the tx-size limit constrains it. Likelihood depends on how large the maximum tx-body size is relative to the ~100-byte-per-authorization tuple cost and how expensive the corresponding block-time budget is; I was not able to fully verify the exact configured max-tx-size in this environment or measure precise per-authorization CPU cost, so the severity should be validated with a benchmark of `associateAuthorizationAuthorities` at the practical maximum authorization-list length permitted by the node's configured `max_tx_bytes`.

### Recommendation
Bound the number of `SetCodeAuthorization` entries processed by `associateAuthorizationAuthorities` (and reject transactions exceeding that bound early, before any recovery work), and/or move the intrinsic-gas/authorization-count check in `BasicDecorator` ahead of `EVMPreprocessDecorator`'s authority pre-association loop so oversized authorization lists are rejected before the expensive per-entry work runs.

### Proof of Concept
1. Construct a valid EIP-7702 `SetCodeTx` with a very large `AuthList` (many thousands of syntactically-valid `SetCodeAuthorization` tuples, sized to approach the node's max transaction byte limit), with a gas limit deliberately set too low to cover the resulting intrinsic gas cost.
2. Broadcast the transaction to a Sei full node's mempool.
3. Observe that `EvmCheckTxAnte` → `EVMPreprocessDecorator`/`AssociateAuthorizationAuthorities` (or `AssociateAuthorizationAuthorities` in `evm_checktx.go`) fully iterates and attempts recovery/association for every authorization entry — consuming CPU and store I/O proportional to authorization-list length — before the transaction is ultimately rejected by `BasicDecorator`'s intrinsic-gas check.
4. Repeat/parallelize submission of many such transactions to amplify aggregate CPU load across mempool-gossiping and block-producing nodes.

Note: I could not fully confirm the exact configured maximum transaction size limit in this deployment (searches surfaced tendermint/mempool config fields but not a definitive effective cap tied to this build), so the concrete achievable authorization-list size and resulting wall-clock cost per transaction should be measured directly against the target node configuration to confirm the practical severity.

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

**File:** x/evm/ante/preprocess.go (L122-146)
```go
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

**File:** utils/helpers/address.go (L143-189)
```go
// AuthorityToPreAssociate returns the authority of an EIP-7702 authorization that should be
// associated with its true (pubkey-derived) Sei address before execution, or ok=false.
//
// It mirrors go-ethereum's StateTransition.validateAuthorization (chain id, nonce overflow,
// authority code, and account-nonce checks) so that pre-association happens only for
// authorizations the EVM will actually apply, and additionally skips authorities that are
// already associated. Mirroring validateAuthorization is essential to security: the
// authorization sig hash is computed from the auth's own ChainID, so recovery and
// auth.Authority() succeed for an authorization signed for ANY chain. Without these checks a
// publicly-visible authorization a user signed for another chain (e.g. Ethereum mainnet)
// could be replayed in a sponsored Sei SetCode tx to force-associate them — migrating their
// direct-cast balance and orphaning staking/distribution state — even though the EVM skips
// the wrong-chain authorization and installs no delegation.
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
}
```

**File:** app/ante/evm_checktx.go (L60-68)
```go
	// Mirror the deliver-tx path and pre-associate EIP-7702 authorization authorities so the
	// mempool's check state matches how the transaction will execute. CheckTx runs on a
	// throwaway cache (its writes are never committed) and never applies authorizations, so
	// this cannot itself cause the direct-cast halt; it is here purely for mempool consistency
	// with EvmDeliverTxAnte.
	AssociateAuthorizationAuthorities(ctx, ek, etx)
	if _, err := EvmCheckAndChargeFees(ctx, evmAddr, ek, upgradeKeeper, txData, etx, msg, version, false); err != nil {
		return ctx, err
	}
```

**File:** app/ante/evm_delivertx.go (L42-50)
```go
	// EIP-7702 authorization authorities are distinct accounts from the tx sender, so the
	// sender association above does not cover them. Pre-associate each authority the EVM will
	// apply to its true (pubkey-derived) Sei address before execution installs delegation code,
	// otherwise SetCode creates a mutable direct-cast mapping that a later associatePubKey can
	// remap, orphaning staking/distribution state (which can halt the chain via the distribution
	// validator-removal hook).
	AssociateAuthorizationAuthorities(ctx, ek, etx)
	ctx = DecorateNonceCallback(ctx, ek, evmAddr, etx.Nonce())
	if err := EvmDeliverChargeFees(ctx, ek, upgradeKeeper, txData, etx, msg, version, evmAddr); err != nil {
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
