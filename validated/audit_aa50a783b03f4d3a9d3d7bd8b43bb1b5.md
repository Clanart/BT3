Found a real analog: the `sendNative` path of the bank precompile transfers native `usei`/wei value bypassing the `SendEnabled` admin control that the ordinary `MsgSend` path enforces.

### Title
Bank precompile `sendNative` bypasses `SendEnabled` (module "pause") checks enforced on ordinary bank sends - (File: precompiles/bank/bank.go)

### Summary
`x/bank` exposes an admin-controlled `SendEnabled` parameter per denom, functioning as a pause switch that operators use to halt transfers of a given denom (e.g. during an incident or migration) without halting the whole chain. `MsgSend`/`MsgMultiSend` enforce this via `IsSendEnabledCoins` in `msgServer.Send`/`MultiSend`, but the EVM bank precompile's `sendNative` function moves `usei`/wei value directly through `SendCoinsAndWei` without ever consulting `IsSendEnabledCoins`.

### Finding Description
`msgServer.Send` explicitly checks `k.IsSendEnabledCoins(ctx, msg.Amount...)` before executing any coin movement [1](#0-0) , and the same enforcement exists in `MultiSend` [2](#0-1) . The base denom's send-enabled flag is the mechanism by which chain operators can pause bank transfers of `usei` for that denom while other admin actions occur (e.g. param changes, migrations, incident response), analogous to `whenNotPaused` in the reference report.

The EVM bank precompile's `sendNative`, however, calls `p.bankKeeper.SendCoinsAndWei` directly, which internally only calls `SubWei`/`AddWei`/`SendCoinsWithoutAccCreation` — none of which check `IsSendEnabledCoins` — bypassing the pause entirely for the EVM native-value transfer path: [3](#0-2) [4](#0-3) 

The `IsSendEnabledCoins`/`IsSendEnabledCoin` checks are only ever invoked from `msgServer.Send`/`MultiSend`, and are not part of the `SendKeeper`'s `SendCoins`/`SendCoinsAndWei` internal logic, so any caller that reaches the keeper directly (rather than through the Cosmos `MsgSend` message path) skips the pause: [5](#0-4) .

Note: the `send` (ERC20-pointer) method of the same precompile does route through `p.bankMsgServer.Send`, so it correctly enforces `IsSendEnabledCoins` [6](#0-5) . Only `sendNative` (native `usei`/wei transfers triggered by sending EVM `value` to the bank precompile) is affected.

### Impact Explanation
If an operator disables sending for the base denom (`usei`) via bank params — e.g. to freeze transfers during an incident, exploit response, or a coordinated state migration — any EVM user can still move funds by calling the bank precompile's `sendNative` (or any contract forwarding native value through it), completely circumventing the pause. This defeats the purpose of the `SendEnabled` control and allows continued fund movement exactly when the chain operators intended to halt it, which can facilitate fund exfiltration mid-incident or interfere with a state migration/param change that assumes transfers are halted.

### Likelihood Explanation
Likelihood is moderate: `SendEnabled=false` for the base denom is not a common steady-state configuration, but it is the documented mechanism operators would reach for during an incident — precisely the scenario in which bypassing it is most damaging. Any EVM account can trigger `sendNative` by sending a transaction with non-zero `value` to the bank precompile address, requiring no special privilege.

### Recommendation
Add an `IsSendEnabledCoins` (or equivalent) check for the base denom inside `sendNative` in `precompiles/bank/bank.go` before calling `SendCoinsAndWei`, mirroring the check already performed in `msgServer.Send`. More robustly, move the `SendEnabled` check into `BaseSendKeeper.SendCoins`/`SendCoinsAndWei` themselves so that every caller of the keeper (message server, precompiles, or future integrations) is uniformly subject to the pause, rather than relying on each caller to remember to check it.

### Proof of Concept
1. Chain governance/admin sets bank params such that `SendEnabled` is `false` for `usei` (the base denom).
2. A user submits a normal `MsgSend` of `usei` — this correctly fails with `ErrSendDisabled` via `IsSendEnabledCoins` in `msgServer.Send`.
3. The same user instead sends an EVM transaction with non-zero `value` to the bank precompile address (triggering `sendNative`) with a valid Sei bech32 recipient argument.
4. `sendNative` calls `HandlePaymentUseiWei` then `p.bankKeeper.SendCoinsAndWei`, which moves the `usei`/wei balance without ever consulting `IsSendEnabledCoins` — the transfer succeeds despite `SendEnabled=false`, proving the pause bypass. [7](#0-6)

### Citations

**File:** sei-cosmos/x/bank/keeper/msg_server.go (L26-31)
```go
func (k msgServer) Send(goCtx context.Context, msg *types.MsgSend) (*types.MsgSendResponse, error) {
	ctx := sdk.UnwrapSDKContext(goCtx)

	if err := k.IsSendEnabledCoins(ctx, msg.Amount...); err != nil {
		return nil, err
	}
```

**File:** sei-cosmos/x/bank/keeper/msg_server.go (L74-81)
```go
func (k msgServer) MultiSend(goCtx context.Context, msg *types.MsgMultiSend) (*types.MsgMultiSendResponse, error) {
	ctx := sdk.UnwrapSDKContext(goCtx)
	denomToAllowListCache := make(map[string]AllowedAddresses)
	// NOTE: totalIn == totalOut should already have been checked
	for _, in := range msg.Inputs {
		if err := k.IsSendEnabledCoins(ctx, in.Coins...); err != nil {
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

**File:** precompiles/bank/bank.go (L280-292)
```go
	usei, wei, err := pcommon.HandlePaymentUseiWei(ctx, p.evmKeeper.GetSeiAddressOrDefault(ctx, p.address), senderSeiAddr, value, p.bankKeeper, p.evmKeeper, hooks, evm.GetDepth())
	if err != nil {
		return nil, 0, err
	}

	if err := p.bankKeeper.SendCoinsAndWei(ctx, senderSeiAddr, receiverSeiAddr, usei, wei); err != nil {
		return nil, 0, err
	}
	accExists := p.accountKeeper.HasAccount(ctx, receiverSeiAddr)
	if !accExists {
		defer metrics.RecordBankNewAccount(ctx.Context())
		p.accountKeeper.SetAccount(ctx, p.accountKeeper.NewAccountWithAddress(ctx, receiverSeiAddr))
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
