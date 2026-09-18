### Title
Cast-address remapping via `associatePubKey`/`associate` orphans locked/staked funds and desyncs address ownership - (File: `precompiles/addr/addr.go`)

### Summary
The MIMO report's root cause is that a "registry" is supposed to be the single source of truth for ownership, but ownership can silently change without the registry being updated (or the registry can be pointed at a new owner while the old identity retains privileged state), causing loss of funds and access-control confusion. Sei-chain has a structurally identical pattern in the EVM↔Sei dual-address system: every EVM address has a deterministic "cast" Sei address (`sdk.AccAddress(evmAddr[:])`) that is used as the default identity anywhere funds, delegations, or module state accrue before an explicit association exists, but a later `associatePubKey`/`associate` precompile call can permanently repoint that EVM address to a *different, truly-derived* Sei address, leaving state under the old cast identity orphaned.

### Finding Description
`x/evm/keeper/address.go`'s `SetAddressMapping` unconditionally overwrites both directions of the EVM↔Sei mapping: [1](#0-0) . The only guard against re-association is in `precompiles/addr/addr.go`'s `associateAddresses`, which checks only that the *Sei* address is not already forward-mapped (`GetEVMAddress(ctx, seiAddr)`); it never checks whether the *EVM* address already backs an existing cast identity with real on-chain state: [2](#0-1) .

When `AssociateAddresses` runs, it migrates only `SpendableCoins` and wei balance away from the cast address; anything counted as `LockedCoins` (bonded/staked delegations, vesting, etc.) is left behind under the cast address and the cast account is not removed: [3](#0-2) . After association, `CanAddressReceive`/`GetSeiAddress` treat the cast address as belonging to the *new* true owner, not the entity that actually holds the locked state, so the cast address can no longer receive further funds: [4](#0-3) .

The team's own tests confirm this exact orphaning scenario occurs for validators: a validator/delegator created under a direct-cast Sei address can have its EVM address re-associated away via `associatePubKey`, after which the operator/delegator address can no longer receive funds, forcing `AfterValidatorRemoved` commission force-withdraw to fail and requiring a special community-pool fallback to avoid panicking during EndBlock: [5](#0-4) . The EIP-7702 SetCode path required an explicit, separate fix (`AssociateAuthorizationAuthorities`) specifically because "SetCode creates a mutable direct-cast EVM->Sei mapping that a later associatePubKey call can remap, orphaning any staking/distribution state created under the direct-cast identity (which can then halt the chain via the distribution validator-removal hook)": [6](#0-5) , [7](#0-6) .

This shows the underlying primitive — `associate`/`associatePubKey` repointing an EVM address's Sei identity while pre-existing locked/staked state remains keyed to the old cast address — is a generally reachable pattern, not limited to the SetCode authority case that was patched. Any user can, without any permission check, trigger `Associate`/`AssociatePubKey` on the `0x0000...1004` precompile for **any** EVM address they control the private key for, as long as no prior explicit forward-association exists for that address's cast identity's "true" Sei address. If that EVM address's cast Sei address already accumulated bonded delegations, an operator role, unbonding queue entries, or other locked-coin state (e.g., because a third party self-delegated to it, or staking rewards accrued to it, before the owner ever associated), that state becomes orphaned once `associatePubKey`/`associate` runs, exactly mirroring the MIMO bug's "registry does not guarantee ownership / owner change is not registered."

### Impact Explanation
Only the validator-commission force-withdraw path has been shown to have a defensive fallback (routing to community pool instead of panicking). Other flows that pay out to a cast address after it becomes "unreceivable" — e.g., staking unbonding-maturity payouts, redelegation completions, or any bank transfer targeting the stale cast identity — were not shown to have equivalent guards, and locked coins left under the cast address after association are never migrated. This can result in either (a) permanent freezing of the locked funds/delegations under the abandoned cast identity, or (b) a failed bank transfer inside an EndBlock/hook code path that is not defensively handled, which — per the pattern already documented in the codebase's own comments — "can halt the chain via the distribution validator-removal hook" if a similar unguarded code path exists elsewhere.

### Likelihood Explanation
Medium-to-High. Any unprivileged user can drive this: (1) an EVM address accrues locked/staked state under its default cast Sei address before ever sending a Sei/EVM transaction (e.g., someone delegates to it, or it becomes a validator operator address, or receives vesting/locked funds), and (2) the true key holder (or anyone colluding with them) later calls `associate`/`associatePubKey` on the public `addr` precompile, which succeeds because the precompile only checks the target Sei address's forward mapping, not whether the EVM address's cast identity already has irrevocable locked state. The codebase's own regression tests and comments confirm this exact class of desync is real and was only partially patched for one specific hook.

### Recommendation
- In `AssociationHelper.AssociateAddresses` / the `associate`/`associatePubKey` precompile handlers, check `LockedCoins`, staking delegations/validator operator status, and any other module-owned state keyed to the cast address before allowing re-association; reject or require explicit migration of that state.
- Audit all EndBlock/hook code paths that can pay out to a Sei address (unbonding completion, redelegation completion, gov deposit refunds, etc.) for the same "unreceivable cast address" failure mode already fixed for `AfterValidatorRemoved`, and add equivalent non-panicking fallbacks (or better, prevent the desync at the source as above).
- Consider making cast-address state migration mandatory and atomic (sweep locked coins or block association until unbonding/vesting completes) rather than optional/best-effort.

### Proof of Concept
Conceptual (root cause confirmed by existing tests, full exploit reachability from a public EVM precompile call not independently re-executed here):
1. Attacker/observer notices an EVM address `E` has not yet called `Associate`/`AssociatePubKey`; its default cast Sei address is `castAddr = bech32(E)`.
2. A third party (or the same actor) delegates stake to `castAddr` as a validator operator/self-delegator, or otherwise causes locked coins to accrue there (as in [8](#0-7) ).
3. The holder of `E`'s private key calls the `addr` precompile's `associatePubKey`/`associate` method (`precompiles/addr/addr.go` lines 205-255), which passes the only check (`GetEVMAddress(ctx, seiAddr)` not found) and calls `SetAddressMapping`, repointing `E` to the new true Sei address.
4. `MigrateBalance` sweeps only spendable/wei balances, not `LockedCoins`, so the delegation/locked funds remain under `castAddr`, which is now permanently unreceivable per `CanAddressReceive`.
5. Any subsequent EndBlock/hook payout targeting `castAddr` either freezes the funds indefinitely or fails in a code path lacking the community-pool-style fallback that was added only for `AfterValidatorRemoved`.

### Citations

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

**File:** x/evm/keeper/address.go (L78-86)
```go
// A sdk.AccAddress may not receive funds from bank if it's the result of direct-casting
// from an EVM address AND the originating EVM address has already been associated with
// a true (i.e. derived from the same pubkey) sdk.AccAddress.
func (k *Keeper) CanAddressReceive(ctx sdk.Context, addr sdk.AccAddress) bool {
	directCast := common.BytesToAddress(addr) // casting goes both directions since both address formats have 20 bytes
	associatedAddr, isAssociated := k.GetSeiAddress(ctx, directCast)
	// if the associated address is the cast address itself, allow the address to receive (e.g. EVM contract addresses)
	return associatedAddr.Equals(addr) || !isAssociated // this means it's either a cast address that's not associated yet, or not a cast address at all.
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

**File:** utils/helpers/associate.go (L57-82)
```go
func (p AssociationHelper) MigrateBalance(ctx sdk.Context, evmAddr common.Address, seiAddr sdk.AccAddress, migrateUseiOnly bool) error {
	castAddr := sdk.AccAddress(evmAddr[:])
	if castAddr.Equals(seiAddr) {
		return nil
	}
	var castAddrBalances sdk.Coins
	if migrateUseiOnly {
		castAddrBalances = sdk.Coins{p.bankKeeper.GetBalance(ctx, castAddr, "usei")}
	} else {
		castAddrBalances = p.bankKeeper.SpendableCoins(ctx, castAddr)
	}
	if !castAddrBalances.IsZero() {
		if err := p.bankKeeper.SendCoins(ctx, castAddr, seiAddr, castAddrBalances); err != nil {
			return err
		}
	}
	castAddrWei := p.bankKeeper.GetWeiBalance(ctx, castAddr)
	if !castAddrWei.IsZero() {
		if err := p.bankKeeper.SendCoinsAndWei(ctx, castAddr, seiAddr, sdk.ZeroInt(), castAddrWei); err != nil {
			return err
		}
	}
	if p.bankKeeper.LockedCoins(ctx, castAddr).IsZero() {
		p.accountKeeper.RemoveAccount(ctx, authtypes.NewBaseAccountWithAddress(castAddr))
	}
	return nil
```

**File:** sei-cosmos/x/distribution/keeper/keeper_test.go (L95-123)
```go
// TestAfterValidatorRemovedRoutesToCommunityPoolForUnreceivableValidator covers the
// case where the validator operator address itself cannot receive funds — its EVM
// address was re-associated (e.g. via associatePubKey) away from the direct-cast Sei
// address it was created under. The withdraw-address fallback in GetDelegatorWithdrawAddr
// resolves back to that same unreceivable operator address, so the commission
// force-withdraw fails. AfterValidatorRemoved runs during EndBlock, so it must not panic:
// the commission is routed to the community pool instead, which conserves value because
// the coins already back the distribution module account.
func TestAfterValidatorRemovedRoutesToCommunityPoolForUnreceivableValidator(t *testing.T) {
	app := seiapp.Setup(t, false, false, false)
	ctx := app.BaseApp.NewContext(false, tmproto.Header{})

	// The validator operator is the direct-cast Sei address of an EVM address.
	evmAddr := common.HexToAddress("0x3333333333333333333333333333333333333333")
	castAddr := sdk.AccAddress(evmAddr[:])
	valAddr := sdk.ValAddress(castAddr)
	valAccAddr := sdk.AccAddress(valAddr) // == castAddr

	require.True(t, app.BankKeeper.CanSendTo(ctx, castAddr))

	// Re-associate the EVM address to a different true Sei address, mirroring
	// associatePubKey after a validator was created under the direct-cast address.
	associatedAddr := seiapp.AddTestAddrs(app, ctx, 1, sdk.NewInt(1000000000))[0]
	app.EvmKeeper.SetAddressMapping(ctx, associatedAddr, evmAddr)

	// The operator/delegator address can no longer receive funds, and the
	// withdraw-address fallback resolves back to that same unreceivable address.
	require.False(t, app.BankKeeper.CanSendTo(ctx, castAddr))
	require.Equal(t, valAccAddr.String(), app.DistrKeeper.GetDelegatorWithdrawAddr(ctx, valAccAddr).String())
```

**File:** app/ante/evm_checktx.go (L251-267)
```go
// AssociateAuthorizationAuthorities pre-associates every EIP-7702 SetCode authorization
// authority in the transaction with its true (pubkey-derived) Sei address before EVM
// execution installs delegation code for it. Authorities are distinct accounts from the tx
// sender, so the sender association performed by the caller does not cover them. Without
// this, SetCode creates a mutable direct-cast EVM->Sei mapping that a later associatePubKey
// call can remap, orphaning any staking/distribution state created under the direct-cast
// identity (which can then halt the chain via the distribution validator-removal hook).
//
// This is the legacyabci counterpart of x/evm/ante's EVMPreprocessDecorator so both ante
// paths associate authorities identically. It is best-effort: only authorities whose
// authorization the EVM will actually apply are pre-associated — helpers.AuthorityToPreAssociate
// enforces the same chain-id/nonce/account-code checks go-ethereum uses, which also prevents
// replaying a publicly-visible authorization a user signed for another chain to force-associate
// them. Each association runs in its own cache context and is only committed on success, so an
// already-associated, skipped, or failing authority affects only itself and never rejects the
// transaction (matching go-ethereum, which would still accept it). Non-SetCode transactions
// carry no authorizations and are a no-op.
```

**File:** x/evm/ante/preprocess.go (L103-110)
```go
	// EIP-7702 authorization authorities are distinct accounts from the tx sender, so the
	// sender association above does not cover them. Associate each authority to its true
	// (pubkey-derived) Sei address before EVM execution installs delegation code for it.
	// Otherwise SetCode creates a mutable direct-cast EVM->Sei mapping that a later
	// associatePubKey call can remap, orphaning any staking/distribution state created
	// under the direct-cast identity (which can then halt the chain via the distribution
	// validator-removal hook).
	p.associateAuthorizationAuthorities(ctx, msg, associateHelper)
```
