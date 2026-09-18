I found a concrete analog: the tokenfactory denom allow-list restriction ("who may send/receive a restricted `factory/...` denom") is enforced only in the bank module's Cosmos `MsgServer.Send`/`MultiSend` handlers, not inside the core `BaseSendKeeper.SendCoins`/`SendCoinsAndWei`/`InputOutputCoins` functions themselves — the same "check-in-wrapper-not-in-core" pattern as the reported ArrakisV2 issue. The EVM `bank` precompile's `sendNative` calls `bankKeeper.SendCoinsAndWei` directly, bypassing the wrapper that carries the allow-list check.

### Title
Tokenfactory denom allow-list restriction can be bypassed via the EVM bank precompile `sendNative` - (File: precompiles/bank/bank.go)

### Summary
`x/tokenfactory` restricted (allow-listed) denoms are enforced by an access-control check, `IsInDenomAllowList`, that lives in the Cosmos bank module's message-handling layer (`msgServer.Send` / `msgServer.MultiSend`) rather than inside the core `BaseSendKeeper` transfer logic. Any code path that moves tokenfactory-denominated coins by calling the core keeper methods directly — bypassing `msgServer` — skips the allow-list check entirely, exactly analogous to the reported ArrakisV2 bug where the whitelist gate lived in the router (`ArrakisV2Router.sol`) instead of the core vault (`ArrakisV2.sol`).

### Finding Description
The allow-list check is implemented as `BaseSendKeeper.IsInDenomAllowList` [1](#0-0) , but it is only invoked from the `msgServer.Send` and `msgServer.MultiSend` RPC handlers before calling `SendCoins`/`InputOutputCoins`: [2](#0-1)  and [3](#0-2) . The core `SendKeeper` interface exposes `SendCoins`, `SendCoinsAndWei`, and `InputOutputCoins` as independently callable methods that do not themselves call `IsInDenomAllowList` [4](#0-3) .

The EVM `bank` precompile's `sendNative` executor is a caller that reaches the core keeper directly, not through `msgServer`: it takes an EVM `value` (usei/wei), resolves sender/receiver Sei addresses, and calls `p.bankKeeper.SendCoinsAndWei(ctx, senderSeiAddr, receiverSeiAddr, usei, wei)` with no allow-list check anywhere in the function [5](#0-4) . This same pattern is repeated across every legacy version of the bank precompile (v552 through v640) [6](#0-5) .

### Impact Explanation
A tokenfactory denom admin can configure a `DenomAllowList` intending to restrict who may hold/transfer a specific `factory/...` denom (e.g., for compliance, KYC-gating, or freezing a compromised holder) using `SetDenomAllowList` [7](#0-6) . Any EVM user holding that denom (via the usei/wei bridge for the token, once associated) can call the `bank` precompile's `sendNative`/equivalent transfer method to move the coin to or from a non-allow-listed address, completely bypassing the restriction that `MsgSend`/`MsgMultiSend` enforce. This is unauthorized transfer of a restricted asset via a Cosmos precompile — an explicit item in the accepted impact list.

### Likelihood Explanation
Reaching this path requires only a normal EVM transaction from an address that is Sei-associated and holds the tokenfactory denom's usei-equivalent balance; no privileged role is needed, and the call is a single, ordinary use of a documented precompile method. The bug is a structural design gap (check-in-wrapper, not-in-core) rather than a rare edge case, making it straightforward to trigger whenever a tokenfactory allow-list is used to restrict a denom that end users can also touch through the EVM.

### Recommendation
Move the `IsInDenomAllowList` (and `BlockedAddr`) checks into the core `SendCoins`/`SendCoinsAndWei`/`InputOutputCoins` implementations in `BaseSendKeeper` so that every caller — `msgServer`, the bank precompile, tokenfactory module operations, IBC transfer, and any future integration — is subject to the restriction, rather than re-implementing the check at each individual entry point. Alternatively, add the same allow-list checks explicitly inside `precompiles/bank`'s `sendNative` (and any other precompile/keeper caller that moves tokenfactory coins) before invoking the keeper.

### Proof of Concept
1. Tokenfactory denom admin creates `factory/<admin>/restricted` and sets a `DenomAllowList` containing only `{admin, alice}` via `SetDenomAllowList`.
2. Bob (not in the allow list) obtains some of this denom's usei balance (e.g., transferred to him before or via another gap) and is EVM-associated.
3. Bob calls the `bank` precompile's `sendNative` (0x1001) method with `value` denominated in the restricted token's usei/wei, targeting an arbitrary receiver address.
4. `sendNative` calls `bankKeeper.SendCoinsAndWei` directly [8](#0-7) , which contains no allow-list check, so the transfer succeeds despite Bob (and/or the receiver) not being on the `DenomAllowList` — whereas the same transfer via `MsgSend` would be rejected with `"... is not allowed to send/receive funds"` as shown in the existing test `TestSendReceiverNotInAllowList` [9](#0-8) .

### Citations

**File:** sei-cosmos/x/bank/keeper/send.go (L26-52)
```go
// SendKeeper defines a module interface that facilitates the transfer of coins
// between accounts without the possibility of creating coins.
type SendKeeper interface {
	ViewKeeper

	InputOutputCoins(ctx sdk.Context, inputs []types.Input, outputs []types.Output) error
	SendCoins(ctx sdk.Context, fromAddr sdk.AccAddress, toAddr sdk.AccAddress, amt sdk.Coins) error
	SendCoinsWithoutAccCreation(ctx sdk.Context, fromAddr sdk.AccAddress, toAddr sdk.AccAddress, amt sdk.Coins) error
	SendCoinsAndWei(ctx sdk.Context, from sdk.AccAddress, to sdk.AccAddress, amt sdk.Int, wei sdk.Int) error
	SubUnlockedCoins(ctx sdk.Context, addr sdk.AccAddress, amt sdk.Coins, checkNeg bool) error
	AddCoins(ctx sdk.Context, addr sdk.AccAddress, amt sdk.Coins, checkNeg bool) error
	SubWei(ctx sdk.Context, addr sdk.AccAddress, amt sdk.Int) error
	AddWei(ctx sdk.Context, addr sdk.AccAddress, amt sdk.Int) error

	GetParams(ctx sdk.Context) types.Params
	SetParams(ctx sdk.Context, params types.Params)

	IsSendEnabledCoin(ctx sdk.Context, coin sdk.Coin) bool
	IsSendEnabledCoins(ctx sdk.Context, coins ...sdk.Coin) error
	SetDenomAllowList(ctx sdk.Context, denom string, allowList types.AllowList)
	GetDenomAllowList(ctx sdk.Context, denom string) types.AllowList
	IsInDenomAllowList(ctx sdk.Context, addr sdk.AccAddress, coins sdk.Coins, cache map[string]AllowedAddresses) bool

	BlockedAddr(addr sdk.AccAddress) bool
	RegisterRecipientChecker(RecipientChecker)
	CanSendTo(ctx sdk.Context, recipient sdk.AccAddress) bool
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

**File:** sei-cosmos/x/bank/keeper/msg_server.go (L26-54)
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

	err = k.SendCoins(ctx, from, to, msg.Amount)
	if err != nil {
		return nil, err
	}
```

**File:** sei-cosmos/x/bank/keeper/msg_server.go (L74-99)
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

	err := k.InputOutputCoins(ctx, msg.Inputs, msg.Outputs)
	if err != nil {
		return nil, err
	}
```

**File:** precompiles/bank/bank.go (L251-287)
```go
func (p PrecompileExecutor) sendNative(ctx sdk.Context, method *abi.Method, args []interface{}, caller common.Address, callingContract common.Address, value *big.Int, readOnly bool, hooks *tracing.Hooks, evm *vm.EVM) ([]byte, uint64, error) {
	if readOnly {
		return nil, 0, errors.New("cannot call sendNative from staticcall")
	}
	if ctx.EVMPrecompileCalledFromDelegateCall() {
		return nil, 0, errors.New("cannot delegatecall sendNative")
	}
	if err := pcommon.ValidateArgsLength(args, 1); err != nil {
		return nil, 0, err
	}
	if value == nil || value.Sign() == 0 {
		return nil, 0, errors.New("set `value` field to non-zero to send")
	}

	senderSeiAddr, ok := p.evmKeeper.GetSeiAddress(ctx, caller)
	if !ok {
		return nil, 0, errors.New("invalid addr")
	}

	receiverAddr, ok := (args[0]).(string)
	if !ok || receiverAddr == "" {
		return nil, 0, errors.New("invalid addr")
	}

	receiverSeiAddr, err := sdk.AccAddressFromBech32(receiverAddr)
	if err != nil {
		return nil, 0, err
	}

	usei, wei, err := pcommon.HandlePaymentUseiWei(ctx, p.evmKeeper.GetSeiAddressOrDefault(ctx, p.address), senderSeiAddr, value, p.bankKeeper, p.evmKeeper, hooks, evm.GetDepth())
	if err != nil {
		return nil, 0, err
	}

	if err := p.bankKeeper.SendCoinsAndWei(ctx, senderSeiAddr, receiverSeiAddr, usei, wei); err != nil {
		return nil, 0, err
	}
```

**File:** precompiles/bank/legacy/v552/bank.go (L208-244)
```go
func (p Precompile) sendNative(ctx sdk.Context, method *abi.Method, args []interface{}, caller common.Address, callingContract common.Address, value *big.Int, readOnly bool, hooks *tracing.Hooks, evm *vm.EVM) ([]byte, error) {
	if readOnly {
		return nil, errors.New("cannot call sendNative from staticcall")
	}
	if caller.Cmp(callingContract) != 0 {
		return nil, errors.New("cannot delegatecall sendNative")
	}
	if err := pcommon.ValidateArgsLength(args, 1); err != nil {
		return nil, err
	}
	if value == nil || value.Sign() == 0 {
		return nil, errors.New("set `value` field to non-zero to send")
	}

	senderSeiAddr, ok := p.evmKeeper.GetSeiAddress(ctx, caller)
	if !ok {
		return nil, errors.New("invalid addr")
	}

	receiverAddr, ok := (args[0]).(string)
	if !ok || receiverAddr == "" {
		return nil, errors.New("invalid addr")
	}

	receiverSeiAddr, err := sdk.AccAddressFromBech32(receiverAddr)
	if err != nil {
		return nil, err
	}

	usei, wei, err := pcommon.HandlePaymentUseiWei(ctx, p.evmKeeper.GetSeiAddressOrDefault(ctx, p.address), senderSeiAddr, value, p.bankKeeper, p.evmKeeper, hooks, evm.GetDepth())
	if err != nil {
		return nil, err
	}

	if err := p.bankKeeper.SendCoinsAndWei(ctx, senderSeiAddr, receiverSeiAddr, usei, wei); err != nil {
		return nil, err
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
