The `x/tokenfactory` denom-admin allow-list is the closest structural analog to the report's `allowedAsset`/`allowedCollection` pattern (an admin-controlled address list, changed atomically with no delay, that gates whether other users' transfers succeed), reachable via `MsgUpdateDenom` → `UpdateDenom` → `bankKeeper.SetDenomAllowList`, enforced synchronously in `IsInDenomAllowList` during every send.### Title
Token factory denom admin can instantly rewrite the `DenomAllowList` with no timelock, immediately freezing other holders' transfers - (File: `sei-cosmos/x/bank/keeper/msg_server.go`, `x/tokenfactory/keeper/msg_server.go`)

### Summary
The `x/tokenfactory` module lets a denom's admin call `MsgUpdateDenom` to atomically replace the bank module's per-denom `AllowList`, and that new list is enforced immediately and unconditionally on every subsequent send of the denom. There is no timelock, delay, or notice period between the admin's decision and its enforcement, mirroring the `allowedAsset`/`allowedCollection` centralization issue in the original report: a single admin-controlled allowlist gates whether other users' transactions succeed, and it can be edited at will to instantly deny specific addresses.

### Finding Description
`MsgUpdateDenom` is processed by `UpdateDenom` in the tokenfactory keeper, which checks only that `msg.Sender` equals the denom's admin and then calls `server.bankKeeper.SetDenomAllowList(ctx, denom, *msg.AllowList)` directly, with no staging/pending state or delay: [1](#0-0) 

The allowlist is enforced synchronously for every coin transfer of that denom via `IsInDenomAllowList`, which is checked at send time in the bank keeper - so a change to the allowlist takes effect on the very next transaction, with no grace period for in-flight transfers or affected holders: [2](#0-1) 

This is confirmed by the bank module's own integration test, which shows that once the admin sets an allowlist that excludes an address, that address's `MsgSend` is rejected immediately even though it already holds a funded balance of the denom: [3](#0-2) 

### Impact Explanation
A denom admin (the `tokenfactory` denom creator, or whoever `MsgChangeAdmin` transfers admin rights to) can, with a single transaction, instantly and retroactively deny any specific holder — including holders who legitimately acquired the token before the change — the ability to move their balance of that denom. Because there is no timelock, holders have no warning and no opportunity to react (e.g., withdraw, exit positions, or complete a pending swap) before the allowlist change takes effect on the very next block. A malicious or compromised denom admin can use this to permanently freeze funds of arbitrary counterparties (e.g., mid-trade or mid-liquidity-provision), which matches the "permanent freezing of funds" impact bar.

### Likelihood Explanation
This is reachable by any tokenfactory denom admin (a normal, unprivileged transaction sender with authority only over their own created denom) issuing a single `MsgUpdateDenom` message. No governance, validator, or protocol upgrade is required — it is a standard message handled every block. The precondition (the target denom already having a non-empty allowlist, or the admin choosing to add one) is entirely within the admin's own control, so likelihood is high whenever an application/user builds on a token-factory denom whose admin they do not fully trust.

### Recommendation
Apply the same mitigation suggested in the analog report: require a timelock/delay (e.g., a pending-allowlist + `block.time`/height threshold, or a two-step "propose then execute after N blocks") between `MsgUpdateDenom`'s allowlist change and its enforcement in `IsInDenomAllowList`/`SetDenomAllowList`, and emit the pending change so affected holders can react before it takes effect.

### Proof of Concept
1. Denom admin creates `factory/{admin}/XYZ` with `MsgCreateDenom` and no allowlist restriction; user `A` acquires and holds a balance of `XYZ` (e.g., via a swap or as payment for goods/services).
2. Admin submits `MsgUpdateDenom` with `AllowList = {Addresses: [admin, otherFavoredAddr]}` (excluding `A`) — this is processed by `UpdateDenom` and written via `bankKeeper.SetDenomAllowList` with no delay. [4](#0-3) 
3. In the very next block, `A` attempts `MsgSend` of `XYZ` (e.g., to redeem, sell, or complete an already-agreed trade); `IsInDenomAllowList` rejects it because `A` is not in the freshly-set allowlist: [5](#0-4) 
4. `A`'s balance of `XYZ` is now frozen indefinitely at the admin's sole discretion, with no timelock having given `A` any opportunity to act beforehand — reproducing the exact "instant deny-list" centralization risk described in the source report, confirmed by the existing test pattern in `TestSendSenderNotInAllowList`. [6](#0-5)

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

**File:** giga/deps/xbank/keeper/send.go (L479-502)
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
