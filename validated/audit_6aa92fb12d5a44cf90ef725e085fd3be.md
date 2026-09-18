### Title
Tokenfactory denom admin can update an `AllowList` to permanently freeze user funds already held in that denom - (File: `x/tokenfactory/keeper/msg_server.go`)

### Summary
The tokenfactory module lets a denom's admin call `MsgUpdateDenom` at any time to replace a denom's bank `AllowList` with an arbitrary new one. The bank keeper's `Send`/`MultiSend` handlers gate every transfer of that denom on `IsInDenomAllowList`, checking both the sender and receiver against the *current* allow list. There is no mechanism to let holders who are excluded from a newly-set allow list ever move or exit the tokens they already legitimately hold, mirroring the UXDController "whitelist removed after mint" bug class: an address can legitimately acquire tokenfactory-denom tokens while allowed, then be excluded from the allow list on a later `MsgUpdateDenom`, permanently freezing its balance.

### Finding Description
`MsgCreateDenom`/`MsgUpdateDenom` allow a denom admin to set/replace a bank-level `AllowList` for a tokenfactory denom via `bankKeeper.SetDenomAllowList`: [1](#0-0) 

`SetDenomAllowList` simply overwrites the previous allow list wholesale — there is no append-only or "cannot remove already-holding address" restriction: [2](#0-1) 

Every `MsgSend` and `MsgMultiSend` for that denom is then gated by `IsInDenomAllowList`, which checks the *current* allow list for both sender and recipient: [3](#0-2) [4](#0-3) 

Attack/failure scenario:
1. Admin creates denom `factory/{admin}/XYZ` with an allow list containing Alice, or with no allow list (unrestricted).
2. Alice legitimately acquires (via mint or transfer) some amount of `factory/{admin}/XYZ`.
3. Admin calls `MsgUpdateDenom` with a new `AllowList` that no longer includes Alice's address (this is entirely valid per `validateUpdateDenom`/`validateAllowList`, which only checks size/bech32-validity, not whether it excludes existing holders): [5](#0-4) 
4. Alice's `MsgSend` of her own balance is now rejected with `"is not allowed to send funds"` by `IsInDenomAllowList`, and she cannot receive further transfers of that denom either. Her balance is stuck permanently unless the admin re-adds her — an action fully outside her control.

This is directly analogous to the reported UXDController issue: a token holder's ability to move/redeem an asset they already legitimately hold is retroactively revoked by a privileged party's whitelist update, with no fallback path for already-held balances.

### Impact Explanation
Any tokenfactory denom (which any account can permissionlessly create) can be weaponized by its admin — intentionally or accidentally — to freeze user funds by simply excluding a previously-allowed holder in a subsequent `MsgUpdateDenom`. Because the check applies to sender AND receiver, an excluded holder cannot transfer out, and no other address can be forced onto them either. This constitutes permanent freezing of user funds (a fund-loss/availability impact meeting High severity), reachable purely through a standard `MsgUpdateDenom` transaction from the module's own designated admin — a normal, permissionless, unprivileged-at-the-chain-level actor (denom admin is not a chain validator/governance role).

### Likelihood Explanation
Likelihood is high: any tokenfactory denom admin (a role obtainable by simply calling `MsgCreateDenom`, which anyone can do) can trigger this at will with a single `MsgUpdateDenom` transaction. No governance, no special permission, no race condition needed — just normal admin authority over their own denom, exercised after users have already accumulated balances.

### Recommendation
When processing `MsgUpdateDenom`, diff the new allow list against existing balances of the denom (or against the previous allow list) and either: (a) disallow removing addresses that currently hold a nonzero balance of the denom, or (b) always permit already-held balances to be sent to/burned regardless of allow-list membership (e.g., special-case "sending your existing balance out" separately from "receiving new balance"), so an allow-list update cannot retroactively strand funds a user acquired while previously permitted.

### Proof of Concept
1. Alice's account is included in the allow list (or the denom has no allow list) for `factory/admin/XYZ`.
2. Admin mints/sends tokens to Alice; Alice now holds `N` `factory/admin/XYZ`.
3. Admin submits `MsgUpdateDenom{Denom: "factory/admin/XYZ", AllowList: {Addresses: [admin_only]}}` — succeeds per `validateUpdateDenom` (`x/tokenfactory/keeper/createdenom.go:72-88`) since it only validates size/bech32 format, not exclusion of holders.
4. Alice submits `MsgSend{FromAddress: alice, ToAddress: bob, Amount: N factory/admin/XYZ}`.
5. `bank.msgServer.Send` calls `IsInDenomAllowList(ctx, alice, ...)` → false → returns `ErrUnauthorized: "alice is not allowed to send funds"` (`sei-cosmos/x/bank/keeper/msg_server.go:42-45`).
6. Alice's `N` tokens are permanently stuck; she has no path to move or exit them.

### Citations

**File:** x/tokenfactory/keeper/msg_server.go (L57-91)
```go
func (server msgServer) UpdateDenom(goCtx context.Context, msg *types.MsgUpdateDenom) (*types.MsgUpdateDenomResponse, error) {
	ctx := sdk.UnwrapSDKContext(goCtx)

	denom, err := server.validateUpdateDenom(ctx, msg)
	if err != nil {
		return nil, err
	}

	authorityMetadata, err := server.GetAuthorityMetadata(ctx, denom)
	if err != nil {
		return nil, err
	}

	if msg.Sender != authorityMetadata.GetAdmin() {
		return nil, types.ErrUnauthorized
	}

	updateDenomEvent := sdk.NewEvent(
		types.TypeMsgUpdateDenom,
		sdk.NewAttribute(types.AttributeCreator, msg.Sender),
		sdk.NewAttribute(types.AttributeUpdatedTokenDenom, denom),
	)

	if msg.AllowList != nil {
		server.bankKeeper.SetDenomAllowList(ctx, denom, *msg.AllowList)
		updateDenomEvent = updateDenomEvent.AppendAttributes(
			sdk.NewAttribute(types.AttributeAllowList, strings.Join(msg.AllowList.Addresses, ",")),
		)
	}

	ctx.EventManager().EmitEvents(sdk.Events{
		updateDenomEvent,
	})

	return &types.MsgUpdateDenomResponse{}, nil
```

**File:** sei-cosmos/x/bank/keeper/send.go (L478-484)
```go
func (k BaseSendKeeper) SetDenomAllowList(ctx sdk.Context, denom string, allowList types.AllowList) {
	store := ctx.KVStore(k.storeKey)
	denomAllowListStore := prefix.NewStore(store, types.DenomAllowListKey(denom))

	m := k.cdc.MustMarshal(&allowList)
	denomAllowListStore.Set([]byte(denom), m)
}
```

**File:** sei-cosmos/x/bank/keeper/send.go (L501-524)
```go
// IsInDenomAllowList checks if the given address is allowed to send the given coins.
// The check is performed only fot token factory denoms. For each token factory denom,
// it checks if there is allow list for the given denom. If there is no allow list,
// the address is allowed to send the coins. If there is an allow list, the address is
// allowed to send the coins only if it is in the allow list.
func (k BaseSendKeeper) IsInDenomAllowList(ctx sdk.Context, addr sdk.AccAddress, coins sdk.Coins, cache map[string]AllowedAddresses) bool {
	for _, coin := range coins {
		// Skip if denom does not contain the token factory prefix
		if !strings.HasPrefix(coin.Denom, TokenFactoryPrefix) {
			continue
		}

		allowedAddresses := k.getAllowedAddresses(ctx, cache, coin.Denom)
		// skip if there is no allow list for the denom
		if len(allowedAddresses.set) == 0 {
			continue
		}

		if !allowedAddresses.contains(addr) {
			return false
		}
	}
	return true
}
```

**File:** sei-cosmos/x/bank/keeper/msg_server.go (L26-49)
```go
func (k msgServer) Send(goCtx context.Context, msg *types.MsgSend) (*types.MsgSendResponse, error) {
	ctx := sdk.UnwrapSDKContext(goCtx)

	if err := k.IsSendEnabledCoins(ctx, msg.Amount...); err != nil {
		return nil, err
	}

	from, err := sdk.AccAddressFromBech32(msg.FromAddress)
	if err != nil {
		return nil, err
	}
	to, err := sdk.AccAddressFromBech32(msg.ToAddress)
	if err != nil {
		return nil, err
	}

	allowListCache := make(map[string]AllowedAddresses)
	if !k.IsInDenomAllowList(ctx, from, msg.Amount, allowListCache) {
		return nil, sdkerrors.Wrapf(sdkerrors.ErrUnauthorized, "%s is not allowed to send funds", msg.FromAddress)
	}

	if k.BlockedAddr(to) || !k.IsInDenomAllowList(ctx, to, msg.Amount, allowListCache) {
		return nil, sdkerrors.Wrapf(sdkerrors.ErrUnauthorized, "%s is not allowed to receive funds", msg.ToAddress)
	}
```

**File:** x/tokenfactory/keeper/createdenom.go (L72-114)
```go
func (k Keeper) validateUpdateDenom(ctx sdk.Context, msg *types.MsgUpdateDenom) (tokenDenom string, err error) {
	_, _, err = types.DeconstructDenom(msg.GetDenom())
	if err != nil {
		return "", err
	}
	_, found := k.bankKeeper.GetDenomMetaData(ctx, msg.GetDenom())
	if !found {
		return "", types.ErrDenomDoesNotExist.Wrapf("denom: %s", msg.GetDenom())
	}

	err = k.validateAllowList(ctx, msg.AllowList)
	if err != nil {
		return "", err
	}

	return msg.GetDenom(), nil
}

func (k Keeper) validateAllowListSize(ctx sdk.Context, allowList *banktypes.AllowList) error {
	if allowList == nil {
		return types.ErrAllowListUndefined
	}

	if len(allowList.Addresses) > int(k.GetDenomAllowListMaxSize(ctx)) {
		return types.ErrAllowListTooLarge
	}
	return nil
}

func (k Keeper) validateAllowList(ctx sdk.Context, allowList *banktypes.AllowList) error {
	err := k.validateAllowListSize(ctx, allowList)
	if err != nil {
		return err
	}

	// validate all addresses in the allow list are bech32
	for _, addr := range allowList.Addresses {
		if _, err = sdk.AccAddressFromBech32(addr); err != nil {
			return fmt.Errorf("invalid address %s: %w", addr, err)
		}
	}
	return nil
}
```
