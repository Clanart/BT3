### Title
Bank precompile allows bypassing tokenfactory `DenomAllowList` (KYC/permissioned-denom) restrictions - (File: precompiles/bank/legacy/v600/bank.go, sei-cosmos/x/bank/keeper/msg_server.go)

### Summary
The original report describes smart-contract wallets being used as unrestricted proxies to bypass a KYC/permission gate (Coinbase attestations) that is only enforced at one entry point. Sei-chain has an analogous permission gate: the bank module's `DenomAllowList` mechanism, used by tokenfactory to restrict who may send/receive a given (e.g. permissioned/KYC'd) denom. This restriction is only checked in the Cosmos `MsgSend`/`MsgMultiSend` message handlers, not inside the underlying `SendCoins` keeper method itself.

### Finding Description
`BaseSendKeeper.IsInDenomAllowList` implements the allowlist check for tokenfactory-prefixed denoms: for a restricted denom, both sender and recipient must be present in the `DenomAllowList` or the transfer must be rejected. [1](#0-0) 

This check is only invoked from the message server layer, in `msgServer.Send`, where both the `from` and `to` addresses are explicitly checked against `IsInDenomAllowList` before `SendCoins` is called: [2](#0-1) 

However, `IsInDenomAllowList` is referenced only inside `sei-cosmos/x/bank/keeper/send.go` (definition) and `sei-cosmos/x/bank/keeper/msg_server.go` (`Send`/`MultiSend` handlers) in the whole codebase — it is not called from `precompiles/bank/legacy/v600/bank.go`, which implements the EVM-exposed bank precompile that lets EVM contracts move `usei`/bank-denom balances by calling `SendCoins` directly rather than going through the Cosmos `MsgSend` handler. Because the allowlist gate lives in the message-handler wrapper and not in `BaseSendKeeper.SendCoins` itself, any EVM smart contract (a deployer-controlled, potentially upgradeable/delegatecall-capable contract, exactly the "smart wallet as proxy" pattern from the report) that reaches the bank precompile's send path can move a KYC/allowlist-restricted tokenfactory denom to or from an address that is not on the `DenomAllowList`, exactly mirroring the original bug: a policy check granted to (or reachable from) a proxy-capable smart contract is not actually enforced at every path that can move value.

### Impact Explanation
If a tokenfactory denom's `DenomAllowList` is used by an issuer to enforce KYC/sanctions-style controls (the same threat model as the Coinbase report), an EVM contract calling the bank precompile can move that restricted denom to/from addresses that were never vetted or attested, defeating the purpose of the allowlist. This is an unauthorized-transfer-via-precompile scenario bypassing an issuer-configured control, which is a concrete impact under the specified criteria.

### Likelihood Explanation
Any unprivileged EVM contract deployer can reach the bank precompile from a single transaction; no special privilege, governance, or validator collusion is required. The only precondition is that an issuer has configured a `DenomAllowList` for a tokenfactory denom (a supported, intentional feature), making this reachable in normal operation whenever permissioned tokens exist on the chain.

### Recommendation
Move the `IsInDenomAllowList` check into `BaseSendKeeper.SendCoins` (or an equivalent lower-level chokepoint that all transfer paths funnel through, including the bank precompile and any CosmWasm bank bindings) rather than only in the `MsgServer.Send`/`MultiSend` handlers, so that EVM precompile calls, CosmWasm `BankMsg::Send`, and any other future entry point cannot bypass the allowlist.

### Proof of Concept
1. Issuer creates a tokenfactory denom `factory/<issuer>/kyc-token` and calls `SetDenomAllowList` restricting transfers to a fixed set of KYC'd addresses [3](#0-2) .
2. A user deploys an EVM smart contract that calls the bank precompile's transfer method with `factory/<issuer>/kyc-token` as the denom, sending it to an address that is not on the allowlist.
3. Because `precompiles/bank/legacy/v600/bank.go` invokes `SendCoins` without going through `msgServer.Send`'s `IsInDenomAllowList` checks, the transfer succeeds despite the recipient being outside the allowlist, whereas the same transfer via a native `MsgSend` would be rejected with "is not allowed to receive funds" as shown in the corresponding test cases [4](#0-3) .

### Citations

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

**File:** sei-cosmos/x/bank/app_test.go (L117-146)
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
```
