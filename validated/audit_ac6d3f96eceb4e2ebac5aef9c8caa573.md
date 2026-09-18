### Title
Tokenfactory denom admin can permanently freeze existing holders' balances via `MsgUpdateDenom` allow-list update - (File: x/tokenfactory/keeper/msg_server.go)

### Summary
A tokenfactory denom's admin (an ordinary transaction sender who created the denom, not a governance/validator role) can call `MsgUpdateDenom` to install or replace a `DenomAllowList` that excludes addresses that already hold that denom's tokens. Once excluded, those addresses can never send/transfer that balance again, permanently freezing their funds — the same "delist blocks withdrawal for existing holders" bug class as the reported JOJO issue, where a privileged flag change makes previously-acquired balances immovable.

### Finding Description
`UpdateDenom` lets the denom's `admin` (set via `ChangeAdmin`/`CreateDenom`, both reachable by any user) call `server.bankKeeper.SetDenomAllowList(ctx, denom, *msg.AllowList)` with no validation that current token holders remain in the list: [1](#0-0) 

`SetDenomAllowList` simply overwrites the allow list for the denom with no check against current balances: [2](#0-1) 

Once an allow list exists for a `factory/...` denom, `IsInDenomAllowList` rejects any address not in that list: [3](#0-2) 

This check is invoked by the bank keeper's send path (`BaseSendKeeper`/`BaseKeeper` send logic and `BankCoinTransferrer`), so a holder omitted from a newly-set allow list is blocked from transferring out of that denom via any standard `MsgSend`/`SendCoins` path, exactly mirroring `TestSendSenderNotInAllowList` and `TestSendReceiverNotInAllowList`, which show the same rejection behavior applied on both send and receive sides: [4](#0-3) [5](#0-4) 

There is no mechanism that grandfathers in existing balances when the allow list changes — the check is applied uniformly to the current holder set at send time, regardless of when the balance was acquired.

### Impact Explanation
Any tokenfactory denom admin can, after other addresses have legitimately acquired that denom (via mint, transfer, DEX trades, LP positions, etc.), issue an allow-list update that omits those addresses. This permanently locks their balance of that denom: they cannot send, swap, withdraw from any protocol holding the denom on their behalf, or otherwise move it, since every `SendCoins`/`InputOutputCoins` invocation for that denom now fails for excluded addresses. This is a permanent freezing of user funds, matching the "Accept only concrete fund loss or permanent freezing" criterion, reachable purely through a `MsgUpdateDenom` transaction from the denom admin — an ordinary account role, not governance or validator-only.

### Likelihood Explanation
Likelihood is high: tokenfactory denom admin is not gated behind governance and is trivially obtained by anyone who creates the denom (`MsgCreateDenom`), and `ChangeAdmin` can transfer that role. No special privileges beyond being the current denom admin are required, and the vulnerable call path (`MsgUpdateDenom` → `SetDenomAllowList`) has no safeguard preventing exclusion of existing balance holders.

### Recommendation
When updating a denom's allow list via `UpdateDenom`, validate that no address currently holding a non-zero balance of the denom is being removed from the allow list, or provide a way for holders to exit/burn their balance before enforcement, or emit a grace-period/migration mechanism so pre-existing balances remain transferable even if a subsequent allow list would otherwise reject the holder. Alternatively, restrict `IsInDenomAllowList` enforcement to only new balance acquisition (deposits) rather than blocking already-held balances from being spent by their owner.

### Proof of Concept
1. User A calls `MsgCreateDenom` to create `factory/A/foo` and becomes its admin. [6](#0-5) 
2. User A mints tokens and transfers some to User B (no allow list yet, so transfer succeeds).
3. User A calls `MsgUpdateDenom` with an `AllowList` containing only User A's address (excluding B), which succeeds via `SetDenomAllowList`. [7](#0-6) 
4. User B, who already holds `factory/A/foo` tokens, attempts `MsgSend` to move or withdraw the tokens. `IsInDenomAllowList` returns false for B, so the send fails with `"is not allowed to send funds"`, permanently freezing B's balance (verified by the existing test showing the exact rejection semantics): [8](#0-7)

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

**File:** sei-cosmos/x/bank/app_test.go (L217-252)
```go
}

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
