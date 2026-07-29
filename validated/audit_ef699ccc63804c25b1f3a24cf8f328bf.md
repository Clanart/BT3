Found a direct analog: in `ConvertCoinToERC20FromPacket` (`x/erc20/keeper/ibc_callbacks.go`), the **`OnAcknowledgementPacket`** and **`OnTimeoutPacket`** paths that convert refunded IBC coins back into their native-ERC20 representation call `k.ConvertCoinNativeERC20(ctx, pair, ...)` directly, bypassing `k.MintingEnabled(ctx, receiver, denom)` — the same gate function that `ConvertERC20`/`ConvertCoin` (msg-server paths) and the `OnRecvPacket` native-ERC20 case (Case 2) always call first.

### Title
Refund-path ERC20 conversion bypasses `MintingEnabled` governance/bank gating checks - (File: x/erc20/keeper/ibc_callbacks.go)

### Summary
`ConvertCoinNativeERC20` is the state-mutating function that escrows coins, unescrows/mints ERC20 tokens, and burns escrowed coins. Every other caller of this function (`ConvertCoin` in `x/erc20/keeper/msg_server.go:222`, and `OnRecvPacket`'s native-ERC20 branch in `x/erc20/keeper/ibc_callbacks.go:137`) first calls `k.MintingEnabled(ctx, receiver, denom)` [1](#0-0) [2](#0-1) . This function enforces: `IsERC20Enabled` (global governance kill-switch), `pair.Enabled` (per-token-pair governance kill-switch), `BlockedAddr` (recipient not on the bank module block-list), and `IsSendEnabledCoin` (coin sends not disabled) [3](#0-2) . However, `ConvertCoinToERC20FromPacket` — invoked from `OnAcknowledgementPacket` on packet failure and unconditionally from `OnTimeoutPacket` — calls `k.ConvertCoinNativeERC20` directly at line 237, checking only `params.EnableErc20` and `IsDenomRegistered`, never `pair.Enabled`, `BlockedAddr`, or `IsSendEnabledCoin` [4](#0-3) .

### Finding Description
The intended invariant (as encoded by `MintingEnabled`'s doc comment) is that ERC20 minting/conversion for a token pair must always pass through the same set of gates: global ERC20-enabled, per-pair enabled, blocked-address check, and bank send-enabled check [5](#0-4) . This mirrors the `fastTrackProposalExecution`/`whenPaused` pattern: a privileged/automatic code path was supposed to respect the same guard as the normal path, but the guard modifier/check was omitted on one call site.

Here, the omitted check is on the IBC-timeout/failed-acknowledgement refund path (`ConvertCoinToERC20FromPacket`), which is triggered automatically by relayers delivering a timeout or error acknowledgement for an outbound ICS20 transfer of a native-ERC20-backed coin. If governance disables a specific token pair (`pair.Enabled = false`, e.g. in response to a discovered vulnerability in that ERC20 contract, or to freeze conversions), or if the bank module disables sends for that denom (`SetSendEnabled(false)`), or if the sender address is later added to the bank blocked-address list, the refund path still executes `ConvertCoinNativeERC20`, unescrowing/minting native-ERC20 tokens to that address, bypassing all three of those governance/safety controls.

### Impact Explanation
This does not directly mint tokens out of thin air (the coins were already escrowed by the original outbound transfer), but it defeats the deliberate governance/safety pause mechanism (`pair.Enabled`, `IsSendEnabledCoin`, `BlockedAddr`) that `MintingEnabled` centralizes for exactly this purpose. An operator who has disabled a token pair or blocked an address to halt fund movement (e.g., during an active exploit or sanctions enforcement) can be bypassed by simply timing out or failing an IBC transfer, causing the pause/freeze to be silently circumvented and escrowed value to be paid out via ERC20 to an address the governance/bank layer intended to block. Given the required Critical-impact gate, this is a partial analog: it does not itself create new value, but it is a genuine unauthorized bypass of a fund-freezing control on user value, potentially allowing extraction to addresses that should be frozen.

### Likelihood Explanation
Reaching this code path is fully unprivileged from the perspective of the acting relayer/user: any account can initiate an IBC transfer of a native-ERC20-backed token and then let the packet time out (or have the counterparty return an error acknowledgement) to trigger `OnTimeoutPacket`/`OnAcknowledgementPacket`. The governance action of disabling the pair or blocking the address is itself privileged, but the bypass of that governance intent requires no special privilege from the attacker — this matches the disclosed pattern where the missing check is exploitable by anyone once the "paused" condition is asserted by the operator.

### Recommendation
In `ConvertCoinToERC20FromPacket` (`x/erc20/keeper/ibc_callbacks.go`), replace the manual `params.EnableErc20`/`IsDenomRegistered` check with a call to `k.MintingEnabled(ctx, sender, coin.Denom)` before invoking `k.ConvertCoinNativeERC20`, mirroring the checks already performed in `ConvertCoin` and `OnRecvPacket`. If `MintingEnabled` fails, treat it the same way the existing conversion failure is handled (emit `EventTypeFailedConvertERC20` and no-op, leaving the coin in the account's bank balance) so refunds are not lost, only the auto-conversion to ERC20 is skipped.

### Proof of Concept
1. Governance registers `denom` as a native-ERC20 token pair and it is enabled.
2. A user converts `denom` coins to the ERC20 representation and sends the ERC20 tokens out via ICS20 (escrowing coins for the outbound transfer).
3. Governance discovers an issue with the ERC20 contract for `denom` (or wants to halt conversions) and disables the pair via `ToggleConversion`, setting `pair.Enabled = false` [6](#0-5) , or the bank module sets `SetSendEnabled(denom, false)`, or blocks the sender's address.
4. The counterparty chain returns a timeout or error acknowledgement for the earlier outbound transfer.
5. `OnTimeoutPacket`/`OnAcknowledgementPacket` → `ConvertCoinToERC20FromPacket` executes, checking only `params.EnableErc20` (global, still true) and `IsDenomRegistered` (still true) — not `pair.Enabled`, not `BlockedAddr`, not `IsSendEnabledCoin` — and calls `ConvertCoinNativeERC20`, converting the refunded coins back to ERC20 and delivering them to the sender despite the pair being disabled/blocked [7](#0-6) .

Note: I was unable to fully trace whether `pair.Enabled = false` also disables `BalanceOf`/EVM-level transfer calls inside `ConvertCoinNativeERC20` at a lower layer (which could make this a no-op in practice) — this would need to be verified with a running test/dynamic trace, which is beyond the scope of static index-based review. If such a lower-layer guard exists and always reverts the EVM call when the pair is disabled, this finding would be neutralized to a lesser (non-Critical) bypass of only the `BlockedAddr`/`IsSendEnabledCoin` checks.

### Citations

**File:** x/erc20/keeper/msg_server.go (L202-222)
```go
	pair, err := k.MintingEnabled(ctx, receiver.Bytes(), msg.Coin.Denom)
	if err != nil {
		return nil, err
	}

	// Check ownership and execute conversion
	switch {
	case pair.IsNativeERC20():
		// Remove token pair if contract is suicided
		acc := k.evmKeeper.GetAccountWithoutBalance(ctx, pair.GetERC20Contract())
		if acc == nil || !acc.HasCodeHash() {
			k.DeleteTokenPair(ctx, pair)
			k.Logger(ctx).Debug(
				"deleting selfdestructed token pair from state",
				"contract", pair.Erc20Address,
			)
			// NOTE: return nil error to persist the changes from the deletion
			return nil, nil
		}

		return nil, k.ConvertCoinNativeERC20(ctx, pair, msg.Coin.Amount, receiver, sender)
```

**File:** x/erc20/keeper/msg_server.go (L368-393)
```go
func (k *Keeper) ToggleConversion(goCtx context.Context, req *types.MsgToggleConversion) (*types.MsgToggleConversionResponse, error) {
	ctx := sdk.UnwrapSDKContext(goCtx)
	// Check if the conversion is globally enabled
	if !k.IsERC20Enabled(ctx) {
		return nil, types.ErrERC20Disabled.Wrap("toggle conversion is currently disabled by governance")
	}

	if err := k.validateAuthority(req.Authority); err != nil {
		return nil, err
	}

	pair, err := k.toggleConversion(ctx, req.Token)
	if err != nil {
		return nil, err
	}

	ctx.EventManager().EmitEvent(
		sdk.NewEvent(
			types.EventTypeToggleTokenConversion,
			sdk.NewAttribute(types.AttributeKeyCosmosCoin, pair.Denom),
			sdk.NewAttribute(types.AttributeKeyERC20Token, pair.Erc20Address),
		),
	)

	return &types.MsgToggleConversionResponse{}, nil
}
```

**File:** x/erc20/keeper/ibc_callbacks.go (L119-137)
```go
	case found && pair.IsNativeERC20():
		// Token pair is disabled -> return
		if !pair.Enabled {
			return ack
		}

		pair, err := k.MintingEnabled(ctx, recipient, coin.Denom)
		if err != nil {
			ctx.EventManager().EmitEvent(
				sdk.NewEvent("erc20_callback_failure",
					sdk.NewAttribute(types.TypeMsgConvertCoin, "mint_failure"),
					sdk.NewAttribute(types.AttributeKeyCosmosCoin, coin.Denom),
					sdk.NewAttribute(types.AttributeKeyReceiver, recipient.String()),
				),
			)
			return channeltypes.NewErrorAcknowledgement(err)
		}

		if err := k.ConvertCoinNativeERC20(ctx, pair, coin.Amount, common.BytesToAddress(recipient.Bytes()), recipient); err != nil {
```

**File:** x/erc20/keeper/ibc_callbacks.go (L216-253)
```go
	// Case 2. if pair is native ERC20 -> unescrow
	case pair.IsNativeERC20():
		// use a zero gas config to avoid extra costs for the relayers
		ctx = ctx.
			WithKVGasConfig(storetypes.GasConfig{}).
			WithTransientKVGasConfig(storetypes.GasConfig{})

		params := k.GetParams(ctx)
		if !params.EnableErc20 || !k.IsDenomRegistered(ctx, coin.Denom) {
			// no-op, ERC20s are disabled or the denom is not registered
			return nil
		}

		// assume that all module accounts on Cosmos EVM need to have their tokens in the
		// IBC representation as opposed to ERC20
		senderAcc := k.accountKeeper.GetAccount(ctx, sender)
		if types.IsModuleAccount(senderAcc) {
			return nil
		}

		// Convert from Coin to ERC20
		if err := k.ConvertCoinNativeERC20(ctx, pair, coin.Amount, common.BytesToAddress(sender), sender); err != nil {
			// We want to record only the failed attempt to reconvert the coins during IBC.
			defer func() {
				telemetry.IncrCounter(1, types.ModuleName, "ibc", "error", "total")
			}()
			ctx.EventManager().EmitEvents(
				sdk.Events{
					sdk.NewEvent(
						types.EventTypeFailedConvertERC20,
						sdk.NewAttribute(types.AttributeCoinSourceChannel, pair.Denom),
						sdk.NewAttribute(types.AttributeKeyERC20Token, pair.Erc20Address),
						sdk.NewAttribute("error", err.Error()),
					),
				},
			)
			return nil
		}
```

**File:** x/erc20/keeper/mint.go (L13-17)
```go
// MintingEnabled checks that:
//   - the global parameter for erc20 conversion is enabled
//   - minting is enabled for the given (erc20,coin) token pair
//   - recipient address is not on the blocked list
//   - bank module transfers are enabled for the Cosmos coin
```

**File:** x/erc20/keeper/mint.go (L18-67)
```go
func (k Keeper) MintingEnabled(
	ctx sdk.Context,
	receiver sdk.AccAddress,
	token string,
) (types.TokenPair, error) {
	if !k.IsERC20Enabled(ctx) {
		return types.TokenPair{}, errorsmod.Wrap(
			types.ErrERC20Disabled, "module is currently disabled by governance",
		)
	}

	id := k.GetTokenPairID(ctx, token)
	if len(id) == 0 {
		return types.TokenPair{}, errorsmod.Wrapf(
			types.ErrTokenPairNotFound, "token '%s' not registered by id", token,
		)
	}

	pair, found := k.GetTokenPair(ctx, id)
	if !found {
		return types.TokenPair{}, errorsmod.Wrapf(
			types.ErrTokenPairNotFound, "token '%s' not registered", token,
		)
	}

	if !pair.Enabled {
		return types.TokenPair{}, errorsmod.Wrapf(
			types.ErrERC20TokenPairDisabled, "minting token '%s' is not enabled by governance", token,
		)
	}

	if k.bankKeeper.BlockedAddr(receiver.Bytes()) {
		return types.TokenPair{}, errorsmod.Wrapf(
			errortypes.ErrUnauthorized, "%s is not allowed to receive transactions", receiver,
		)
	}

	// NOTE: ignore amount as only denom is checked on IsSendEnabledCoin
	coin := sdk.Coin{Denom: pair.Denom}

	// check if minting to a recipient address other than the sender is enabled
	// for for the given coin denom
	if !k.bankKeeper.IsSendEnabledCoin(ctx, coin) {
		return types.TokenPair{}, errorsmod.Wrapf(
			banktypes.ErrSendDisabled, "minting '%s' coins to an external address is currently disabled", token,
		)
	}

	return pair, nil
}
```
