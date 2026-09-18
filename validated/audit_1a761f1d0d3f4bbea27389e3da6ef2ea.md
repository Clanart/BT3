Based on my investigation, I found a genuine analog reachable by an unprivileged EVM transaction sender.

### Title
Unbounded EIP-7702 authorization list allows a single transaction to trigger unmetered per-authority ECDSA recovery and state-write work in the ante handler - (File: `x/evm/ante/preprocess.go`)

### Summary
`EVMPreprocessDecorator.associateAuthorizationAuthorities` iterates over every `SetCodeAuthorization` tuple in an EIP-7702 `SetCodeTx` and, for each one, performs signature recovery, several state store lookups, and a full association write, all under an **infinite gas meter** and *before* intrinsic gas / auth-list-size is checked meaningfully against real cost. There is no cap on how many authorization tuples a single transaction may carry, so a single submitted transaction can force this loop to run an attacker-controlled, arbitrarily large number of times on every node that processes/validates the transaction.

### Finding Description
`NewEVMPreprocessDecorator.AnteHandle` first calls `Preprocess`, then immediately swaps in an infinite gas meter because "EVM handles gas checking from within": [1](#0-0) 

It then unconditionally calls `associateAuthorizationAuthorities`, which loops over `ethTx.SetCodeAuthorizations()` with no bound on `len(...)`, performing `helpers.AuthorityToPreAssociate` (ECDSA signature recovery + several keeper state reads) and, for every tuple that passes, a full `AssociateAddresses` state write in a cache context: [2](#0-1) 

`AuthorityToPreAssociate` performs `RecoverAddressesFromAuthorization` (RLP encode + keccak256 + ECDSA pubkey recovery) plus several `GetCode`/`GetNonce`/`GetEVMAddress` state reads per authorization tuple: [3](#0-2) 

The only size validation applied to `AuthList` is that it's non-empty; there is no upper bound on the number of authorization tuples: [4](#0-3) 

Crucially, intrinsic gas (`core.IntrinsicGas(...)`, which does scale cost with `len(SetCodeAuthorizations())`) is only checked in `BasicDecorator`, which runs **after** `EVMPreprocessDecorator` in the ante chain: [5](#0-4) [6](#0-5) 

This means the full authority-association loop — ECDSA recoveries, multiple store reads, and cache-context writes for potentially thousands of authorization tuples — executes and completes *before* any gas-based rejection can occur, and it executes under an infinite gas meter so it is never gas-metered/capped at all. The same unbounded pattern also exists in the legacyabci/giga path: [7](#0-6) [8](#0-7) 

The only practical limit on the number of authorization tuples in one transaction is the raw transaction byte-size limit; since each SetCodeAuthorization tuple is compact (chainID, address, nonce, v, r, s — roughly 100 bytes), a transaction near the typical size limit (e.g. ~100KB–1MB depending on node/mempool config) can carry many thousands of authorization tuples, each independently signed for potentially many different accounts, forcing thousands of ECDSA recoveries and store I/O operations in ante-handler processing alone — work that every full node performing CheckTx/DeliverTx/mempool validation on that transaction must repeat.

### Impact Explanation
This is a CPU/compute-cost DoS vector reachable from a single unprivileged EVM transaction: an attacker can craft one `SetCodeTx` with an outsized `AuthList` (bounded only by max tx size) and broadcast it. Every node that receives it in CheckTx (mempool admission), and every validator that must process it in DeliverTx, performs the entire unmetered authority-association loop (ECDSA recovery + several KV-store reads/writes per tuple) before the transaction can be gas-rejected. Because the underlying gas meter is infinite for this segment and the check that *would* charge for auth-list size (`core.IntrinsicGas`) runs strictly afterward, this work is essentially "free" from the gas-accounting/fee perspective but not free computationally. Submitted repeatedly (or as one very large tx, since a single tx can already carry thousands of tuples), this can materially slow down ante-handler processing across the network, increasing block-processing latency — directly aligning with the "block delay beyond 2.5 seconds" / node-processing-DoS criteria, without requiring any special privilege, precompile access, or contract deployment.

### Likelihood Explanation
High feasibility: constructing a `SetCodeTx` with a very large `AuthList` requires only standard tooling (each authorization tuple is a simple, cheaply-computable ECDSA signature over `keccak256(0x05 || rlp([chainId, address, nonce]))`), no special account balance beyond covering the eventual (small) intrinsic-gas-priced fee estimate the mempool would compute after the fact, and no interaction with governance, validators, or privileged roles. The attack surface is the standard EVM transaction submission path (`eth_sendRawTransaction` / p2p tx gossip), which is exactly the "unprivileged transaction sender" / "public EVM JSON-RPC surface" reachable surface explicitly in scope.

### Recommendation
Add an explicit, gas-metered upper bound on the number of `SetCodeAuthorizations` accepted per transaction (mirroring or tightening go-ethereum's per-tx authorization limits), and enforce it — together with normal `core.IntrinsicGas` accounting — **before** `associateAuthorizationAuthorities` (and the legacyabci/giga equivalents) perform any per-authority recovery or state work under the infinite gas meter. Concretely: move (or duplicate) the auth-list-size/intrinsic-gas check to run prior to `EVMPreprocessDecorator`'s authority-association loop (or cap `len(ethTx.SetCodeAuthorizations())` directly inside `associateAuthorizationAuthorities`/`AssociateAuthorizationAuthorities`/`setCodeTxRequiresAuthorityAssociation` before iterating), so a transaction with an excessive authorization list is rejected before the expensive per-tuple work runs.

### Proof of Concept
1. Generate `N` (e.g. 5,000–20,000) distinct private keys, each acting as a would-be "authority."
2. For each key, sign an `ethtypes.SetCodeAuthorization{ChainID: localChainID, Address: <arbitrary target>, Nonce: 0}` via `ethtypes.SignSetCode` (as done in `app/setcode_authority_test.go`).
3. Build a single `ethtypes.SetCodeTx` with `AuthList` containing all `N` authorizations, signed by one sponsor key with sufficient gas fee cap to pass fee checks; the raw tx stays within standard tx-size limits because each tuple is ~100 bytes.
4. Submit the transaction via `eth_sendRawTransaction` (or directly as a `MsgEVMTransaction` to `CheckTx`).
5. Observe that `EVMPreprocessDecorator.AnteHandle` → `associateAuthorizationAuthorities` performs `N` ECDSA-recovery + state-lookup + cache-context-write cycles under the infinite gas meter, before `BasicDecorator`'s `core.IntrinsicGas` check (which would otherwise reject/require higher gas for a huge auth list) is ever reached — measure ante-handler wall-clock time scaling linearly and unboundedly with `N`, with no corresponding gas charge preventing repeated submission of maximal-size variants.

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

**File:** x/evm/ante/preprocess.go (L122-147)
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

**File:** app/ante/evm_checktx_test.go (L30-52)
```go
func TestEvmStatelessChecksRejectsEmptySetCodeAuthList(t *testing.T) {
	chainID := sdk.NewInt(1)
	gasTipCap := sdk.NewInt(50)
	gasFeeCap := sdk.NewInt(100)
	amount := sdk.NewInt(20)
	setCodeTx := &ethtx.SetCodeTx{
		ChainID:   &chainID,
		Nonce:     1,
		GasTipCap: &gasTipCap,
		GasFeeCap: &gasFeeCap,
		GasLimit:  1000,
		To:        common.Address{'a'}.Hex(),
		Amount:    &amount,
		AuthList:  ethtx.AuthList{},
		V:         []byte{3},
		R:         []byte{5},
		S:         []byte{7},
	}
	msg, err := evmtypes.NewMsgEVMTransaction(setCodeTx)
	require.NoError(t, err)

	err = EvmStatelessChecks(sdk.Context{}, evmStatelessCheckTx{msgs: []sdk.Msg{msg}}, big.NewInt(1))
	require.ErrorContains(t, err, "auth list cannot be empty")
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

**File:** x/evm/ante/basic.go (L43-57)
```go
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

**File:** app/app.go (L2083-2090)
```go
func (app *App) setCodeTxRequiresAuthorityAssociation(ctx sdk.Context, ethTx *ethtypes.Transaction) bool {
	for _, auth := range ethTx.SetCodeAuthorizations() {
		if _, _, _, ok := helpers.AuthorityToPreAssociate(ctx, &app.GigaEvmKeeper, auth); ok {
			return true
		}
	}
	return false
}
```
