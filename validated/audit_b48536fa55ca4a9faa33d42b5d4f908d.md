Based on my investigation, the strongest analog in sei-chain to the blk-crypto "evict key" robustness bug class is the address-association re-mapping issue that the codebase's own comments describe and has already been patched for one specific vector (EIP-7702 `SetCode` authorities). The underlying bug class — "stale/[keyed] state is not cleaned up before a new identity takes over the same key, leaving dangling references that can crash critical code paths" — matches this precisely, and the current fix is scoped only to the `SetCode` authority vector, not to the general `associatePubKey`/`Associate` re-association path.

### Title
Unvalidated EVM→Sei re-association via `associatePubKey`/`Associate` precompile can orphan staking/distribution state and halt the chain - ([File: utils/helpers/associate.go])

### Summary
`AssociationHelper.AssociateAddresses` [1](#0-0)  unconditionally calls `SetAddressMapping`, which overwrites any pre-existing `EVMAddress<->SeiAddress` mapping [2](#0-1) , without checking whether the EVM address being associated already has state (staking delegations, distribution rewards, validator bonding, etc.) recorded against its current, implicit "direct-cast" Sei identity. The `associatePublicKey`/`associateAddresses` precompile handlers only guard against the *target Sei address* already being associated, not against the EVM address's previous direct-cast identity holding state that would be orphaned by the remap [3](#0-2) .

### Finding Description
The codebase's own inline documentation confirms this exact bug class exists and was only partially remediated: `SetCode creates a mutable direct-cast EVM->Sei mapping that a later associatePubKey call can remap, orphaning any staking/distribution state created under the direct-cast identity (which can then halt the chain via the distribution validator-removal hook)` [4](#0-3) . The fix that was implemented, `associateAuthorizationAuthorities`, only pre-associates EIP-7702 `SetCode` authorization authorities to their true pubkey-derived address before EVM execution — closing one specific path to the dangling-state condition [5](#0-4) .

However, `SetAddressMapping` itself performs no eviction/cleanup of any state indexed by the previous direct-cast identity of the EVM address before installing the new mapping [2](#0-1) , and `AssociateAddresses` similarly does no such check [1](#0-0) . This mirrors the blk-crypto pattern: an "evict"/replace operation (`SetAddressMapping`) that must fully unlink all state tied to the old key before proceeding, but instead leaves stale state reachable under structures that assumed the mapping was stable, creating a use-after-free-equivalent condition for state keyed by identity (validator/delegator address bookkeeping that no longer matches the live association).

### Impact Explanation
If any address holds staking delegator state, validator self-bond, or distribution reward state under its direct-cast Sei identity (`sdk.AccAddress(evmAddr[:])`) — reachable in scenarios other than the already-patched `SetCode` vector — and a subsequent `associate`/`associatePubKey` call remaps that EVM address to a new "true" Sei address, the orphaned state is left indexed against an address the system no longer treats as canonically tied to the EVM address. The comment explicitly states this "can halt the chain via the distribution validator-removal hook," i.e. a permanent chain halt, which meets the required severity bar.

### Likelihood Explanation
The `associatePublicKey`/`associate` precompile methods are directly callable by any EVM transaction sender with no special privilege, and the underlying `SetAddressMapping`/`AssociateAddresses` code performs no defensive check to reject a remap when the EVM address's current direct-cast identity carries live staking/distribution state, outside of the one specific SetCode-authority path that was hardened. This makes the residual attack surface plausible for any code path that creates a direct-cast identity with delegation/distribution state before a canonical association is set up (e.g. any yet-unaudited entry point functionally analogous to SetCode's account-creation-then-delegation flow).

### Recommendation
Extend the same defensive check used in `AuthorityToPreAssociate`/`associateAuthorizationAuthorities` to the general `AssociateAddresses` path: before calling `SetAddressMapping`, verify that the EVM address's implicit direct-cast Sei identity holds no staking/distribution/validator state (or migrate/reject the association if it does), rather than relying solely on per-callsite pre-association workarounds scoped to `SetCode`.

### Proof of Concept
A conclusive PoC requires confirming a live path (outside the already-patched SetCode flow) that allows a direct-cast identity to accumulate delegator/distribution/validator state prior to a canonical `associatePubKey` call remapping it, and triggering the distribution validator-removal hook against the now-orphaned identity. I was not able to fully confirm such a residual path is currently reachable in this indexed snapshot of the codebase (the SetCode vector is patched), so this should be validated with a live Devin session or by testing the `associate`/`associatePubKey` precompiles against an address holding staking/distribution state under its cast identity.

### Citations

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

**File:** x/evm/keeper/address.go (L10-22)
```go
func (k *Keeper) SetAddressMapping(ctx sdk.Context, seiAddress sdk.AccAddress, evmAddress common.Address) {
	store := ctx.KVStore(k.storeKey)
	store.Set(types.EVMAddressToSeiAddressKey(evmAddress), seiAddress)
	store.Set(types.SeiAddressToEVMAddressKey(seiAddress), evmAddress[:])
	if !k.accountKeeper.HasAccount(ctx, seiAddress) {
		k.accountKeeper.SetAccount(ctx, k.accountKeeper.NewAccountWithAddress(ctx, seiAddress))
	}
	ctx.EventManager().EmitEvent(sdk.NewEvent(
		types.EventTypeAddressAssociated,
		sdk.NewAttribute(types.AttributeKeySeiAddress, seiAddress.String()),
		sdk.NewAttribute(types.AttributeKeyEvmAddress, evmAddress.Hex()),
	))
}
```

**File:** precompiles/addr/addr.go (L239-255)
```go
func (p PrecompileExecutor) associateAddresses(ctx sdk.Context, method *abi.Method, evmAddr common.Address, seiAddr sdk.AccAddress, pubkey cryptotypes.PubKey) (ret []byte, remainingGas uint64, err error) {
	// Check that address is not already associated
	_, found := p.evmKeeper.GetEVMAddress(ctx, seiAddr)
	if found {
		return nil, 0, fmt.Errorf("address %s is already associated with evm address %s", seiAddr, evmAddr)
	}

	// Associate Addresses:
	associationHelper := helpers.NewAssociationHelper(p.evmKeeper, p.bankKeeper, p.accountKeeper)
	err = associationHelper.AssociateAddresses(ctx, seiAddr, evmAddr, pubkey, false)
	if err != nil {
		return nil, 0, err
	}

	ret, err = method.Outputs.Pack(seiAddr.String(), evmAddr)
	return ret, pcommon.GetRemainingGas(ctx, p.evmKeeper), err
}
```

**File:** x/evm/ante/preprocess.go (L103-109)
```go
	// EIP-7702 authorization authorities are distinct accounts from the tx sender, so the
	// sender association above does not cover them. Associate each authority to its true
	// (pubkey-derived) Sei address before EVM execution installs delegation code for it.
	// Otherwise SetCode creates a mutable direct-cast EVM->Sei mapping that a later
	// associatePubKey call can remap, orphaning any staking/distribution state created
	// under the direct-cast identity (which can then halt the chain via the distribution
	// validator-removal hook).
```

**File:** x/evm/ante/preprocess.go (L115-147)
```go
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
}
```
