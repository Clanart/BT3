### Title
Tokenfactory `DenomAllowList` (restricted-denom) enforcement is bypassed for any transfer path that does not go through `bank.MsgServer.Send`/`MultiSend` - ([File: sei-cosmos/x/bank/keeper/send.go], [File: sei-wasmd/x/wasm/keeper/handler_plugin.go])

### Summary
The bug class matches the External Report exactly: an access-restriction check exists in the codebase (`IsInDenomAllowList`, the sei-chain analog of `marketWhitelistCheck`) but is not actually wired into the core token-movement primitive. It is only invoked from the two top-level bank message handlers, `MsgServer.Send` and `MsgServer.MultiSend`. The underlying `SendCoins`/`SendCoinsWithoutAccCreation`/`InputOutputCoins` keeper functions that every other module (CosmWasm, precompiles, module-to-account transfers, etc.) calls directly perform **no** allow-list check at all. As a result, a tokenfactory denom creator's "restricted" (allow-listed) denom can be transferred to/from addresses that are **not** on the allow list simply by routing the transfer through any code path other than `bank.MsgSend`/`MsgMultiSend`.

### Finding Description
`x/tokenfactory` lets a denom creator restrict transfers of a `factory/...` denom to a specific set of addresses via `MsgCreateDenom`/`MsgUpdateDenom` → `bankKeeper.SetDenomAllowList` [1](#0-0) . This allow list is only consulted by `IsInDenomAllowList`, and that function is called exclusively from `bank.msgServer.Send` and `bank.msgServer.MultiSend`: [2](#0-1) [3](#0-2) 

The actual token-moving primitives (`SendCoins`, `SendCoinsWithoutAccCreation`, `InputOutputCoins`, `SendCoinsAndWei`) declared on the `SendKeeper` interface contain no call to `IsInDenomAllowList` anywhere in their implementation: [4](#0-3) 

This means any caller that invokes the keeper method directly — rather than submitting a `MsgSend`/`MsgMultiSend` — completely bypasses the restriction, exactly analogous to `RCTreasury.marketWhitelistCheck()` never being consulted by the actual restricted code path. One concrete reachable instance: CosmWasm's `BankMsg` dispatch in `sei-wasmd/x/wasm/keeper/handler_plugin.go` calls the bank keeper's `SendCoins` directly (not through `MsgServer.Send`):



Any CosmWasm contract that forwards attacker-supplied funds via `BankMsg::Send` therefore moves tokenfactory coins without any allow-list check, even if the denom has a restrictive `AllowList` set. Likewise, several legacy bank-precompile implementations call `p.bankKeeper.SendCoins` directly instead of going through `bankMsgServer.Send` (e.g. `precompiles/bank/legacy/v552/bank.go`, `v555`, `v562`, etc.): [5](#0-4) 

while the *current* `precompiles/bank/bank.go` was fixed to route through `bankMsgServer.Send` (and thus does perform the check): [6](#0-5) 

This shows the maintainers already recognize `SendCoins` is not allow-list-safe and patched one call site (the EVM bank precompile) while leaving the underlying primitive itself — and other call sites such as the CosmWasm bank message dispatcher — unprotected.

### Impact Explanation
A denom creator who restricts a tokenfactory denom to a whitelist (e.g. for compliance/KYC purposes) gets no actual guarantee: any unprivileged user can submit a `MsgExecuteContract` against a CosmWasm contract that forwards funds via `BankMsg::Send` (a completely generic, widely available capability of the CW execute pipeline) to move the restricted denom to/from addresses outside the allow list. This defeats the security guarantee the allow-list feature is meant to provide and could be used to violate regulatory/compliance restrictions that projects rely on this mechanism to enforce, or to drain/launder a compliance-restricted asset out of the permitted set of holders. This satisfies "unauthorized transfer" / "fund loss or permanent freezing" impact criteria to the extent that the restricted-denom control is a security boundary the protocol advertises and enforces inconsistently.

### Likelihood Explanation
High. Any unprivileged party can create a trivial CosmWasm contract (or use an existing generic proxy/wallet contract) whose execute handler returns a `BankMsg::Send` submessage forwarding the caller's coins. This requires only a standard `MsgExecuteContract`, no special permissions, no precompile allow-listing, and no validator collusion. The existence of the same anti-pattern already fixed in the current EVM bank precompile (routing through `bankMsgServer.Send` instead of raw `SendCoins`) shows the underlying primitive is known to be allow-list-unsafe, but the fix was applied at only one call site instead of at the primitive itself.

### Recommendation
Move the `IsInDenomAllowList` (and any related send-restriction) check into `BaseSendKeeper.SendCoins`/`SendCoinsWithoutAccCreation`/`InputOutputCoins` themselves (or into a bank "send restriction" hook invoked unconditionally by these functions), rather than only in the `MsgServer.Send`/`MultiSend` handlers. Audit every direct caller of these keeper methods — CosmWasm's bank message dispatcher, all legacy and current precompiles, and any other module doing direct transfers — to confirm the allow list is now enforced universally, and add regression tests exercising CW `BankMsg::Send` and precompile `send` paths against an allow-listed tokenfactory denom.

### Proof of Concept
1. Create a tokenfactory denom `factory/{creator}/RESTRICTED` and set `DenomAllowList{Addresses: [creator]}` via `MsgCreateDenom`/`MsgUpdateDenom`.
2. Fund a CosmWasm contract account (or the creator's own account, then interact with a generic "forwarder" contract) with the restricted denom.
3. Submit `MsgExecuteContract` invoking a contract that returns a `BankMsg::Send{ to_address: <non-whitelisted address>, amount: [restricted denom] }` submessage.
4. Observe that the transfer succeeds because `sei-wasmd/x/wasm/keeper/handler_plugin.go` calls `bankKeeper.SendCoins` directly, which never calls `IsInDenomAllowList`, unlike `bank.MsgServer.Send` which would have rejected the same transfer with `"is not allowed to receive funds"` per the existing test `TestSendReceiverNotInAllowList` [7](#0-6) .

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

**File:** sei-cosmos/x/bank/keeper/msg_server.go (L74-94)
```go
func (k msgServer) MultiSend(goCtx context.Context, msg *types.MsgMultiSend) (*types.MsgMultiSendResponse, error) {
	ctx := sdk.UnwrapSDKContext(goCtx)
	denomToAllowListCache := make(map[string]AllowedAddresses)
	// NOTE: totalIn == totalOut should already have been checked
	for _, in := range msg.Inputs {
		if err := k.IsSendEnabledCoins(ctx, in.Coins...); err != nil {
			return nil, err
		}
		accAddr := sdk.MustAccAddressFromBech32(in.Address)
		if !k.IsInDenomAllowList(ctx, accAddr, in.Coins, denomToAllowListCache) {
			return nil, sdkerrors.Wrapf(sdkerrors.ErrUnauthorized, "%s is not allowed to send funds", accAddr)
		}
	}

	for _, out := range msg.Outputs {
		accAddr := sdk.MustAccAddressFromBech32(out.Address)

		if k.BlockedAddr(accAddr) || !k.IsInDenomAllowList(ctx, accAddr, out.Coins, denomToAllowListCache) {
			return nil, sdkerrors.Wrapf(sdkerrors.ErrUnauthorized, "%s is not allowed to receive funds", out.Address)
		}
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

**File:** precompiles/bank/legacy/v552/bank.go (L200-203)
```go

	if err := p.bankKeeper.SendCoins(ctx, senderSeiAddr, receiverSeiAddr, sdk.NewCoins(sdk.NewCoin(denom, sdk.NewIntFromBigInt(amount)))); err != nil {
		return nil, err
	}
```

**File:** precompiles/bank/bank.go (L232-245)
```go
	msg := &banktypes.MsgSend{
		FromAddress: senderSeiAddr.String(),
		ToAddress:   receiverSeiAddr.String(),
		Amount:      sdk.NewCoins(sdk.NewCoin(denom, sdk.NewIntFromBigInt(amount))),
	}

	err = msg.ValidateBasic()
	if err != nil {
		return nil, 0, err
	}

	if _, err = p.bankMsgServer.Send(sdk.WrapSDKContext(ctx), msg); err != nil {
		return nil, 0, err
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
