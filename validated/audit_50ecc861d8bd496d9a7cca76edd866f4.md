### Title
Tokenfactory denom admin can retroactively freeze existing holders' balances via `MsgUpdateDenom` allow-list changes - (File: `x/tokenfactory/keeper/msg_server.go`)

### Summary
The tokenfactory module lets the creator of a `factory/{creator}/{subdenom}` token act as its permanent "admin," analogous to the vault owner in the referenced Notional report. Just as a vault owner can flip an `ONLY_VAULT_EXIT`-style flag to block a user from exiting their own position, a tokenfactory denom admin can unilaterally call `MsgUpdateDenom` at any time — even long after tokens have been distributed to third parties — to install or modify a `bank.AllowList` for that denom. Any holder not on the list is immediately unable to send (or receive) the token, permanently freezing balances they already legitimately hold, with zero recourse for the affected holder.

### Finding Description
`CreateDenom` sets the message sender as permanent admin of the new denom via `types.DenomAuthorityMetadata{Admin: creatorAddr}` [1](#0-0) .

The admin can later call `UpdateDenom`, which only checks that `msg.Sender == authorityMetadata.GetAdmin()` before overwriting the denom's allow list with `server.bankKeeper.SetDenomAllowList(ctx, denom, *msg.AllowList)`: [2](#0-1) 

This is fundamentally different from setting an allow list at `CreateDenom` time, since `UpdateDenom` can be issued *after* the admin has already distributed/sold/transferred the token to other unprivileged holders, changing the rules on funds those holders already possess.

The bank module then enforces the allow list on every subsequent transfer: `IsInDenomAllowList` rejects sends where either the sender or receiver of a tokenfactory-prefixed coin is not present in the configured allow list, with no way for the excluded holder to override or bypass it: [3](#0-2) 

This is confirmed by existing unit tests showing sends are rejected with "is not allowed to send funds" / "is not allowed to receive funds" once an address is excluded from the allow list: [4](#0-3) [5](#0-4) 

Because the admin role is permissionless (any unprivileged account can call `MsgCreateDenom` and become admin of their own denom), and `MsgUpdateDenom` has no restriction preventing the admin from installing/mutating an allow list after tokens are already circulating among other users, the admin has outsized, unilateral control to lock existing holders' balances — mirroring the `ONLY_VAULT_EXIT` issue where the vault owner (an untrusted third party from the user's perspective) can prevent a user from moving funds they already hold.

### Impact Explanation
An unprivileged denom admin can permanently freeze other users' already-held token balances of that denom by submitting a single `MsgUpdateDenom` transaction with a restrictive allow list that omits target holders. Affected holders lose the ability to transfer/send that token entirely, with no way to regain access unless the admin voluntarily updates the allow list again. This satisfies the "permanent freezing of funds" impact bar, since the loss of transfer capability for held tokens is enforced unconditionally at the bank-module level for any address not on the list.

### Likelihood Explanation
Likelihood is high: creating a tokenfactory denom and calling `UpdateDenom` are both ordinary, permissionless transactions available to any account. No governance, validator, or operator privileges are required — only being the original creator/admin of a denom that has already been distributed to other users (e.g., via normal transfers, DEX trades, or airdrops), which is a common real-world pattern for tokenfactory-based tokens.

### Recommendation
Consider disallowing `MsgUpdateDenom` from setting/tightening an allow list once a denom has non-zero circulating supply held by addresses other than the admin, or require that any allow-list change only affect future mints rather than existing balances. Alternatively, require additional governance/multisig oversight or a time-locked/opt-in mechanism before an allow list can restrict addresses that already hold the denom, so a single admin transaction cannot retroactively freeze other users' funds.

### Proof of Concept
1. Attacker calls `MsgCreateDenom` to create `factory/{attacker}/mytoken`, with no allow list.
2. Attacker mints tokens and sends/sells them to victim addresses via normal `MsgSend`/DEX activity — this succeeds because `IsInDenomAllowList` returns `true` when no allow list exists [3](#0-2) .
3. Once victims hold balances, attacker (still the denom admin) calls `MsgUpdateDenom` with an `AllowList` containing only the attacker's own address [2](#0-1) .
4. Any subsequent `MsgSend` attempt by a victim holding `factory/{attacker}/mytoken` is now rejected by the bank keeper with "is not allowed to send funds", permanently freezing the victim's balance of that token [5](#0-4) .

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
