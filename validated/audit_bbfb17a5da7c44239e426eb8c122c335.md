### Title
`bank` precompile `sendNative`/EVM native transfers bypass `SendEnabled`/allow-list pause checks enforced by `MsgSend` - (File: sei-cosmos/x/bank/keeper/send.go, precompiles/bank/bank.go)

### Summary
`MsgSend` (and `MsgMultiSend`) enforce `IsSendEnabledCoins` and `IsInDenomAllowList`/`BlockedAddr` checks before moving funds, giving governance a way to pause outbound transfers of a given denom. `SendCoinsAndWei`, which underlies native `usei`/wei transfers reachable from the EVM (including the bank precompile's native-transfer path and EVM value transfers), performs no such checks, so this class of fund movement cannot be paused the same way `MsgSend` can.

### Finding Description
`msgServer.Send` explicitly calls `k.IsSendEnabledCoins(ctx, msg.Amount...)` and `k.IsInDenomAllowList`/`BlockedAddr` before calling `SendCoins`, so operators can disable transfers of a denom via `SendEnabled` params. [1](#0-0) 

However, `BaseSendKeeper.SendCoinsAndWei`, the function used for combined `usei`+wei transfers (the native currency bridge used by the EVM), only manipulates wei/usei balances directly and never calls `IsSendEnabledCoins` or the allow-list check: [2](#0-1) 

This is exposed as an interface method available to callers such as the EVM state DB bridge and precompiles: [3](#0-2) 

By contrast, `IsSendEnabledCoins`/`IsSendEnabledCoin` exist specifically to let the chain pause sending of a denom: [4](#0-3) 

Because EVM-originated value transfers and the `bank` precompile's native transfer path move funds through `SendCoinsAndWei` (or equivalent wei-balance mutation) rather than through `msgServer.Send`, they never consult `SendEnabled`, `BlockedAddr`, or the denom allow-list. This mirrors the reported bug class exactly: a fund-moving path structurally analogous to other paths that have an explicit "is this allowed" gate, but which itself lacks the gate — meaning the protocol cannot pause it even though it can pause the sibling `MsgSend` path.

### Impact Explanation
If the base denom (`usei`) or another asset moved via the wei bridge is put in a paused/disabled state (e.g., during an incident, exploit response, or a legal freeze requirement) via `SendEnabled=false`, users can still move funds using EVM transactions (plain value transfers or the bank precompile), because that path never checks `IsSendEnabledCoins`. This defeats the chain-level pause mechanism for outbound value transfers of `usei`, allowing continued fund movement/outflow during a period when the operators specifically intended to halt it. This is a fund-movement control bypass, not merely an informational gap, since it directly nullifies an existing security control (`SendEnabled`) for an entire class of user-triggered transactions (EVM transfers).

### Likelihood Explanation
Any unprivileged EVM user can trigger this by simply sending a normal EVM value-transfer transaction or calling the bank precompile's native send method; no special privilege, precompile whitelisting, or contract deployment is required. The condition is only exploitable/relevant once governance actually disables sending for the base denom, but at that moment the bypass is trivially and universally reachable by every EVM account holder.

### Recommendation
Route native `usei`/wei transfers originating from the EVM (both plain value transfers processed through the state DB and the `bank` precompile's native-send path) through the same `IsSendEnabledCoins`, `BlockedAddr`, and denom-allow-list checks that `msgServer.Send` performs, or add an equivalent check inside `SendCoinsAndWei` before it mutates balances, so that disabling `SendEnabled` for the base denom also halts EVM-originated transfers.

### Proof of Concept
1. Governance (or an emergency param-change process) sets `SendEnabled` to `false` for `usei` via the bank module's params, intending to halt all outbound transfers of the native token.
2. A user submits a standard `MsgSend` transaction moving `usei` — this correctly fails with `ErrSendDisabled` because `msgServer.Send` calls `IsSendEnabledCoins`.
3. The same user instead submits a plain EVM transaction with a nonzero `value` field (a native coin transfer) to another EVM address, or calls the `bank` precompile's native send method.
4. Internally this is processed via the wei/usei balance-mutation path (`SendCoinsAndWei`/`AddWei`/`SubWei`), which contains no `IsSendEnabledCoins` or allow-list check, so the transfer succeeds and funds move despite the pause — demonstrating the bypass.

### Citations

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

**File:** sei-cosmos/x/bank/keeper/send.go (L28-52)
```go
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

**File:** sei-cosmos/x/bank/keeper/send.go (L440-459)
```go
func (k BaseSendKeeper) SendCoinsAndWei(ctx sdk.Context, from sdk.AccAddress, to sdk.AccAddress, amt sdk.Int, wei sdk.Int) error {
	if err := k.SubWei(ctx, from, wei); err != nil {
		return err
	}
	if err := k.AddWei(ctx, to, wei); err != nil {
		return err
	}
	ctx.EventManager().EmitEvents(sdk.Events{
		sdk.NewEvent(
			types.EventTypeWeiTransfer,
			sdk.NewAttribute(types.AttributeKeyRecipient, to.String()),
			sdk.NewAttribute(types.AttributeKeySender, from.String()),
			sdk.NewAttribute(sdk.AttributeKeyAmount, wei.String()),
		),
	})
	if amt.GT(sdk.ZeroInt()) {
		return k.SendCoinsWithoutAccCreation(ctx, from, to, sdk.NewCoins(sdk.NewCoin(sdk.MustGetBaseDenom(), amt)))
	}
	return nil
}
```
