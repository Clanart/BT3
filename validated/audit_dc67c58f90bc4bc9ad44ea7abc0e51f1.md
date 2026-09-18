### Title
Tokenfactory denom admin can retroactively set an AllowList that permanently freezes existing token holders' funds - (File: `sei-cosmos/x/bank/keeper/msg_server.go`)

### Summary
This is a valid analog of the reported bug class. In the InitCapital report, a governor could blacklist an already-deposited collateral token (wLP), permanently preventing the depositor from withdrawing/transferring it even though they held it before the blacklist was applied. The equivalent mechanism in sei-chain is the tokenfactory denom `AllowList`: a denom admin can call `MsgUpdateDenom` at any time to set an `AllowList` on a denom that users already hold, and the bank module's `Send`/`MultiSend` handlers will then reject transfers to/from any address not in that list, permanently freezing existing balances.

### Finding Description
When a user holds a token-factory denom (`factory/{creator}/{subdenom}`), the tokenfactory admin can call `MsgUpdateDenom` to set/replace the `AllowList` on the denom at any time via `server.bankKeeper.SetDenomAllowList(ctx, denom, *msg.AllowList)` [1](#0-0) . There is no requirement that existing holders be included, nor any check that holders can still move out balances they already hold.

The bank module enforces this allow list at `MsgSend`/`MsgMultiSend` time via `IsInDenomAllowList`, which rejects the transaction if the sender or receiver address is not present in the allow list for that denom: [2](#0-1) [3](#0-2) 

Because the allow list can be set to any arbitrary address set at any point in time — after users have already acquired/held the denom — a legitimate existing holder who is not included in a newly-set allow list becomes permanently unable to send that denom out of their account (or to any address not on the list), exactly mirroring the InitCapital wLP scenario where a governor's post-hoc blacklist locks a user's already-deposited collateral.

### Impact Explanation
An unprivileged tokenfactory denom creator/admin (reachable via a normal `MsgCreateDenom`/`MsgUpdateDenom` transaction, no chain governance required) can permanently freeze the funds of any holder of their factory denom by setting an `AllowList` that excludes that holder's address. This meets the "permanent freezing of funds" bar: affected holders cannot execute `MsgSend`/`MsgMultiSend` for that denom, and there is no override or health-check-style escape hatch (unlike the sponsor's proposed decay-then-blacklist mitigation in the original report, here the switch is instantaneous and unconditional).

### Likelihood Explanation
High likelihood of reachability: any account can call `MsgCreateDenom` to become a tokenfactory admin, distribute/sell the denom to other users, then call `MsgUpdateDenom` to set a restrictive `AllowList`. This is a straightforward, permissionless griefing/rug vector requiring only two transactions from the denom admin, using functionality that is exposed to any unprivileged transaction sender via the tokenfactory module.

### Recommendation
Consider grandfathering existing balances or providing an "exit" path when a denom allow list transitions from unset/permissive to restrictive — e.g., allow addresses that already held a positive balance at the time the allow list changed to still send (but not necessarily receive) that balance out, similar to the InitCapital recommendation to still allow withdrawal even while the asset is blacklisted for new deposits. Alternatively, document/restrict `MsgUpdateDenom`'s ability to alter the allow list for denoms with existing non-empty circulating supply, or require the new allow list to be a superset of current addresses holding nonzero balance.

### Proof of Concept
1. Attacker calls `MsgCreateDenom` to create `factory/{attacker}/mytoken` with no allow list.
2. Attacker mints tokens and distributes/sells them to victim addresses via normal `MsgSend`/`MsgMultiSend` (unrestricted, since no allow list exists yet) — see `IsInDenomAllowList` short-circuiting when the list is empty [4](#0-3) .
3. Attacker calls `MsgUpdateDenom` with an `AllowList` containing only the attacker's own address [5](#0-4) .
4. Victim now attempts `MsgSend` of their held `factory/{attacker}/mytoken` balance; the transaction fails with `"%s is not allowed to send funds"` because the victim's address is not in the allow list [6](#0-5) .
5. The victim's balance of that denom is now permanently frozen with no way to transfer it out, confirmed by existing unit tests demonstrating this exact rejection behavior [7](#0-6) .

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

**File:** sei-cosmos/x/bank/app_test.go (L219-251)
```go
func TestSendSenderNotInAllowList(t *testing.T) {
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
		types.AllowList{Addresses: []string{addr2.String()}})

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
	require.Contains(t, err.Error(), fmt.Sprintf("%s is not allowed to send funds", addr1))

	app.CheckBalance(t, a, addr1, sdk.Coins{sdk.NewInt64Coin(factoryDenom, 100)})
}
```
