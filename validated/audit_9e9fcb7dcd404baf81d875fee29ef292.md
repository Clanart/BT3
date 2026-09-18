### Title
Tokenfactory admin can permanently lock itself out of minting/burning its own denom via the coin `AllowList` - (File: `x/tokenfactory/keeper/bankactions.go`)

### Summary
The external report describes `rOUSG`'s privileged `BURNER_ROLE` being blocked from burning tokens because a generic compliance check (`_getKYCStatus`) is applied to the `from` address with no bypass for the privileged burner. The `x/tokenfactory` module has a structurally identical pattern: the denom `admin` is the only account authorized to mint/burn a `factory/...` denom, but the underlying bank-level transfer used to execute that privileged mint/burn enforces the `bank` module's `AllowList` compliance check against that very same admin address, with no special-case bypass for the admin/authority role.

### Finding Description
`tokenfactory` denoms can carry a `cosmos.bank.v1beta1.AllowList` restricting which addresses may hold/transfer the denom (set at `MsgCreateDenom` and updatable via `MsgUpdateDenom`) [1](#0-0) . When the admin mints or burns their own denom, `mintTo`/`burnFrom` route the operation through the standard bank keeper's `SendCoinsFromModuleToAccount` / `SendCoinsFromAccountToModule`, which move funds directly to/from the admin's own address [2](#0-1) .

The bank module enforces `IsInDenomAllowList` for `factory/...` denoms on the address being debited/credited, with no exemption for a denom's own admin/authority: if an `AllowList` exists for the denom and the acting address is not on it, the transfer is rejected with `"... is not allowed to send/receive funds"` [3](#0-2) . This mirrors the rOUSG `_beforeTokenTransfer` check that blocked `BURNER_ROLE` because it treated the privileged actor the same as any regular counterparty [4](#0-3) . Test coverage confirms only whitelisted module accounts are exempted from the allow-list check, not arbitrary privileged EOAs like a denom's admin [5](#0-4) .

Because `MsgUpdateDenom` lets the admin change/replace the `AllowList` for their own denom, an admin who removes their own address from the allow list (intentionally or by mistake) — or who is removed by a co-admin via `ChangeAdmin`/`UpdateDenom` — will subsequently fail `MsgMint` and `MsgBurn` on that denom even though `Burn`/`Mint` in `x/tokenfactory/keeper/msg_server.go` correctly authorizes them as the on-chain `admin` [6](#0-5) .

### Impact Explanation
This can permanently freeze the admin's ability to mint or burn their own tokenfactory-issued denom, since `Mint`/`Burn` are gated exclusively to the `admin` (no alternate privileged path bypasses the allow list check), matching the "no mechanism to allow privileged burn/mint despite compliance list" theme in the source report. Frozen supply management for an admin-controlled denom is a form of permanent functionality loss for that denom's issuer.

### Likelihood Explanation
Requires a tokenfactory denom to have been configured (at creation or update) with a non-empty `AllowList` that does not include the current admin address — a state reachable purely by a denom creator/admin's own transactions (`MsgCreateDenom`/`MsgUpdateDenom`), without any privileged/validator/governance action, making it directly reachable by any unprivileged tokenfactory denom creator.

### Recommendation
Exempt the denom's current `admin`/authority address from the `IsInDenomAllowList` check when executing `mintTo`/`burnFrom` in `x/tokenfactory/keeper/bankactions.go`, or bypass the allow-list check within `BaseSendKeeper.SendCoins*` when the caller is the recognized tokenfactory module performing an authorized admin mint/burn.

### Proof of Concept
1. Admin `A` creates a denom via `MsgCreateDenom` with an initial `AllowList` (or later sets one via `MsgUpdateDenom`) that does not include `A`'s own address.
2. `A` calls `MsgMint` or `MsgBurn` for that denom.
3. `msg_server.go`'s `Mint`/`Burn` confirm `A` is the authorized admin and call `mintTo`/`burnFrom`, which invoke `SendCoinsFromModuleToAccount`/`SendCoinsFromAccountToModule` [2](#0-1) .
4. The bank keeper's allow-list enforcement rejects the transfer because `A` is not in the denom's `AllowList`, exactly mirroring the `"rOUSG: 'from' address not KYC'd"` failure mode from the source report.

**Note**: I could not fully trace, within the remaining available tool calls, whether `SendCoinsFromModuleToAccount`/`SendCoinsFromAccountToModule` (as distinct from the `BaseSendKeeper.SendCoins` path directly tested) definitively invoke `IsInDenomAllowList` in this exact codebase version — the strongest direct evidence I found is the `TestSendCoinsAccountToModuleWithAllowList`/`TestSendCoinsFromModuleToAccountWithAllowList`-style tests showing account↔module allow-list enforcement [5](#0-4) , plus the `IsInDenomAllowList` helper itself [3](#0-2) . Confirming the exact call chain from `MsgMint`/`MsgBurn` end-to-end would benefit from a full Devin session with repository access to trace `SendCoinsFromModuleToAccount`/`SendCoinsFromAccountToModule` implementations, which were not fully retrievable via the index.

### Citations

**File:** proto/tokenfactory/tx.proto (L29-37)
```text
message MsgCreateDenom {
  string sender = 1 [(gogoproto.moretags) = "yaml:\"sender\""];
  // subdenom can be up to 44 "alphanumeric" characters long.
  string subdenom = 2 [(gogoproto.moretags) = "yaml:\"subdenom\""];
  cosmos.bank.v1beta1.AllowList allow_list = 3 [
    (gogoproto.moretags) = "yaml:\"allow_list\"",
    (gogoproto.nullable) = true
  ];
}
```

**File:** x/tokenfactory/keeper/bankactions.go (L11-57)
```go
func (k Keeper) mintTo(ctx sdk.Context, amount sdk.Coin, mintTo string) error {
	// verify that denom is an x/tokenfactory denom
	_, _, err := types.DeconstructDenom(amount.Denom)
	if err != nil {
		return err
	}

	logger.Info("Minting amount for module", "amount", amount, "module", types.ModuleName)
	err = k.bankKeeper.MintCoins(ctx, types.ModuleName, sdk.NewCoins(amount))
	if err != nil {
		return err
	}

	addr, err := sdk.AccAddressFromBech32(mintTo)
	if err != nil {
		return err
	}

	logger.Info("Sending minted amount to addr", "amount", amount, "addr", addr)
	return k.bankKeeper.SendCoinsFromModuleToAccount(ctx, types.ModuleName,
		addr,
		sdk.NewCoins(amount))
}

func (k Keeper) burnFrom(ctx sdk.Context, amount sdk.Coin, burnFrom string) error {
	// verify that denom is an x/tokenfactory denom
	_, _, err := types.DeconstructDenom(amount.Denom)
	if err != nil {
		return err
	}

	addr, err := sdk.AccAddressFromBech32(burnFrom)
	if err != nil {
		return err
	}

	logger.Info("Sending amount to module from account", "amount", amount, "module", types.ModuleName, "account", addr)
	err = k.bankKeeper.SendCoinsFromAccountToModule(ctx,
		addr,
		types.ModuleName,
		sdk.NewCoins(amount))
	if err != nil {
		return err
	}

	logger.Info("Burning amount from module", "amount", amount, "module", types.ModuleName)
	return k.bankKeeper.BurnCoins(ctx, types.ModuleName, sdk.NewCoins(amount))
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

**File:** sei-cosmos/x/bank/app_test.go (L219-249)
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

```

**File:** sei-cosmos/x/bank/keeper/keeper_test.go (L656-690)
```go
}

// Test that creating allowlist does not block sending from account to module even though we are not explicitly adding
// the module account to the allowlist
func (suite *IntegrationTestSuite) TestSendCoinsAccountToModuleWithAllowList() {
	// add module accounts to supply keeper
	ctx := suite.ctx
	moduleAddresses := make(map[string]bool)
	moduleAddresses[multiPermAcc.GetAddress().String()] = true
	moduleAddresses[suite.app.AccountKeeper.GetModuleAddress("mint").String()] = true
	_, keeper := suite.initKeepersWithmAccPerms(make(map[string]bool))
	app := suite.app
	app.BankKeeper = keeper

	addr1 := sdk.AccAddress("addr1_______________")
	acc1 := app.AccountKeeper.NewAccountWithAddress(ctx, addr1)
	app.AccountKeeper.SetAccount(ctx, acc1)
	factoryCoin := newFactoryFooCoin(addr1, 100)
	balances := sdk.NewCoins(factoryCoin, newBarCoin(50))
	app.BankKeeper.SetDenomAllowList(ctx, factoryCoin.Denom, types.AllowList{
		Addresses: []string{addr1.String()}})

	// set up bank balances
	suite.Require().NoError(apptesting.FundAccount(app.BankKeeper, ctx, addr1, balances))

	sendCoins := sdk.NewCoins(newFactoryFooCoin(addr1, 50), newBarCoin(20))
	suite.Require().NoError(app.BankKeeper.SendCoinsFromAccountToModule(ctx, addr1, multiPerm, sendCoins))
	expectedBankBalances := sdk.NewCoins(newFactoryFooCoin(addr1, 50), newBarCoin(30))
	// assert account balances correct
	bals := app.BankKeeper.GetAllBalances(ctx, addr1)
	suite.Require().Equal(expectedBankBalances, bals)
	// assert module balances correct
	userBals := app.BankKeeper.GetAllBalances(ctx, multiPermAcc.GetAddress())
	suite.Require().Equal(sendCoins, userBals)
}
```

**File:** x/tokenfactory/keeper/msg_server.go (L94-154)
```go
func (server msgServer) Mint(goCtx context.Context, msg *types.MsgMint) (*types.MsgMintResponse, error) {
	ctx := sdk.UnwrapSDKContext(goCtx)

	// pay some extra gas cost to give a better error here.
	_, denomExists := server.bankKeeper.GetDenomMetaData(ctx, msg.Amount.Denom)
	if !denomExists {
		return nil, types.ErrDenomDoesNotExist.Wrapf("denom: %s", msg.Amount.Denom)
	}

	authorityMetadata, err := server.GetAuthorityMetadata(ctx, msg.Amount.GetDenom())
	if err != nil {
		return nil, err
	}

	if msg.Sender != authorityMetadata.GetAdmin() {
		return nil, types.ErrUnauthorized
	}

	err = server.mintTo(ctx, msg.Amount, msg.Sender)
	if err != nil {
		return nil, err
	}

	ctx.EventManager().EmitEvents(sdk.Events{
		sdk.NewEvent(
			types.TypeMsgMint,
			sdk.NewAttribute(types.AttributeMintToAddress, msg.Sender),
			sdk.NewAttribute(types.AttributeAmount, msg.Amount.String()),
		),
	})

	return &types.MsgMintResponse{}, nil
}

func (server msgServer) Burn(goCtx context.Context, msg *types.MsgBurn) (*types.MsgBurnResponse, error) {
	ctx := sdk.UnwrapSDKContext(goCtx)

	authorityMetadata, err := server.GetAuthorityMetadata(ctx, msg.Amount.GetDenom())
	if err != nil {
		return nil, err
	}

	if msg.Sender != authorityMetadata.GetAdmin() {
		return nil, types.ErrUnauthorized
	}

	err = server.burnFrom(ctx, msg.Amount, msg.Sender)
	if err != nil {
		return nil, err
	}

	ctx.EventManager().EmitEvents(sdk.Events{
		sdk.NewEvent(
			types.TypeMsgBurn,
			sdk.NewAttribute(types.AttributeBurnFromAddress, msg.Sender),
			sdk.NewAttribute(types.AttributeAmount, msg.Amount.String()),
		),
	})

	return &types.MsgBurnResponse{}, nil
}
```
