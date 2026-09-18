### Title
Unbounded, gas-unmetered EIP-7702 authorization-list processing in the EVM ante pipeline enables a single-transaction CPU-exhaustion / block-delay attack - (File: `x/evm/ante/preprocess.go`, `app/ante/evm_checktx.go`)

### Summary
The Morpho report warns that a forced (non-optional) iteration loop whose bound (`maxIterations`) is not tied to the actual gas available can cause reverts or resource exhaustion, because the loop runs to completion before gas accounting catches up. Sei-chain has a structurally similar — and more severe — instance: the EIP-7702 `SetCode` authorization list is iterated and cryptographically verified in the EVM ante pipeline **before gas metering is installed**, with no cap on the list length independent of gas paid.

### Finding Description
For every `SetCodeTx`, both `EVMPreprocessDecorator.AnteHandle` (deliver/simulate path) and `EvmCheckTxAnte`/`EvmDeliverTxAnte` (legacy/CheckTx path) explicitly set an **infinite gas meter** before iterating `etx.SetCodeAuthorizations()`: [1](#0-0) [2](#0-1) 

The same unbounded, unmetered loop also runs on the CheckTx (mempool-admission) path, before any fee/balance check: [3](#0-2) [4](#0-3) 

For each authorization entry, `helpers.AuthorityToPreAssociate` performs an ECDSA signature recovery (`RecoverAddressesFromAuthorization`) plus several state reads, and on success spins up a cache context to write an address association: [5](#0-4) 

Crucially, the *intrinsic-gas* check that go-ethereum normally uses to bound authorization-list size relative to gas paid happens **later**, in `BasicDecorator`, which runs strictly after `EVMPreprocessDecorator` in the ante chain: [6](#0-5) [7](#0-6) 

So the costly authorization loop executes unconditionally and without any gas bound, even for a transaction that will subsequently be rejected for failing the intrinsic-gas or fee check. This mirrors exactly the class of bug in the Morpho report: a "forced" pipeline stage (here, mandatory ante processing for every submitted EVM tx) performs work sized by an attacker-controlled parameter (authorization-list length) with no effective ceiling tied to what the caller actually pays for.

### Impact Explanation
An attacker can craft a single `SetCodeTx` whose authorization list is packed with as many entries as fit within the node's transaction/tx-size limits (each entry is only ChainID + address + nonce + V/R/S, ~100 bytes), yielding thousands of authorizations per transaction. Because the ecrecover-per-authorization loop runs under an infinite gas meter both at CheckTx (mempool ingestion, on every node) and at DeliverTx, submitting a modest number of such transactions forces every validator and full node to perform tens of thousands of secp256k1 recoveries with zero gas charged. This is unbounded CPU consumption reachable from a single unprivileged EVM transaction, and at scale can push per-block processing time past the 2.5-second threshold across the network, i.e. a chain-wide block-delay / validator-halt risk, not merely a local resource issue for the sender.

### Likelihood Explanation
Likelihood is high: SetCode (EIP-7702) transactions are a fully public, standard EVM transaction type reachable by any external RPC client; no special permissions, precompile access, or malicious validator/peer behavior is required. The only constraint is the maximum transaction byte size accepted by the node/mempool, which authorization entries are compact enough to fill efficiently.

### Recommendation
- Bound the number of `SetCodeAuthorizations` processed in `associateAuthorizationAuthorities` / `AssociateAuthorizationAuthorities` to a small, fixed constant (mirroring the `maxNestedMsgs` pattern already used in `app/antedecorators/authz_nested_message.go`) rather than letting it scale with `len(auths)`.
- Alternatively, charge real (metered) gas for each authorization recovered — proportional to go-ethereum's `PerEmptyAccountCost`/`PerAuthBaseCost` — before performing the ECDSA recovery, and abort the loop once the metered budget is exhausted, instead of running it under an infinite gas meter.
- Move (or duplicate) the intrinsic-gas/authorization-count check ahead of `EVMPreprocessDecorator` in the ante chain so oversized authorization lists are rejected cheaply before any recovery work is performed.

### Proof of Concept
1. Construct a `SetCodeTx` (EIP-7702, type 4) whose `AuthList` contains the maximum number of `SetCodeAuthorization` entries that fit under the node's accepted transaction size (each entry ~100 bytes; a multi-hundred-KB to few-MB tx yields several thousand entries). Entries can reference arbitrary, even random, victim keys/addresses — validity of the authorization is irrelevant to triggering the cost, since `RecoverAddressesFromAuthorization` runs on every entry regardless.
2. Submit the transaction via the public EVM JSON-RPC (`eth_sendRawTransaction`).
3. Every node's CheckTx path (`EvmCheckTxAnte` → `AssociateAuthorizationAuthorities`) and, upon inclusion, every validator's DeliverTx path (`EVMPreprocessDecorator.AnteHandle` → `associateAuthorizationAuthorities`) iterate the full authorization list under an infinite gas meter, performing one ECDSA recovery plus state reads per entry, before any fee/intrinsic-gas rejection can occur.
4. Repeating with multiple such transactions in the mempool multiplies the unmetered CPU cost across all nodes, delaying CheckTx admission and block assembly/execution network-wide.

### Citations

**File:** x/evm/ante/preprocess.go (L63-66)
```go

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

**File:** app/ante/evm_checktx.go (L46-68)
```go

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
	if _, err := EvmCheckAndChargeFees(ctx, evmAddr, ek, upgradeKeeper, txData, etx, msg, version, false); err != nil {
		return ctx, err
	}
```

**File:** app/ante/evm_checktx.go (L268-284)
```go
func AssociateAuthorizationAuthorities(ctx sdk.Context, ek *evmkeeper.Keeper, etx *ethtypes.Transaction) {
	auths := etx.SetCodeAuthorizations()
	if len(auths) == 0 {
		return
	}
	associateHelper := helpers.NewAssociationHelper(ek, ek.BankKeeper(), ek.AccountKeeper())
	for _, auth := range auths {
		evmAddr, seiAddr, pubkey, ok := helpers.AuthorityToPreAssociate(ctx, ek, auth)
		if !ok {
			continue
		}
		cacheCtx, write := ctx.CacheContext()
		if err := associateHelper.AssociateAddresses(cacheCtx, seiAddr, evmAddr, pubkey, false); err == nil {
			write()
		}
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

**File:** x/evm/ante/basic.go (L24-57)
```go
// cherrypicked from go-ethereum:txpool:ValidateTransaction
func (gl BasicDecorator) AnteHandle(ctx sdk.Context, tx sdk.Tx, simulate bool, next sdk.AnteHandler) (sdk.Context, error) {
	msg := evmtypes.MustGetEVMTransactionMessage(tx)
	etx, _ := msg.AsTransaction()

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

	if etx.To() == nil && len(etx.Data()) > params.MaxInitCodeSize {
		return ctx, fmt.Errorf("%w: code size %v, limit %v", core.ErrMaxInitCodeSizeExceeded, len(etx.Data()), params.MaxInitCodeSize)
	}

	if etx.Value().Sign() < 0 {
		return ctx, sdkerrors.ErrInvalidCoins
	}

	intrGas, err := core.IntrinsicGas(etx.Data(), etx.AccessList(), etx.SetCodeAuthorizations(), etx.To() == nil, true, true, true)
	if err != nil {
		return ctx, err
	}
	if etx.Gas() < intrGas {
		return ctx, core.ErrIntrinsicGas
	}
```
