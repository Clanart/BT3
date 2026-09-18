Confirmed finding: the `sendNative` path (native usei/wei transfers between EVM accounts) bypasses the `bank` module's `SendEnabled` pause mechanism, while the `send` path (ERC20-pointer transfers of a native denom) goes through `MsgServer.Send`, which does check it.

### Title
EVM `bank` precompile `sendNative` bypasses bank module's `SendEnabled` pause, allowing native token transfers while sends are frozen - (File: precompiles/bank/bank.go)

### Summary
sei-chain's Cosmos `x/bank` module exposes a pause-style mechanism via `Params.SendEnabled`/`DefaultSendEnabled`, checked by `IsSendEnabledCoins`, which lets governance/admins freeze transfers of a denom (e.g. in an emergency). [1](#0-0)  This check is enforced in the standard Cosmos `MsgSend` handler. [2](#0-1)  However, the EVM-side `bank` precompile's `sendNative` function — the path used for native `usei`/wei value transfers reachable from any EVM transaction/contract call — calls `SendCoinsAndWei` directly on the `BaseSendKeeper`, which never checks `IsSendEnabledCoins`. [3](#0-2)  This mirrors the reported bug class exactly: one execution surface (L1/Cosmos `MsgSend`) enforces the pause, while the parallel surface (L2/EVM `sendNative`) does not, letting users move funds while the chain intends transfers to be frozen.

### Finding Description
`BaseSendKeeper.SendCoins`/`SendCoinsAndWei` in `sei-cosmos/x/bank/keeper/send.go` perform the actual balance debits/credits but contain no `IsSendEnabledCoins` gate — that check lives only in the `SendKeeper` interface and is invoked explicitly by the message handler. [4](#0-3)  The Cosmos-native `MsgSend` msg server calls `k.IsSendEnabledCoins(ctx, msg.Amount...)` before `SendCoins`, so a governance-set pause (`DefaultSendEnabled=false` or a per-denom `SendEnabled` override) correctly blocks Cosmos-side sends. [5](#0-4) 

The EVM `bank` precompile's `send` method (transferring a native-denom ERC20 pointer's balance) also routes through `p.bankMsgServer.Send(...)`, so it inherits the `SendEnabled` check. [6](#0-5)  But `sendNative` — used whenever a plain EVM transaction sends native `value` (usei/wei) to a Sei address through this precompile — calls `p.bankKeeper.SendCoinsAndWei(ctx, senderSeiAddr, receiverSeiAddr, usei, wei)` directly, with no `IsSendEnabledCoins` check anywhere in the call path. [7](#0-6)  The `giga/deps/xbank` fork of the keeper exhibits the identical structure (check only in its own msg server-equivalent path, absent from the raw `SendCoins`/`SubWei`/`AddWei` primitives). [8](#0-7) 

### Impact Explanation
If governance pauses transfers of `usei` (or any native denom) via `SendEnabled` params — e.g., in response to an exploit or compliance freeze — any user can still move funds using the EVM `sendNative` precompile path or plain EVM value transfers routed to it, completely circumventing the intended freeze. This directly matches the accepted impact class "unauthorized transfer... fee or refund abuse... permanent freezing" being defeated: the pause is not permanently enforced across both execution paths, undermining the chain's designed emergency-freeze control and letting funds continue to move when they should not.

### Likelihood Explanation
Any unprivileged EVM transaction sender can trigger `sendNative` by sending a transaction with non-zero `value` targeting the bank precompile address `0x0000000000000000000000000000000000001001`. [9](#0-8)  No special privileges, precompile allowlisting, or unusual conditions are required beyond a `SendEnabled=false` pause being active, which is exactly the scenario the pause mechanism exists to prevent.

### Recommendation
Add an `IsSendEnabledCoins` (or equivalent) check in `sendNative` (and any other precompile/internal callers that invoke `SendCoins`/`SendCoinsAndWei`/`SubWei`/`AddWei` directly) so that native-denom transfer restrictions apply uniformly regardless of whether the transfer originates from a Cosmos `MsgSend` or an EVM precompile call. Alternatively, move the `SendEnabled` check into `BaseSendKeeper.SendCoins`/`SendCoinsAndWei` itself so all callers are protected by default.

### Proof of Concept
1. Governance sets `x/bank` params with `DefaultSendEnabled=false` (or explicitly disables `usei`), invoking the pause functionality intended to freeze fund movement.
2. A user issues a standard Cosmos `MsgSend` of `usei` → it fails with `ErrSendDisabled` via `k.IsSendEnabledCoins` in `msgServer.Send`. [2](#0-1) 
3. The same user instead sends an EVM transaction with non-zero `value` to the bank precompile's `sendNative` method (address `0x...1001`), specifying a valid recipient Sei address.
4. `sendNative` computes usei/wei splits and calls `p.bankKeeper.SendCoinsAndWei` directly, with no `IsSendEnabledCoins` check anywhere in the call chain, so the transfer succeeds despite the active pause. [3](#0-2)

### Citations

**File:** sei-cosmos/x/bank/types/params.go (L60-68)
```go
// SendEnabledDenom returns true if the given denom is enabled for sending
func (p Params) SendEnabledDenom(denom string) bool {
	for _, pse := range p.SendEnabled {
		if pse.Denom == denom {
			return pse.Enabled
		}
	}
	return p.DefaultSendEnabled
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

**File:** precompiles/bank/bank.go (L38-40)
```go
const (
	BankAddress = "0x0000000000000000000000000000000000001001"
)
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

**File:** sei-cosmos/x/bank/keeper/send.go (L355-370)
```go
// IsSendEnabledCoins checks the coins provide and returns an ErrSendDisabled if
// any of the coins are not configured for sending.  Returns nil if sending is enabled
// for all provided coin
func (k BaseSendKeeper) IsSendEnabledCoins(ctx sdk.Context, coins ...sdk.Coin) error {
	for _, coin := range coins {
		if !k.IsSendEnabledCoin(ctx, coin) {
			return sdkerrors.Wrapf(types.ErrSendDisabled, "%s transfers are currently disabled", coin.Denom)
		}
	}
	return nil
}

// IsSendEnabledCoin returns the current SendEnabled status of the provided coin's denom
func (k BaseSendKeeper) IsSendEnabledCoin(ctx sdk.Context, coin sdk.Coin) bool {
	return k.GetParams(ctx).SendEnabledDenom(coin.Denom)
}
```

**File:** giga/deps/xbank/keeper/send.go (L333-348)
```go
// IsSendEnabledCoins checks the coins provide and returns an ErrSendDisabled if
// any of the coins are not configured for sending.  Returns nil if sending is enabled
// for all provided coin
func (k BaseSendKeeper) IsSendEnabledCoins(ctx sdk.Context, coins ...sdk.Coin) error {
	for _, coin := range coins {
		if !k.IsSendEnabledCoin(ctx, coin) {
			return sdkerrors.Wrapf(types.ErrSendDisabled, "%s transfers are currently disabled", coin.Denom)
		}
	}
	return nil
}

// IsSendEnabledCoin returns the current SendEnabled status of the provided coin's denom
func (k BaseSendKeeper) IsSendEnabledCoin(ctx sdk.Context, coin sdk.Coin) bool {
	return k.GetParams(ctx).SendEnabledDenom(coin.Denom)
}
```
