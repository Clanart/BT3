### Title
Tokenfactory denom admin can weaponize `MsgUpdateDenom`'s AllowList to instantly and permanently freeze existing holders' balances - ([File: x/tokenfactory/keeper/msg_server.go], [File: x/tokenfactory/keeper/createdenom.go], [File: sei-cosmos/x/bank/keeper/send.go])

### Summary
The external report's bug class ("unrestricted parameter update breaks depositor/holder assumptions") maps directly onto sei-chain's `x/tokenfactory` module. A tokenfactory denom is permissionlessly creatable by any account, which becomes the denom's "admin" with unilateral, unrestricted power to update the denom's `AllowList` via `MsgUpdateDenom` at any block, with no grace period, no notice, and no protection of holders who already own the denom. Once the admin sets an `AllowList` that excludes an existing holder, that holder's balance becomes permanently unspendable, exactly analogous to the reported `set_vault_config_lock_period()` issue where an unrestricted, globally-applied parameter change breaks the assumptions of existing depositors.

### Finding Description
Any account can permissionlessly create a tokenfactory denom and become its admin: [1](#0-0) 

The admin can call `MsgUpdateDenom` to set the denom's `AllowList` at any time. The only validations performed are on list size and bech32-formedness of the addresses in the new list — there is no check that existing token holders remain included, no timelock, and no per-deposit/historical exemption: [2](#0-1) 

The message handler enforces only that the sender is the current admin, then immediately applies the new list via the bank keeper: [3](#0-2) 

The bank keeper then persists this list and it takes effect on the very next block for every subsequent transfer check: [4](#0-3) 

`MsgSend`/`MsgMultiSend` enforce the allow list on both sender and recipient with no reference to *when* the holder acquired the tokens: [5](#0-4) 

This is confirmed by the module's own test coverage: a holder can be denied the ability to send their existing, already-owned balance the instant the allow list is set to exclude them: [6](#0-5) 

This is structurally identical to the reported Move `vault_config.lock_period_ms` issue: a single privileged-but-reachable-by-an-ordinary-user role (the vault's exchange manager / here, the tokenfactory denom admin) can change a global parameter (`lock_period_ms` / the denom `AllowList`) that retroactively governs funds/withdrawal rights of *existing* holders/depositors, with no grace period and no protection for value already committed before the change.

### Impact Explanation
An account holding a factory-denominated token can have its existing, already-acquired balance permanently frozen the moment the denom admin submits `MsgUpdateDenom` with an `AllowList` that omits that holder's address. The holder cannot send the tokens out, and (per `TestSendReceiverNotInAllowList`) cannot receive further tokens of that denom either — this is a concrete, permanent freeze of user funds triggered unilaterally and instantaneously by a single transaction, satisfying the "permanent freezing" impact bar. Because tokenfactory denom creation is fully permissionless, this affects any user who accepts or holds a factory-denom token from any third-party admin, including EVM-side holders via the `factory/...` denom (reachable in the EVM/bank bridge path as well, since transfers ultimately route through `BankKeeper.SendCoins`/`IsInDenomAllowList`).

### Likelihood Explanation
High: it requires only that (1) a malicious or compromised account creates a tokenfactory denom (permissionless, one transaction) and distributes/receives it normally, and (2) the same account later submits a single `MsgUpdateDenom` transaction. No governance, no validator collusion, and no special privileges beyond being the self-appointed denom admin are needed. This is directly reachable by an ordinary transaction sender.

### Recommendation
- Do not allow `AllowList` updates to retroactively strip already-held balances of transfer rights; only apply new allow-list restrictions to future receipt of the denom, or grandfather existing holders at the time of the update.
- Introduce a mandatory delay/grace period between an `AllowList` update and its enforcement, mirroring the report's recommendation, so holders have an opportunity to exit before restrictions take effect.
- Emit clear, queryable warnings/documentation (e.g., via `MsgCreateDenom` docs and wallets) that tokenfactory denom admins can unilaterally freeze holder balances at any time via `AllowList` changes, so integrators and users can assess this risk before accepting such denoms.

### Proof of Concept
1. Account `A` creates denom `factory/A/foo` via `MsgCreateDenom` (permissionless) — `A` becomes admin per `x/tokenfactory/keeper/createdenom.go`.
2. `A` mints/sends `foo` tokens to account `B` (normal transfer, no allow list yet, so unrestricted).
3. `A` submits `MsgUpdateDenom{Denom: "factory/A/foo", AllowList: {Addresses: [A]}}` (excluding `B`) — this passes `validateUpdateDenom`/`validateAllowList` since it only checks size/bech32 validity, and is applied immediately via `SetDenomAllowList` (`x/tokenfactory/keeper/msg_server.go:57-92`, `sei-cosmos/x/bank/keeper/send.go:478-484`).
4. `B` (holding a pre-existing balance of `foo`) attempts `MsgSend` of `foo` — this fails with `"... is not allowed to send funds"` per the enforcement logic in `sei-cosmos/x/bank/keeper/msg_server.go:26-49` and demonstrated by the existing test `TestSendSenderNotInAllowList` (`sei-cosmos/x/bank/app_test.go:219-251`).
5. `B`'s previously-acquired `foo` balance is now permanently frozen with no recourse, achieved purely by `A`'s single, unrestricted `MsgUpdateDenom` transaction.

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

**File:** x/tokenfactory/keeper/createdenom.go (L72-114)
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
