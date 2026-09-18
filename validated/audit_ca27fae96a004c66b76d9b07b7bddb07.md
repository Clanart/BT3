### Title
Unbounded per-transaction EIP-7702 authorization-list processing under an infinite gas meter enables ante-handler CPU exhaustion - (File: x/evm/ante/preprocess.go)

### Summary
The Tomcat advisory (CVE-2025-48988) is a resource-allocation-without-limits DoS: a single client request can force the server to perform an unbounded amount of processing work before any size/limit accounting is applied. The closest reachable analog in sei-chain is in the EVM ante pipeline's handling of EIP-7702 `SetCode` transactions: for every submitted `MsgEVMTransaction`, `EVMPreprocessDecorator.AnteHandle` iterates over *all* authorization tuples in the transaction and performs expensive per-entry work (ECDSA recovery, state reads, and a `CacheContext`/write per authority) — all while the ante context is running under `sdk.NewInfiniteGasMeterWithMultiplier`, i.e. before the real EVM gas limit is installed by `GasDecorator` later in the chain.

### Finding Description
`NewAnteHandler` chains the EVM ante decorators in this order: `EVMNoCosmosFieldsDecorator → EVMPreprocessDecorator → BasicDecorator → EVMFeeCheckDecorator → EVMSigVerifyDecorator → GasDecorator`. [1](#0-0) 

`EVMPreprocessDecorator.AnteHandle` first calls `Preprocess`, then explicitly swaps in an infinite gas meter (`sdk.NewInfiniteGasMeterWithMultiplier`) before doing any per-authority work, and only afterwards calls `p.associateAuthorizationAuthorities`: [2](#0-1) [3](#0-2) 

`associateAuthorizationAuthorities` unpacks the `SetCodeTx`, converts it into an `ethtypes.Transaction`, and loops over **every** `SetCodeAuthorization` entry in the tuple list with no cap on count, calling `helpers.AuthorityToPreAssociate` (which does an `Ecrecover`, an `auth.Authority()` re-derivation, plus `GetCode`/`GetNonce`/`GetEVMAddress` state reads) and, for entries that pass, opens a `ctx.CacheContext()` and runs `AssociateAddresses` (multiple account/bank reads and writes) for each one: [4](#0-3) [5](#0-4) [6](#0-5) 

The actual real gas limit for the transaction is only installed later, at the end of the EVM ante chain, in `GasDecorator.AnteHandle`: [7](#0-6) 

Because the authorization list is fully processed while gas metering is infinite, the *number of ECDSA recoveries and KV reads/writes performed in the ante handler is bounded only by transaction size, not by gas*. EIP-7702 puts no protocol limit on the authorization-list length; an authorization tuple is small (chain ID, address, nonce, y-parity, r, s — roughly a hundred bytes), so a single transaction sized at the node's ordinary tx-size ceiling can carry many thousands of authorization tuples, each of which will trigger an `Ecrecover` call plus several keeper reads/writes in the ante pipeline before the transaction's own gas limit or fee sufficiency is even checked.

Critically, this ante chain runs identically for `CheckTx` (mempool admission) via `EvmCheckTxAnte`/router logic before any EVM execution begins, so an attacker does not need the transaction to be included in a block or to pay full execution gas to inflict this CPU cost on every node — an unbounded number of unfunded/rejected probe transactions repeatedly re-submitted to `CheckTx` can each trigger this expensive unmetered loop. [8](#0-7) 

### Impact Explanation
Every full node (validator or public RPC-attached node) that receives this transaction — during `CheckTx`, `ReCheckTx`, or `DeliverTx` — performs unmetered, unbounded CPU work (signature recovery is computationally the most expensive step, roughly on the order of that in normal tx signature verification, repeated per authorization) plus repeated state reads/writes per authorization entry, scaling with the number of tuples an attacker packs into one transaction. A validator that must process such a transaction as part of a proposed block (or that continually re-validates it while it sits in mempool via `CheckTx`/`ReCheckTx`) can be delayed well beyond normal block-processing time, directly risking the "block delay beyond 2.5 seconds" / "validator halt" impact class, and can be similarly weaponized against public RPC nodes' mempool admission path with no funds required if the transaction is otherwise rejected downstream (e.g., insufficient fee) after the expensive preprocessing has already run.

### Likelihood Explanation
Reachable by any unprivileged transaction sender: submitting a normally-signed `SetCode` (EIP-7702, type-4) Ethereum transaction with a maximal authorization list is a standard, permissionless RPC action (`eth_sendRawTransaction`) requiring no special privileges, validator status, or precompile access. The only constraints are the node's ordinary transaction/mempool size limits, which do not scale down the per-authorization-tuple processing cost, and the fact that `SetCode` transactions are only accepted from `derived.Prague` version onward (already active on current chain configuration per `AllowedTxTypes`). [9](#0-8) 

### Recommendation
Bound the number of `SetCodeAuthorizations` processed by `associateAuthorizationAuthorities` (e.g., mirroring a sane per-tx cap or making the loop cost proportional to gas actually charged/available), and/or move authorization pre-association after (or under) a properly metered gas meter so the cost is charged to the sender rather than performed for free under an infinite gas meter. At minimum, reject `SetCode` transactions whose authorization-list length exceeds a fixed, safe cap during the cheap `ValidateBasic`/stateless checks (`EvmStatelessChecks` / `BasicDecorator`) before the expensive per-authority recovery loop runs.

### Proof of Concept
1. Craft an EIP-7702 (`SetCodeTx`, type 4) Ethereum transaction with a very large `AuthorizationList` (many thousands of well-formed `SetCodeAuthorization` tuples, each independently signed with a random key so `Ecrecover` succeeds and each authority's on-chain nonce/code state satisfies `AuthorityToPreAssociate`'s pass conditions to maximize work done, e.g., nonce 0, no code).
2. Submit the transaction via `eth_sendRawTransaction` to a node's RPC endpoint (or gossip it directly) so it enters `CheckTx`.
3. Observe that `EvmCheckTxAnte` → `EVMPreprocessDecorator.AnteHandle` → `associateAuthorizationAuthorities` iterates the entire authorization list under an infinite gas meter, performing one `Ecrecover` + several keeper reads/writes per tuple, consuming CPU time proportional to the crafted list size regardless of the transaction's declared/paid gas.
4. Repeat submission (optionally with an insufficient fee so the transaction is rejected right after this preprocessing step, allowing free repeated resubmission) to amplify load across mempool `CheckTx`/`ReCheckTx` cycles on all connected nodes.

### Citations

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

**File:** x/evm/ante/preprocess.go (L42-46)
```go
var AllowedTxTypes = map[derived.SignerVersion][]uint8{
	derived.London: {ethtypes.LegacyTxType, ethtypes.AccessListTxType, ethtypes.DynamicFeeTxType},
	derived.Cancun: {ethtypes.LegacyTxType, ethtypes.AccessListTxType, ethtypes.DynamicFeeTxType, ethtypes.BlobTxType},
	derived.Prague: {ethtypes.LegacyTxType, ethtypes.AccessListTxType, ethtypes.DynamicFeeTxType, ethtypes.BlobTxType, ethtypes.SetCodeTxType},
}
```

**File:** x/evm/ante/preprocess.go (L58-66)
```go
func (p *EVMPreprocessDecorator) AnteHandle(ctx sdk.Context, tx sdk.Tx, simulate bool, next sdk.AnteHandler) (sdk.Context, error) {
	msg := evmtypes.MustGetEVMTransactionMessage(tx)
	if err := Preprocess(ctx, msg, p.evmKeeper.ChainID(ctx), p.evmKeeper.EthBlockTestConfig.Enabled); err != nil {
		return ctx, err
	}

	// use infinite gas meter for EVM transaction because EVM handles gas checking from within
	ctx = ctx.WithGasMeter(sdk.NewInfiniteGasMeterWithMultiplier(ctx))

```

**File:** x/evm/ante/preprocess.go (L103-113)
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

**File:** utils/helpers/address.go (L143-188)
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

**File:** x/evm/ante/gas.go (L24-44)
```go
// Called at the end of the ante chain to set gas limit and gas used estimate properly
func (gl GasDecorator) AnteHandle(ctx sdk.Context, tx sdk.Tx, simulate bool, next sdk.AnteHandler) (sdk.Context, error) {
	msg := evmtypes.MustGetEVMTransactionMessage(tx)
	txData, err := evmtypes.UnpackTxData(msg.Data)
	if err != nil {
		return ctx, err
	}

	txGas := txData.GetGas()
	if txGas > math.MaxInt64 {
		return ctx, errors.New("tx gas exceeds max")
	}
	adjustedGasLimit := gl.evmKeeper.GetPriorityNormalizer(ctx).MulInt64(int64(txGas)) //nolint:gosec
	gasMeter := sdk.NewGasMeterWithMultiplier(ctx, adjustedGasLimit.TruncateInt().Uint64())
	ctx = ctx.WithGasMeter(gasMeter)
	if tx.GetGasEstimate() >= MinGasEVMTx {
		ctx = ctx.WithGasEstimate(tx.GetGasEstimate())
	} else {
		ctx = ctx.WithGasEstimate(gasMeter.Limit())
	}
	return next(ctx, tx, simulate)
```

**File:** app/ante/evm_checktx.go (L35-76)
```go
func EvmCheckTxAnte(
	ctx sdk.Context,
	tx sdk.Tx,
	upgradeKeeper *upgradekeeper.Keeper,
	ek *evmkeeper.Keeper,
) (returnCtx sdk.Context, returnErr error) {
	chainID := ek.ChainID(ctx)
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
	if _, err := EvmCheckAndChargeFees(ctx, evmAddr, ek, upgradeKeeper, txData, etx, msg, version, false); err != nil {
		return ctx, err
	}

	ctx, err = CheckNonce(ctx, ek, etx, evmAddr)
	if err != nil {
		return ctx, err
	}

	return DecorateContext(ctx, ek, tx, txData, etx, evmAddr, seiAddr), nil
}
```
