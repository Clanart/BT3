### Title
Tokenfactory denom admin can retroactively impose an allow-list to freeze existing token holders' balances - (File: x/tokenfactory/keeper/msg_server.go)

### Summary
`x/tokenfactory` lets any unprivileged account permissionlessly create a denom via `MsgCreateDenom`, which automatically makes the creator the denom's "admin" [1](#0-0) . That same admin can later call `MsgUpdateDenom` at any time to attach or replace the denom's `AllowList`, with the only check being that the sender matches the stored admin [2](#0-1) . This is directly analogous to the OpenQ finding: a single account that other, unrelated users have come to trust (here, third parties who received/hold the tokenfactory denom through swaps, transfers, EVM pointer wrapping, etc.) can unilaterally change a critical parameter after the fact — not payout token/volume, but the transferability rule for a token users already hold — with no consent, timelock, or on-chain commitment protecting those third parties.

### Finding Description
Once an `AllowList` is set for a denom, `BaseSendKeeper.IsInDenomAllowList` enforces that **both the sender and the receiver** of that denom must be in the allow list, otherwise the transfer is rejected [3](#0-2) . This check is applied in `MsgSend` and `MsgMultiSend` for both the sending and receiving address [4](#0-3) .

Critically, the admin does not need to set the allow list at denom-creation time — `MsgUpdateDenom` allows the admin to impose an allow list at any later point, after arbitrary third parties have already acquired and are holding balances of the denom (e.g., via DEX trades, LP pools, or receiving it as payment) [2](#0-1) . Because `IsInDenomAllowList` blocks both sending *and* receiving for non-listed addresses, an admin can retroactively exclude existing holders from the allow list, permanently locking their already-held balances: they can never move the tokens out again (blocked as sender) and no one else can help them by receiving from an alternate route since the same rule applies to any transfer of that denom. The `ChangeAdmin` message additionally allows the admin to hand off (or clear) admin rights afterward, meaning the freeze can be made irreversible since no future admin action could undo the allow list [5](#0-4) .

Unlike `CreateDenom`, the `validateUpdateDenom` path does not appear to prevent an admin from removing existing legitimate holders from a previously permissive (empty) allow list, nor does it require holder consent — the only checks performed are format checks and allow-list size validation [6](#0-5) .

### Impact Explanation
This meets the "permanent freezing of funds" bar: any tokenfactory denom holder who is not the admin can have their balance of that denom permanently locked at the admin's sole discretion, at any time after they've acquired the tokens, with no way to exit. Because tokenfactory denom creation is fully permissionless and reachable by any unprivileged transaction sender, and the denom can circulate to arbitrary third parties through normal bank transfers/DEX activity before the freeze is imposed, this is a concrete fund-freezing vector analogous to the cited report's "no trust in a supposedly-neutral construct" issue.

### Likelihood Explanation
Likelihood is high in the sense that this is trivially reachable: any account can create a denom (becoming its admin) and later call `MsgUpdateDenom` to set/replace the allow list. No special privileges, governance, or validator collusion are required — only a single `MsgUpdateDenom` transaction from the denom's current admin. The main constraint is that a victim must actually hold a balance of that specific tokenfactory denom, which occurs naturally through normal token circulation (transfers, DEX trades, being paid in the denom, etc.).

### Recommendation
Consider one or more of the following:
- Only allow narrowing/loosening the allow list in a way that never removes addresses that already hold a nonzero balance of the denom (i.e., grandfather existing holders), or require any allow-list update to still include (or auto-append) all current balance holders.
- Require a timelock/delay between an `MsgUpdateDenom` allow-list change and its enforcement, giving current holders a window to exit before new restrictions apply.
- Emit a distinct, clearly documented warning/event when an allow-list update would strip transfer rights from existing balance holders, and consider requiring governance or explicit opt-in from affected addresses for such downgrades.

### Proof of Concept
1. Attacker calls `MsgCreateDenom` to create `factory/{attacker}/coolcoin` with no allow list; attacker becomes admin (`x/tokenfactory/keeper/createdenom.go:40-46`).
2. Attacker mints/distributes `coolcoin` and it circulates to third parties (e.g., Alice) via ordinary `MsgSend`/DEX swaps — no allow list exists yet, so transfers succeed freely.
3. Once Alice holds a balance of `coolcoin`, the attacker submits `MsgUpdateDenom` with an `AllowList` that excludes Alice's address (`x/tokenfactory/keeper/msg_server.go:57-85`).
4. Alice attempts to send her `coolcoin` balance anywhere via `MsgSend`; `IsInDenomAllowList` rejects it because Alice is not in the allow list (`sei-cosmos/x/bank/keeper/send.go:501-524`, enforced in `sei-cosmos/x/bank/keeper/msg_server.go:26-49`), permanently freezing her funds.
5. Attacker optionally calls `MsgChangeAdmin` to relinquish admin rights (set admin to `""`), making the freeze unrecoverable by any account.

### Citations

**File:** x/tokenfactory/keeper/createdenom.go (L40-46)
```go
	authorityMetadata := types.DenomAuthorityMetadata{
		Admin: creatorAddr,
	}
	err = k.setAuthorityMetadata(ctx, denom, authorityMetadata)
	if err != nil {
		return err
	}
```

**File:** x/tokenfactory/keeper/createdenom.go (L72-89)
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

```

**File:** x/tokenfactory/keeper/msg_server.go (L57-85)
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

**File:** proto/tokenfactory/tx.proto (L69-79)
```text
// MsgChangeAdmin is the sdk.Msg type for allowing an admin account to reassign
// adminship of a denom to a new account
message MsgChangeAdmin {
  string sender = 1 [(gogoproto.moretags) = "yaml:\"sender\""];
  string denom = 2 [(gogoproto.moretags) = "yaml:\"denom\""];
  string new_admin = 3 [(gogoproto.moretags) = "yaml:\"new_admin\""];
}

// MsgChangeAdminResponse defines the response structure for an executed
// MsgChangeAdmin message.
message MsgChangeAdminResponse {}
```
