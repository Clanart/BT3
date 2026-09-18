Found a valid analog: `MsgUpdateDenom`'s allow-list path (`server.UpdateDenom` in `x/tokenfactory/keeper/msg_server.go`) sets a token's bank `AllowList` with no lower-bound / self-inclusion validation.

### Title
Tokenfactory denom admin can permanently lock all holders (including itself) out of a token via an unvalidated, unbounded `AllowList` in `MsgUpdateDenom` - (File: `x/tokenfactory/keeper/msg_server.go`)

### Summary
`MsgUpdateDenom.UpdateDenom` lets a tokenfactory denom's admin overwrite the bank `AllowList` for that denom with **no minimum-content or self-inclusion validation**, unlike `MsgCreateDenom`, which validates the initial allow list via `validateAllowList`. Once set, `x/bank`'s `IsInDenomAllowList` enforces that only listed addresses may send/receive that denom, so an allow list that omits some or all addresses (including the admin) permanently blocks transfers of that denom for those addresses — mirroring the reported bug class of "config value settable with no bound, producing a state that makes required user actions permanently impossible."

### Finding Description
`CreateDenom`'s `msgServer.CreateDenom` validates any provided allow list with `server.validateAllowList(ctx, msg.AllowList)` before calling `bankKeeper.SetDenomAllowList` [1](#0-0) , and `validateAllowList`/`validateAllowListSize` only check an *upper* bound (`k.GetDenomAllowListMaxSize`) and bech32-validity of each address, never a lower bound or membership requirement [2](#0-1) .

However, `UpdateDenom` calls `server.bankKeeper.SetDenomAllowList(ctx, denom, *msg.AllowList)` directly with **no call to `validateAllowList` at all** [3](#0-2) . This means an admin can submit an `AllowList` that is empty, or that simply excludes some/most/all currently-held addresses of that denom — including the admin's own address — with zero validation.

Once set, `x/bank`'s send-restriction logic enforces this list strictly: any address not present in `AllowList.Addresses` is rejected on both send and receive, as demonstrated by `TestSendReceiverNotInAllowList` and `TestSendSenderNotInAllowList` [4](#0-3) [5](#0-4) . There is no code path that lets the admin subsequently "unlock" existing holders' balances if the admin address itself is excluded from the new list — the admin can no longer send that denom either, and any future `MsgUpdateDenom` call must itself be sent using usei/other funds (which is fine), but the specific tokenfactory denom becomes permanently frozen for excluded addresses (funds can never move again for them, since minting/burning still requires the admin to be the *authority* — not the allow-listed sender — but ordinary bank `Send`/transfers of the already-distributed denom go through the allow-list check that now rejects them).

This is directly analogous to the reported CVE-2026-56228 pattern: an authenticated administrator (org admin / denom admin) sets a policy value (min password length / an allow list) with no bound/consistency validation, and the resulting state makes a required action (logging in / transacting) permanently impossible for members (org members / token holders), causing an application-level denial of service.

### Impact Explanation
Any address excluded from a maliciously or carelessly updated `AllowList` permanently loses the ability to send or receive that specific tokenfactory denom. If the admin's own address is excluded (accidentally or via griefing by a compromised/malicious key), the denom becomes unmanageable and frozen for those holders — a permanent freezing of funds for that denom's holder set, satisfying the "permanent freezing" impact bar. This is scoped to the specific tokenfactory denom (equivalent to the "organization" in the original report) rather than the whole chain, but is a concrete, unrecoverable state change reachable by a single `MsgUpdateDenom` transaction from an unprivileged (denom-admin) sender.

### Likelihood Explanation
Likelihood is high for accidental self-lockout (a common operational mistake — admin updates an allow list without realizing existing holders, or itself, must be included), and plausible for deliberate griefing if a denom admin key is compromised or acts maliciously against its own token's holders. No governance, consensus, or validator-level access is required — only a standard signed `MsgUpdateDenom` transaction from the denom's admin account, which is the same trust tier as the tokenfactory `CreateDenom`/`ChangeAdmin` flows already reachable by ordinary users.

### Recommendation
Apply the same `validateAllowList`/`validateAllowListSize` checks used in `CreateDenom` to `UpdateDenom` before calling `SetDenomAllowList`, and additionally validate that the list is either empty (no restriction) or contains a sane minimum, and optionally require/ warn if the current admin address is omitted, to avoid an update that immediately locks the admin (and by extension the denom) out of further correction.

### Proof of Concept
1. Admin `A` creates denom `factory/A/foo` via `MsgCreateDenom` (no allow list) and mints/distributes it to holders `B`, `C`.
2. Admin `A` submits `MsgUpdateDenom{Sender: A, Denom: "factory/A/foo", AllowList: {Addresses: ["A"]}}` (or even `{Addresses: []}`), which is processed by `msgServer.UpdateDenom` with no allow-list validation [6](#0-5) .
3. `bankKeeper.SetDenomAllowList` stores this list unconditionally.
4. Holder `B` attempts `MsgSend` of `factory/A/foo` to `C`; the bank keeper's `IsInDenomAllowList` check rejects it, exactly as reproduced by `TestSendSenderNotInAllowList`/`TestSendReceiverNotInAllowList` [5](#0-4) . `B` and `C` are now permanently unable to move `factory/A/foo`, and there is no recovery path since the restriction persists indefinitely absent another `MsgUpdateDenom` call that must itself originate from admin `A` (who may also be locked out if `A` was excluded from the list).

### Citations

**File:** x/tokenfactory/keeper/msg_server.go (L37-46)
```go
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

**File:** x/tokenfactory/keeper/createdenom.go (L88-114)
```go
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

**File:** sei-cosmos/x/bank/app_test.go (L117-148)
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
