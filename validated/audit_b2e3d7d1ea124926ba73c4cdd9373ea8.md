### Title
Tokenfactory `UpdateDenom` Allow-List Changes Permanently Freeze Existing Token Holders' Balances - (File: x/tokenfactory/keeper/msg_server.go)

### Summary
The tokenfactory module lets a denom's admin call `MsgUpdateDenom` to set or replace a denom's `AllowList` at any time, with no check of who currently holds a balance of that denom. Once an address is excluded from the allow list, `bank.IsInDenomAllowList` blocks it from sending (and, if excluded on the receive side, from receiving) that denom via `MsgSend`/`MsgMultiSend` and CosmWasm bank transfers. Because the admin can also permanently renounce the admin role via `MsgChangeAdmin` (setting the new admin to `""`), the restrictive allow list can never be corrected again, permanently freezing the balances of any holder who was excluded — the same root-cause pattern as the `removePool()` bug: a privileged party mutates access-control/registry state that gates fund movement without verifying or preserving the standing of existing balance holders, and with no recovery path once the privileged actor is gone.

### Finding Description
`MsgUpdateDenom` allows the current denom admin to set an arbitrary `AllowList` for any tokenfactory denom: [1](#0-0) 

The allow list is enforced by `IsInDenomAllowList`, which is checked before both send and receive of any `factory/...` denom in `bank.MsgSend`/`MsgMultiSend`: [2](#0-1) 

and in the wasm bank bridge used by CosmWasm contracts: [3](#0-2) 

`SetDenomAllowList`/`IsInDenomAllowList` do not check whether addresses currently holding a nonzero balance of the denom are being excluded from the new list — analogous to `removePool()` deleting a pool's active state without checking `userBalances`: [4](#0-3) 

The tokenfactory module documents that an admin can set the admin to `""`, permanently removing anyone's ability to fix a bad allow list: [5](#0-4) 

and `ChangeAdmin` accepts this directly with no restriction against renouncing to empty: [6](#0-5) 

The combination — (1) admin sets an `AllowList` that excludes existing token holders, (2) admin renounces the admin role — makes the freeze permanent and unrecoverable via the tokenfactory admin path, exactly like `removePool()`'s lack of a recovery path for balances of a removed pool.

### Impact Explanation
Any tokenfactory denom is permissionlessly creatable by ordinary users (`MsgCreateDenom`), and a denom's admin is a normal, unprivileged account (the creator by default, or whoever it's transferred to). If that admin sets or updates the `AllowList` (deliberately or accidentally) to exclude addresses that already hold balances of the denom, those addresses immediately lose the ability to send that denom via `MsgSend`/`MsgMultiSend` or wasm-triggered transfers. If the admin subsequently renounces admin rights (a supported, first-class operation), the freeze becomes permanent — there is no governance or protocol-level rescue mechanism to restore access for the frozen holders. This is a concrete, permanent loss of fund mobility for affected users, matching the "permanent freezing" impact class.

### Likelihood Explanation
Tokenfactory denom creation and admin management are fully permissionless, unprivileged-user-reachable flows (`MsgCreateDenom`, `MsgUpdateDenom`, `MsgChangeAdmin` are ordinary transactions). No governance or validator involvement is required to trigger the freeze — a single admin account for a single denom, acting alone (maliciously or by mistake), can lock out any subset of that denom's holders and then discard admin control. Given how common it is for token issuers to set allow lists (e.g., for KYC/compliance use-cases) and then rotate/renounce admin authority, this is a realistic and easily reachable scenario.

### Recommendation
- When updating a denom's `AllowList` via `MsgUpdateDenom`, either reject allow lists that would exclude addresses currently holding a nonzero balance of the denom, or automatically retain such holders on the list.
- Alternatively/additionally, provide an escape hatch: allow addresses to redeem/burn or otherwise recover balances of a denom they hold even when they are not on the denom's allow list, so an admin misconfiguration (or malicious/renounced admin) cannot permanently trap funds.
- Consider disallowing `ChangeAdmin` to `""` while an active, exclusionary `AllowList` exists, to avoid making a bad allow-list state irreversible.

### Proof of Concept
1. Account A calls `MsgCreateDenom` to create `factory/A/token`, becoming its admin.
2. Account A mints `factory/A/token` and sends some to Account B via `MsgSend` (allowed, since no allow list exists yet).
3. Account A calls `MsgUpdateDenom` with `AllowList = {A}` (excluding B), per `UpdateDenom` in `x/tokenfactory/keeper/msg_server.go`.
4. Account B attempts `MsgSend` of `factory/A/token`; the transaction fails with `"B is not allowed to send funds"` because `IsInDenomAllowList` in `sei-cosmos/x/bank/keeper/msg_server.go` rejects it.
5. Account A calls `MsgChangeAdmin` setting `NewAdmin = ""`, permanently renouncing admin control (a supported action per `x/tokenfactory/README.md`).
6. Account B's balance of `factory/A/token` can never be moved via `MsgSend`/`MsgMultiSend`/wasm transfers again — the funds are permanently frozen with no recovery path, mirroring the `removePool()` finding's root cause and impact.

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

**File:** x/tokenfactory/keeper/msg_server.go (L156-186)
```go
func (server msgServer) ChangeAdmin(goCtx context.Context, msg *types.MsgChangeAdmin) (*types.MsgChangeAdminResponse, error) {
	ctx := sdk.UnwrapSDKContext(goCtx)

	authorityMetadata, err := server.GetAuthorityMetadata(ctx, msg.Denom)
	if err != nil {
		return nil, err
	}

	if msg.Sender != authorityMetadata.GetAdmin() {
		return nil, types.ErrUnauthorized
	}

	// Validate new admin we change to should be different from current admin
	if msg.NewAdmin == authorityMetadata.GetAdmin() {
		return nil, types.ErrAdminAlreadyExists
	}

	err = server.setAdmin(ctx, msg.Denom, msg.NewAdmin)
	if err != nil {
		return nil, err
	}
	ctx.EventManager().EmitEvents(sdk.Events{
		sdk.NewEvent(
			types.TypeMsgChangeAdmin,
			sdk.NewAttribute(types.AttributeDenom, msg.GetDenom()),
			sdk.NewAttribute(types.AttributeNewAdmin, msg.NewAdmin),
		),
	})

	return &types.MsgChangeAdminResponse{}, nil
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

**File:** sei-wasmd/x/wasm/keeper/keeper.go (L1198-1213)
```go
// TransferCoins transfers coins from source to destination account when coin send was enabled for them and the recipient
// is not in the blocked address list.
func (c BankCoinTransferrer) TransferCoins(parentCtx sdk.Context, fromAddr sdk.AccAddress, toAddr sdk.AccAddress, amount sdk.Coins) error {
	em := sdk.NewEventManager()
	ctx := parentCtx.WithEventManager(em)
	if err := c.keeper.IsSendEnabledCoins(ctx, amount...); err != nil {
		return err
	}
	if c.keeper.BlockedAddr(toAddr) {
		return sdkerrors.Wrapf(sdkerrors.ErrUnauthorized, "%s is not allowed to receive funds", toAddr.String())
	}

	sdkerr := c.keeper.SendCoins(ctx, fromAddr, toAddr, amount)
	if sdkerr != nil {
		return sdkerr
	}
```

**File:** sei-cosmos/x/bank/keeper/send.go (L478-524)
```go
func (k BaseSendKeeper) SetDenomAllowList(ctx sdk.Context, denom string, allowList types.AllowList) {
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

**File:** x/tokenfactory/README.md (L87-97)
```markdown
### ChangeAdmin

Change the admin of a denom. Note, this is only allowed to be called by the current admin of the denom.

```protobuf
message MsgChangeAdmin {
  string sender = 1 [ (gogoproto.moretags) = "yaml:\"sender\"" ];
  string denom = 2 [ (gogoproto.moretags) = "yaml:\"denom\"" ];
  string newAdmin = 3 [ (gogoproto.moretags) = "yaml:\"new_admin\"" ];
}
```
```
