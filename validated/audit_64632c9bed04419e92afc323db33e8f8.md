### Title
TokenFactory `MsgUpdateDenom` allow-list removal permanently freezes an address's already-held denom balance - (File: `x/tokenfactory/keeper/msg_server.go`)

### Summary
The tokenfactory module lets a denom's admin (an ordinary tokenfactory denom creator, not a validator or governance actor) update a denom's `AllowList` at any time via `MsgUpdateDenom`, exactly analogous to the `UXDController.whitelistAsset()` entry point that toggles an asset's whitelist status. Just as unwhitelisting a UXD collateral asset blocked `_redeem()` for already-open PERP positions using it, removing an address from a tokenfactory denom's allow list blocks `MsgSend`/`MsgMultiSend` for that address's already-held balance of that denom, with no remaining code path in the bank module to move the tokens out.

### Finding Description
`MsgUpdateDenom` allows the denom admin to set an arbitrary new `AllowList` for the denom, and this is written directly to the bank keeper's per-denom allow list store: [1](#0-0) 

The allow-list is enforced only inside the `x/bank` `Msg` server handlers for `MsgSend` and `MsgMultiSend`: both the sender and the recipient must be present in the denom's allow list (unless the list is empty, in which case there is no restriction): [2](#0-1) 

The keeper primitive backing this is `IsInDenomAllowList`, which iterates a token-factory-prefixed coin's allow list and rejects the address if it is not a member: [3](#0-2) 

This mirrors the audit finding precisely: the "entry" check (who may send/receive the token) and the "exit" check are the *same* whitelist gate, and there is no separate/relaxed path preserved for addresses that already hold a balance before being removed from the list. In the UXD report, `whitelistAsset(asset, false)` blocked `_redeem()` for existing PERP collateral (`UXDController.sol` L316-318); here, `MsgUpdateDenom` with a narrower `AllowList` blocks `MsgSend`/`MsgMultiSend` for an address that already holds the factory denom (`bank/keeper/msg_server.go` L42-49).

Once an address is excluded from a denom's allow list:
- It cannot call `MsgSend` (sender check fails, `bank/keeper/msg_server.go` L43-45).
- It cannot be the recipient of a corrective transfer either (recipient check, L47-49), so it cannot even be temporarily whitelisted-then-swept by another party via ordinary bank messages if the admin instead chooses to exclude by omission.
- There is no `MsgBurn`-by-holder function that lets an arbitrary holder self-burn to escape the restriction; `MsgBurn` in the tokenfactory module only allows the denom admin to burn from the admin's own balance (`x/tokenfactory/keeper/msg_server.go`, `Burn`/`burnFrom` helpers in `x/tokenfactory/keeper/bankactions.go`), not to force-burn or rescue an excluded third party's balance.

Thus the excluded address's tokenfactory-denom balance becomes permanently unmovable through the standard bank message surface, the same "entry-blocking check also blocks exit" bug class described in the referenced report.

### Impact Explanation
Any user (not just a malicious or compromised admin) that is removed from a tokenfactory denom's allow list — whether by an honest compliance-driven admin action, an admin mistake, or an admin turning malicious after the address has already accumulated a balance — has their existing holdings of that denom permanently frozen with no path to redeem, transfer, or burn them via the public message surface. This is a concrete permanent freezing of funds, matching the accepted impact category ("permanent freezing" of funds), analogous to the PERP positions being frozen and forced toward liquidation in the source report.

### Likelihood Explanation
This requires the tokenfactory denom's admin (who is explicitly reachable and set purely by a `MsgCreateDenom`/`MsgChangeAdmin` transaction from any unprivileged sender — not gated by validator or governance permission) to call `MsgUpdateDenom` with a narrower allow list after users have already accumulated balances. This is a normal, expected, and permissionless usage path for compliance-oriented tokenfactory denoms with allow lists, making the likelihood plausible (not requiring a 51% attack, p2p compromise, or leaked validator key) — it only requires an honest-looking admin operation on a token whose holders already hold balances.

### Recommendation
Preserve an exit path for addresses that already hold a token-factory denom balance when they are removed from the allow list, mirroring the audit's fix pattern (removing the whitelist gate from the redeem/exit path while keeping it on the mint/entry path). Concretely:
- Allow a previously-included holder to still burn/redeem their own balance of a denom even if later excluded from the allow list, or
- Grandfather in addresses that already hold a nonzero balance at the time the allow list is narrowed, or
- Provide a dedicated escape/burn message that bypasses the allow-list send/receive check for self-initiated burns.

### Proof of Concept
1. Any account `A` creates a tokenfactory denom `factory/A/COIN` via `MsgCreateDenom` with an allow list containing addresses `{A, B}` (`x/tokenfactory/keeper/msg_server.go` `CreateDenom`, `x/tokenfactory/keeper/createdenom_test.go` shows this flow).
2. `A` mints and sends some `factory/A/COIN` to `B` via `MsgMint`/`MsgSend`; `B` now holds a balance (allowed at the time, per `bank/keeper/msg_server.go` L42-49 and `IsInDenomAllowList`).
3. `A` (the denom admin) calls `MsgUpdateDenom` on `factory/A/COIN` with a new `AllowList{Addresses: [A]}`, excluding `B` (`x/tokenfactory/keeper/msg_server.go` `UpdateDenom`, L57-92).
4. `B` now attempts `MsgSend` to move its existing `factory/A/COIN` balance to any other address — the call fails with `ErrUnauthorized: "%s is not allowed to send funds"` because `IsInDenomAllowList` rejects `B` as sender (`bank/keeper/msg_server.go` L42-45).
5. `B` has no other bank message to move, burn, or otherwise redeem this balance; the tokens are permanently stuck in `B`'s account, matching the "frozen position with no exit" impact from the source report.

### Citations

**File:** x/tokenfactory/keeper/msg_server.go (L57-92)
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

**File:** sei-cosmos/x/bank/keeper/send.go (L479-524)
```go
	store := ctx.KVStore(k.storeKey)
	denomAllowListStore := prefix.NewStore(store, types.DenomAllowListKey(denom))

	m := k.cdc.MustMarshal(&allowList)
	denomAllowListStore.Set([]byte(denom), m)
}

func (k BaseSendKeeper) GetDenomAllowList(ctx sdk.Context, denom string) types.AllowList {
	store := ctx.KVStore(k.storeKey)
	store = prefix.NewStore(store, types.DenomAllowListKey(denom))

	bz := store.Get([]byte(denom))
	if bz == nil {
		return types.AllowList{}
	}

	var allowList types.AllowList
	k.cdc.MustUnmarshal(bz, &allowList)

	return allowList
}

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
