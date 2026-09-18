### Title
Unrestricted `MsgUpdateDenom` Lets a Tokenfactory Denom Admin Rewrite the Transfer Allow-List After Users Already Hold and Have Committed the Token - (File: x/tokenfactory/keeper/msg_server.go / x/tokenfactory/keeper/createdenom.go)

### Summary
The externally-reported bug is that `HarTokenSale.setWhitelistReserve()` lets an owner change an allocation-defining parameter (`whitelistReserve`) at any time, even after users have already deposited funds under the old rule, with no freeze, timelock, or consent mechanism, breaking users' economic expectations. The same bug class — a privileged single-party keeper mutating a rule that governs what other users can already do with funds they hold/committed, with no freeze/timelock/settlement gate — exists in sei-chain's tokenfactory module: `MsgUpdateDenom` lets the current denom admin arbitrarily rewrite (or newly impose) a `DenomAllowList` on an existing tokenfactory denom at any time, instantly restricting which addresses already holding that denom may send or receive it, with no lock-in period and no protection for holders who acquired the token before the change.

### Finding Description
`MsgUpdateDenom` is handled by `msgServer.UpdateDenom` in [1](#0-0) . The only checks performed are that the caller is the current admin (`authorityMetadata.GetAdmin()`) and that the supplied `AllowList` passes basic validation (bech32 addresses, size limit) via `validateUpdateDenom`/`validateAllowList` in [2](#0-1) . There is:
- No check on when in the token's lifecycle the allow-list can be changed (no "first mint" / "first transfer" freeze analogous to the report's "before settlement" gate).
- No timelock or notice period before the new `AllowList` takes effect — `server.bankKeeper.SetDenomAllowList(ctx, denom, *msg.AllowList)` is applied in the very same transaction, as shown at [3](#0-2) .
- No snapshot of "who already holds/committed the token" that is protected from a subsequent restriction.

Once set, the allow-list is enforced globally for every bank send of that denom via `IsInDenomAllowList`, which blocks both sending and receiving for any address not in the list: [4](#0-3) . Tests confirm the immediate, absolute effect on holders who are not on the (admin-controlled) list — both as sender and as receiver — in [5](#0-4)  and [6](#0-5) .

This mirrors the report's pattern precisely: an unprivileged-relative-to-governance, single-signer-controlled parameter (`whitelistReserve` in the report, `DenomAllowList` here) that governs what *other* participants who already committed value (deposited funds / already hold and expect to freely transact the token) can do, is mutable at will by that one party, at any point after those participants have already acted, with no freeze/lock/notice mechanism. Any tokenfactory denom used as a tradable, LP, staking-derivative, or reward token can have its full holder base's transfer rights rewritten post hoc by the admin the instant after users acquired/deposited/staked it.

### Impact Explanation
- An admin can mint/distribute a tokenfactory denom freely, let users acquire or lock it into other protocols (LPs, vaults, staking wrappers), and then call `MsgUpdateDenom` to impose or rewrite an allow-list that instantly freezes transferability for any subset of holders — a direct "permanent freezing of funds" for the excluded addresses' token balance, since `SendCoins`/`MultiSend` calls will error with "is not allowed to send/receive funds" going forward.
- Because `SetDenomAllowList` fully overwrites the previous allow-list rather than merging or requiring monotonic widening, it can also be used to arbitrarily un-restrict, or restrict to an ever-shrinking whitelist, changing the effective distribution/allocation rules for token holders after they have already committed capital — the same "rewriting allocation rules after deposits" pattern as the report, but affecting token transferability instead of a sale allocation split.
- Unlike the reported bug (locked in a third-party sale contract), this is a first-party sei-chain module (`x/tokenfactory`) reachable by any account that created a denom (`MsgCreateDenom` is permissionless), so any public user can become the "admin" of a denom, distribute it, and then weaponize `MsgUpdateDenom` against later holders — this satisfies the fund-freezing impact bar without requiring governance or validator collusion.

### Likelihood Explanation
High. `MsgCreateDenom` is fully permissionless [7](#0-6) , and `MsgUpdateDenom`/`MsgChangeAdmin` require only the denom's current admin signature — no governance, no multi-party approval, no timelock [1](#0-0) . The entire attack is executable by one ordinary account, in one or two ordinary transactions (create denom → distribute/let others acquire → `MsgUpdateDenom` with a restrictive `AllowList`), with no special privileges beyond having created the denom in the first place.

### Recommendation
- Add a lifecycle gate to `validateUpdateDenom`/`UpdateDenom` analogous to the report's recommendation: disallow allow-list changes after some measurable "commitment" event (e.g., after total supply or holder count exceeds a threshold), or
- Require a time-locked delay between calling `MsgUpdateDenom` and the new `AllowList` taking effect, giving existing holders a window to exit positions, or
- Make allow-list changes only able to add addresses (monotonic widening) once a denom has non-admin holders, preventing an admin from retroactively restricting transferability for token that has already been distributed, or
- At minimum, emit a strong on-chain signal/event ahead of enforcement and document that tokenfactory denoms with mutable allow-lists are administrator-trust-dependent, and consider snapshotting/grandfathering existing holders' transfer rights at the time the allow-list is first set.

### Proof of Concept
1. Account A calls `MsgCreateDenom` with subdenom `foo`, becoming admin of `factory/A/foo` (permissionless, per `x/tokenfactory/README.md`).
2. A mints and freely transfers `factory/A/foo` to accounts B, C, D (no allow-list yet, transfers unrestricted, per `IsInDenomAllowList` skip-if-empty logic in [8](#0-7) ). B, C, D deposit this token into an LP pool / vault / stake it, believing it is freely transferable.
3. A calls `MsgUpdateDenom` on `factory/A/foo` with `AllowList = {A}` (or any list excluding B, C, D). This is accepted immediately: `msgServer.UpdateDenom` only checks that A is the admin ( [9](#0-8) ) and that the new list is well-formed ( [10](#0-9) ), then calls `server.bankKeeper.SetDenomAllowList(ctx, denom, *msg.AllowList)` ( [11](#0-10) ).
4. From the next block onward, any `MsgSend`/`MsgMultiSend` involving B, C, or D and `factory/A/foo` fails with "is not allowed to send/receive funds" (behavior verified by `TestSendReceiverNotInAllowList` / `TestSendSenderNotInAllowList` in [5](#0-4)  and [6](#0-5) ), permanently trapping B, C, and D's already-held/committed balances of that denom.

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

**File:** sei-cosmos/x/bank/keeper/send.go (L479-524)
```go
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
