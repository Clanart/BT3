### Title
Delegator's pre-association staking rewards become permanently stuck once their EVM address is re-associated, because `BeforeDelegationSharesModified` panics with no fallback - ([File: sei-cosmos/x/distribution/keeper/hooks.go])

### Summary
A user can delegate stake before associating their EVM address (using the direct-cast Sei address as delegator). If they later associate their EVM address to their true pubkey-derived Sei address (e.g. via `associatePubKey`), the original direct-cast address that still owns the delegation becomes unable to receive funds. Any future action that touches that delegation's shares (delegate more, undelegate, redelegate) triggers a distribution hook that tries to pay accrued rewards to that now-unreceivable address, fails, and panics — permanently freezing the delegation.

### Finding Description
Delegation ownership in `x/staking`/`x/distribution` is keyed by the delegator's `sdk.AccAddress`. When a user delegates before ever associating their EVM address, the delegator address used is the direct-cast Sei address of their EVM address, obtained via `GetSeiAddressOrDefault`: [1](#0-0) .

If that user later associates the same EVM address with their *true*, pubkey-derived Sei address (a normal, user-reachable flow via the `addr` precompile's `associatePubKey`/`associate`, or `AssociateAddress`), the mapping is updated with `SetAddressMapping`: [2](#0-1) .

After that, the original direct-cast address can no longer receive funds, per `CanAddressReceive`: [3](#0-2) .

The delegator's default withdraw address, when no custom withdraw address has been set, falls back to the delegator address itself **without any receivability check**: [4](#0-3) .

Rewards are paid out via `SendCoinsFromModuleToAccount` to that withdraw address inside `withdrawDelegationRewards`: [5](#0-4) .

Critically, the `BeforeDelegationSharesModified` staking hook — invoked on every delegate-more, undelegate, or redelegate action against an existing delegation — calls this reward withdrawal and **panics unconditionally on any error**, with no fallback to the community pool or any other safe path: [6](#0-5) .

This is in stark contrast to `AfterValidatorRemoved`, which was hardened specifically for this exact class of bug (an EVM address re-associated away from its direct-cast Sei address making a payout target unreceivable) by adding a `canReceiveWithdrawAddr` check and falling back to the community pool instead of panicking: [7](#0-6) . The codebase's own test explicitly documents this exact re-association scenario as reachable and dangerous: [8](#0-7) . No equivalent protection exists for `BeforeDelegationSharesModified`.

### Impact Explanation
Once the delegator's default withdraw address becomes unreceivable (via re-association), any subsequent transaction that modifies that specific delegation's shares — additional delegation, undelegation, or redelegation, whether via native staking messages or the staking precompile (`delegate`/`undelegate`/`redelegate`) — will panic when the hook attempts to pay out rewards. Because the failure recurs identically every time the same delegation is touched, the user's staked principal on that validator, plus all pending and future rewards for that delegation, become permanently frozen: they can never undelegate, redelegate, or add to that specific delegation again. This is a permanent freezing of funds for the affected delegator, reachable purely through unprivileged, self-inflicted (but entirely legitimate) transaction sequencing — delegate first, associate later.

### Likelihood Explanation
This requires only two ordinary, unprivileged transactions from the victim themselves: (1) delegate stake while unassociated (using the direct-cast address as delegator), and (2) associate the EVM address to its true pubkey-derived Sei address afterward — both are normal, encouraged user flows (associating late is common, e.g. a user might interact with Sei natively first, then later use an EVM wallet/dApp and associate). No attacker or malicious third party is needed; the condition is a natural consequence of the association model already acknowledged and partially patched elsewhere in the codebase (`AfterValidatorRemoved`), but missed in `BeforeDelegationSharesModified`.

### Recommendation
Apply the same defensive pattern used in `AfterValidatorRemoved`: before paying out rewards in `withdrawDelegationRewards` (or specifically in the `BeforeDelegationSharesModified` hook), check `canReceiveWithdrawAddr` on the resolved withdraw address; if unreceivable, either route the truncated rewards to the community pool (consistent with existing dust/remainder handling) or fall back to a receivable address, but do not panic. This prevents any future delegate/undelegate/redelegate action from being permanently blocked.

### Proof of Concept
1. User controls EVM address `E`. Before associating, they delegate to validator `V` using the staking precompile's `delegate` (or a native `MsgDelegate`), which resolves the delegator to `castAddr = sdk.AccAddress(E[:])` via `GetSeiAddressOrDefault`.
2. Rewards begin accruing for delegation `(castAddr, V)`.
3. User calls `associatePubKey` (or sends any Sei-native tx) that associates `E` with their true pubkey-derived Sei address `trueAddr != castAddr`, via `AssociateAddress`/`SetAddressMapping`.
4. `CanAddressReceive(ctx, castAddr)` now returns `false` because `GetSeiAddress(directCast=E)` resolves to `trueAddr`, not `castAddr`.
5. User calls `undelegate`/`redelegate`/`delegate` again on validator `V` (any action that fires `BeforeDelegationSharesModified` for delegator `castAddr`).
6. `withdrawDelegationRewards` attempts `SendCoinsFromModuleToAccount(ctx, distr.ModuleName, castAddr, finalRewards)`, which fails because `castAddr` cannot receive funds; `BeforeDelegationSharesModified` panics, reverting the transaction — and every future attempt to touch this delegation will panic identically, permanently freezing the stake and rewards.

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

**File:** x/evm/keeper/address.go (L58-64)
```go
func (k *Keeper) GetSeiAddressOrDefault(ctx sdk.Context, evmAddress common.Address) sdk.AccAddress {
	addr, ok := k.GetSeiAddress(ctx, evmAddress)
	if ok {
		return addr
	}
	return sdk.AccAddress(evmAddress[:])
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

**File:** sei-cosmos/x/distribution/keeper/store.go (L10-22)
```go
// get the delegator withdraw address, defaulting to the delegator address
func (k Keeper) GetDelegatorWithdrawAddr(ctx sdk.Context, delAddr sdk.AccAddress) sdk.AccAddress {
	store := ctx.KVStore(k.storeKey)
	b := store.Get(types.GetDelegatorWithdrawAddrKey(delAddr))
	if b == nil {
		return delAddr
	}
	withdrawAddr := sdk.AccAddress(b)
	if !k.canReceiveWithdrawAddr(ctx, withdrawAddr) {
		return delAddr
	}
	return withdrawAddr
}
```

**File:** sei-cosmos/x/distribution/keeper/delegation.go (L286-293)
```go
	// add coins to user account
	if !finalRewards.IsZero() {
		withdrawAddr := k.GetDelegatorWithdrawAddr(ctx, del.GetDelegatorAddr())
		err := k.bankKeeper.SendCoinsFromModuleToAccount(ctx, types.ModuleName, withdrawAddr, finalRewards)
		if err != nil {
			return nil, err
		}
	}
```

**File:** sei-cosmos/x/distribution/keeper/hooks.go (L44-72)
```go
		// add to validator account
		if !coins.IsZero() {
			accAddr := sdk.AccAddress(valAddr)
			withdrawAddr := h.k.GetDelegatorWithdrawAddr(ctx, accAddr)

			// GetDelegatorWithdrawAddr falls back to the delegator (accAddr) when the
			// configured withdraw address cannot receive funds, but that fallback can
			// itself be unable to receive — e.g. accAddr is an EVM address whose Sei
			// mapping was re-associated to a different address, so CanAddressReceive
			// rejects it. This hook runs in EndBlock, so attempting the send and
			// panicking on the resulting bank error would halt the chain. Check
			// receivability first: when the recipient cannot receive, route the
			// commission to the community pool instead. The coins already back the
			// distribution module account (where community pool funds are held), so this
			// conserves value and avoids the partial module-account debit that a failed
			// SendCoins leaves behind.
			if h.k.canReceiveWithdrawAddr(ctx, withdrawAddr) {
				if err := h.k.bankKeeper.SendCoinsFromModuleToAccount(ctx, types.ModuleName, withdrawAddr, coins); err != nil {
					panic(err)
				}
			} else {
				feePool := h.k.GetFeePool(ctx)
				decCoins, err := sdk.NewDecCoinsFromCoins(coins...)
				if err != nil {
					panic(err)
				}
				feePool.CommunityPool = feePool.CommunityPool.Add(decCoins...)
				h.k.SetFeePool(ctx, feePool)
			}
```

**File:** sei-cosmos/x/distribution/keeper/hooks.go (L105-113)
```go
// withdraw delegation rewards (which also increments period)
func (h Hooks) BeforeDelegationSharesModified(ctx sdk.Context, delAddr sdk.AccAddress, valAddr sdk.ValAddress) {
	val := h.k.stakingKeeper.Validator(ctx, valAddr)
	del := h.k.stakingKeeper.Delegation(ctx, delAddr, valAddr)

	if _, err := h.k.withdrawDelegationRewards(ctx, val, del); err != nil {
		panic(err)
	}
}
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
