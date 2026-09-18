### Title
Tokenfactory denom admin can instantly set an `AllowList` to freeze existing holders' tokens with no delay - (File: x/tokenfactory/keeper/msg_server.go, sei-cosmos/x/bank/keeper/send.go)

### Summary
The tokenfactory module allows any unprivileged account to permissionlessly create a denom (`factory/{creator}/{subdenom}`), and the creator is automatically granted "admin" rights over that denom. The admin can call `MsgUpdateDenom` to instantly set a bank-level `AllowList` on the denom that restricts which addresses may send/receive it, with no delay or notice, closely mirroring the NFTX report's pattern of an unprivileged-but-privileged-over-their-own-object "manager" instantly changing rules to trap other users' funds.

### Finding Description
Anyone can call `MsgCreateDenom` and become the sole "admin" of a tokenfactory denom [1](#0-0) . Other users can then acquire and hold that denom (e.g., via transfers, DEX trading, airdrops) without any special permission, since minting/transfer is otherwise permissionless for holders.

The `UpdateDenom` message handler lets the admin unilaterally set a bank `AllowList` for the denom in a single transaction, with only an admin-authorization check and no timelock or notice period: [2](#0-1) 

That `AllowList` is enforced globally by the bank keeper's `IsInDenomAllowList`/`SetDenomAllowList` logic, which governs whether *any* address may send or receive coins of that denom, not just the admin's own balance: [3](#0-2) 

This is confirmed by existing tests showing that an address holding the factory denom, if excluded from a newly set `AllowList`, is rejected with "is not allowed to send/receive funds" on a plain `MsgSend`: [4](#0-3) [5](#0-4) 

Because the admin can set/change this list at any block, at any time, for a denom that may already be held broadly by third parties (e.g., a popular tokenfactory-based community token), the admin can retroactively lock out existing holders from ever moving their already-acquired balances — permanently freezing their funds in place — exactly analogous to the NFTX vault manager instantly disabling features/fees to trap user funds with no reaction window.

### Impact Explanation
An admin who created a widely-adopted tokenfactory denom can, in a single transaction, exclude arbitrary existing holders from the `AllowList`, permanently freezing their already-held balances (they can never send them, and non-allow-listed addresses can never receive them). This is a concrete, permanent freezing of funds for unprivileged holders who have no way to react, satisfying the "permanent freezing" impact bar. It does not require any consensus, node, or governance compromise — a single tokenfactory admin transaction is sufficient.

### Likelihood Explanation
Likelihood is High: tokenfactory denom creation and `MsgUpdateDenom` are both fully permissionless/self-service actions reachable by any transaction sender, requiring no privileged role, no governance, and no coordination. Any tokenfactory denom creator can create trust, let others acquire the token, and then set a restrictive `AllowList` at will.

### Recommendation
- Document clearly (and ideally surface at the wallet/RPC/CLI level) that tokenfactory denom holders are exposed to admin-controlled `AllowList` changes at any time, since the admin is fully trusted for that denom.
- Consider adding a timelock/delay (e.g., N blocks) before an `AllowList` change (especially one that would remove addresses that currently hold nonzero balances) takes effect, so holders have a window to exit or move funds before restrictions apply.
- Alternatively, restrict `AllowList` updates so that they cannot retroactively exclude addresses that already hold a nonzero balance of the denom, only affecting future recipients.

### Proof of Concept
1. Attacker calls `MsgCreateDenom` to create `factory/{attacker}/{subdenom}`, becoming its admin [6](#0-5) .
2. Attacker mints tokens and distributes/sells them to third-party users via normal transfers (no `AllowList` set yet, so transfers succeed freely, as in `TestSendWithEmptyAllowList`) [7](#0-6) .
3. Once the token has gained holders, attacker submits `MsgUpdateDenom` with an `AllowList` containing only the attacker's own address (or excluding the victim holders) [8](#0-7) .
4. Any subsequent `MsgSend` by an excluded holder is rejected by the bank keeper's `IsInDenomAllowList` check, permanently freezing their balance [9](#0-8) .

### Citations

**File:** x/tokenfactory/README.md (L1-18)
```markdown
# Token Factory

The tokenfactory module allows any account to create a new token with
the name `factory/{creator address}/{subdenom}`. Because tokens are
namespaced by creator address, this allows token minting to be
permissionless, due to not needing to resolve name collisions. A single
account can create multiple denoms, by providing a unique subdenom for each
created denom. Once a denom is created, the original creator is given
"admin" privileges over the asset. This allows them to:

- Mint their denom to any account
- Burn their denom from any account
- Create a transfer of their denom between any two accounts
- Change the admin. In the future, more admin capabilities may be added. Admins
  can choose to share admin privileges with other accounts using the authz
  module. The `ChangeAdmin` functionality, allows changing the master admin
  account, or even setting it to `""`, meaning no account has admin privileges
  of the asset.
```

**File:** x/tokenfactory/keeper/msg_server.go (L55-55)
```go
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

**File:** sei-cosmos/x/bank/keeper/send.go (L474-524)
```go
func SplitUseiWeiAmount(amt sdk.Int) (sdk.Int, sdk.Int) {
	return amt.Quo(OneUseiInWei), amt.Mod(OneUseiInWei)
}

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

**File:** sei-cosmos/x/bank/app_test.go (L185-217)
```go
func TestSendWithEmptyAllowList(t *testing.T) {
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
		types.AllowList{Addresses: []string{}})

	a.Commit(context.Background())

	res1 := a.AccountKeeper.GetAccount(ctx, addr1)
	require.NotNil(t, res1)
	require.Equal(t, acc, res1.(*authtypes.BaseAccount))

	origAccNum := res1.GetAccountNumber()
	origSeq := res1.GetSequence()

	sendMsg := types.NewMsgSend(addr1, addr2, sdk.Coins{sdk.NewInt64Coin(factoryDenom, 10)})
	header := tmproto.Header{ChainID: a.ChainID, Height: a.LastBlockHeight() + 1}
	txGen := app.MakeEncodingConfig().TxConfig
	_, _, err := app.SignCheckDeliver(t, txGen, a.BaseApp, header, []sdk.Msg{sendMsg}, []uint64{origAccNum}, []uint64{origSeq}, true, true, priv1)
	require.NoError(t, err)

	app.CheckBalance(t, a, addr1, sdk.Coins{sdk.NewInt64Coin(factoryDenom, 90)})
	app.CheckBalance(t, a, addr2, sdk.Coins{sdk.NewInt64Coin(factoryDenom, 10)})
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
