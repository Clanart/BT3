### Title
Tokenfactory denom admin can retroactively lock existing holders' funds by setting an AllowList via `MsgUpdateDenom` - (File: x/tokenfactory/keeper/msg_server.go)

### Summary
The tokenfactory module lets a permissionless, self-appointed denom admin call `MsgUpdateDenom` at any time to attach or replace a denom's `AllowList` in the bank module, with no timelock, no grandfathering, and no check against existing token holders. This mirrors the reported UXDController pattern where a privileged (but not necessarily trusted-by-users) role instantly changes an asset's eligibility list, stranding funds that users already hold/deposited under the old rules.

### Finding Description
`CreateDenom` lets any account become the "admin" of a `factory/{creator}/{subdenom}` token, which can then be freely minted, transferred, and held by third parties. The same admin can later call `UpdateDenom`, which — after only an authority check — immediately calls `bankKeeper.SetDenomAllowList(ctx, denom, *msg.AllowList)` with no timelock or restriction: [1](#0-0) 

This allow list is enforced on every `bank.Send`/`MsgSend` for that denom via `IsInDenomAllowList`, which requires *both* sender and recipient to appear in the list (if one is configured) or the transfer is rejected outright: [2](#0-1) [3](#0-2) 

So an admin can: (1) create a denom with no allow list, (2) let third parties acquire/hold/deposit the token elsewhere (e.g. into a DEX, vault, or lending pool as collateral, analogous to `UXDController`'s "collateral asset"), and (3) at any later block call `UpdateDenom` with an `AllowList` that omits those holders' addresses. From that point on, those holders (and any downstream contract module account holding the token, e.g. a vault module account) can no longer send the tokens they already hold, because `IsInDenomAllowList` returns `false` for both send and receive checks. There is no cooldown, no migration path, and no on-chain warning before the change takes effect — the admin can even front-run/interleave this with a victim's pending withdrawal transaction in the same block, exactly as described in the analog report ("Owner can revert user calls by frontrunning").

### Impact Explanation
Funds already held in accounts (or in third-party contracts/pools that accepted the denom as collateral) can become permanently non-transferable the moment the admin updates the allow list, since there is no way for an excluded holder to regain send/receive rights except by convincing the admin to re-include them. This is a direct, admin-triggerable freezing of user funds reachable purely through a standard `MsgUpdateDenom` transaction — no governance, no validator collusion, no node/peer trust needed.

### Likelihood Explanation
Likelihood is high in practice: tokenfactory denom creation is fully permissionless, so any account can become an "admin," distribute or list a token, and later restrict the allow list, whether maliciously or through misconfiguration/mistake. The action requires only a single transaction (`MsgUpdateDenom`) by the party who already holds admin rights over the denom — this is analogous to the referenced report's "Owner" role, which is a privileged application-level actor, not a protocol validator/node.

### Recommendation
Add safeguards before an `AllowList` update takes effect, e.g.:
- Enforce a timelock/delay before a new `MsgUpdateDenom` allow-list change becomes active, giving current holders a window to exit.
- Grandfather addresses that already hold a non-zero balance of the denom at the time of the update (auto-include them in the new allow list, or allow them at least to transfer out to any address once).
- Emit a clear, queryable "pending allow list change" state so downstream integrators (DEXs, vaults) can react before the change is committed.

### Proof of Concept
1. Attacker/admin calls `MsgCreateDenom` (no allow list) to create `factory/admin/foo`, and users acquire/hold/deposit `factory/admin/foo` in their own wallets or in an external contract vault. [4](#0-3) 
2. Once user funds are deposited/held, the admin submits `MsgUpdateDenom` with an `AllowList` that excludes the victim addresses (or excludes a vault's module account address): [5](#0-4) 
3. Any subsequent `MsgSend` (or internal `SendCoins` call) from an excluded holder for that denom now fails with `"... is not allowed to send/receive funds"`, permanently locking the victim's balance in place: [6](#0-5) 
This is confirmed by the existing unit test pattern showing a `MsgSend` reverting once a `DenomAllowList` excludes the recipient, demonstrating the exact freezing mechanism: [7](#0-6)

### Citations

**File:** x/tokenfactory/keeper/msg_server.go (L23-55)
```go
func (server msgServer) CreateDenom(goCtx context.Context, msg *types.MsgCreateDenom) (*types.MsgCreateDenomResponse, error) {
	ctx := sdk.UnwrapSDKContext(goCtx)

	denom, err := server.Keeper.CreateDenom(ctx, msg.Sender, msg.Subdenom)
	if err != nil {
		return nil, err
	}

	createDenomEvent := sdk.NewEvent(
		types.TypeMsgCreateDenom,
		sdk.NewAttribute(types.AttributeCreator, msg.Sender),
		sdk.NewAttribute(types.AttributeNewTokenDenom, denom),
	)

	if msg.AllowList != nil {
		err = server.validateAllowList(ctx, msg.AllowList)
		if err != nil {
			return nil, err
		}
		server.bankKeeper.SetDenomAllowList(ctx, denom, *msg.AllowList)
		createDenomEvent = createDenomEvent.AppendAttributes(
			sdk.NewAttribute(types.AttributeAllowList, strings.Join(msg.AllowList.Addresses, ",")),
		)
	}

	ctx.EventManager().EmitEvents(sdk.Events{
		createDenomEvent,
	})

	return &types.MsgCreateDenomResponse{
		NewTokenDenom: denom,
	}, nil
}
```

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

**File:** sei-cosmos/x/bank/app_test.go (L117-149)
```go
func TestSendReceiverNotInAllowList(t *testing.T) {
	acc := &authtypes.BaseAccount{
		Address: addr1.String(),
	}

	genAccs := []authtypes.GenesisAccount{acc}
	a := app.SetupWithGenesisAccounts(t, genAccs)
	ctx := a.BaseApp.NewContext(false, tmproto.Header{})
	testDenom := "testDenom"
	factoryDenom := fmt.Sprintf("factory/%s/%s", addr1.String(), testDenom)

	require.NoError(t, apptesting.FundAccount(a.BankKeeper, ctx, addr1, sdk.NewCoins(sdk.NewInt64Coin(factoryDenom, 100))))
	a.BankKeeper.SetDenomAllowList(ctx, factoryDenom,
		types.AllowList{Addresses: []string{addr1.String()}})

	a.Commit(context.Background())

	res1 := a.AccountKeeper.GetAccount(ctx, addr1)
	require.NotNil(t, res1)
	require.Equal(t, acc, res1.(*authtypes.BaseAccount))

	origAccNum := res1.GetAccountNumber()
	origSeq := res1.GetSequence()

	sendMsg := types.NewMsgSend(addr1, addr2, sdk.Coins{sdk.NewInt64Coin(factoryDenom, 10)})
	header := tmproto.Header{ChainID: a.ChainID, Height: a.LastBlockHeight() + 1}
	txGen := app.MakeEncodingConfig().TxConfig
	_, _, err := app.SignCheckDeliver(t, txGen, a.BaseApp, header, []sdk.Msg{sendMsg}, []uint64{origAccNum}, []uint64{origSeq}, false, false, priv1)
	require.Error(t, err)
	require.Contains(t, err.Error(), fmt.Sprintf("%s is not allowed to receive funds", addr2))

	app.CheckBalance(t, a, addr1, sdk.Coins{sdk.NewInt64Coin(factoryDenom, 100)})
}
```
