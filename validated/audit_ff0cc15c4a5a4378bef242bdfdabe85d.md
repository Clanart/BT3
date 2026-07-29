### Title
Auto-conversion middleware can double-mint value on IBC receive when the underlying ERC20 rejects the transfer — ([File: x/erc20/keeper/ibc_callbacks.go])

### Summary
This is an unverified hypothesis, not a confirmed finding. I was not able to fully trace whether `ibc/module.go` / `x/erc20/ibc_middleware.go` wrap the base ICS20 transfer app's `OnRecvPacket` and this module's post-processing in a single cached/branched context before the final acknowledgement is committed. That detail is decisive for whether the pattern below is actually exploitable, and I ran out of iterations before confirming it.

### Finding Description
`x/erc20/keeper/ibc_callbacks.go`'s `OnRecvPacket` [1](#0-0)  is documented as middleware that runs "transfer after the ICS20 OnRecvPacket" — i.e. by the time this code executes, the base transfer app has already minted/unescrowed the native bank coin to the recipient's account. This module then attempts to auto-convert that bank coin into its native-ERC20 representation via `k.ConvertCoinNativeERC20`, which performs an EVM `transfer` call out of the module account to the recipient [2](#0-1) .

If that EVM `transfer` call reverts — the exact bug class from the report, where an underlying asset-representation contract has logic (pause, allow-list, cooldown-style guard, blacklist, custom hook) that can reject a transfer — `ConvertCoinNativeERC20` returns an error and the middleware returns `channeltypes.NewErrorAcknowledgement(err)` [3](#0-2) .

In standard ICS20 semantics, an error acknowledgement written on the receiving chain causes the **source** chain to refund the sender (un-escrow/re-mint the original tokens there) once it processes that acknowledgement. The critical question is whether the bank-coin mint that the base transfer app already performed on the receiving chain is rolled back when this middleware later decides to return an error acknowledgement. If the mint is not rolled back (i.e., if the whole `OnRecvPacket` middleware chain is not executed inside a single `CacheContext` that only commits on a successful final acknowledgement), the result is:
1. Recipient keeps the newly minted bank coins on the destination chain (successful, uncommitted-rollback state).
2. Source chain refunds the original sender because it sees an error acknowledgement.
3. Net effect: token duplication — both sender (refunded) and recipient (already credited) hold spendable value for the same transferred amount.

This is the same root cause shape as the WStable/sUSDe report: a supposedly atomic user-facing operation (bank mint + ERC20 conversion) is not actually atomic with respect to acknowledgement semantics, and an external asset representation's ability to revert (analogous to `ensureCooldownOff`) is what triggers the divergent, non-atomic outcome.

Similar exposure exists in `OnAcknowledgementPacket` / `OnTimeoutPacket` → `ConvertCoinToERC20FromPacket` [4](#0-3) , though there the code explicitly swallows the conversion error and leaves the user with the bank coin instead of the ERC20 (`return nil` at line 252), which is the documented, presumably safe fallback — it does NOT return an error acknowledgement, so no refund/duplication would occur through that path.

### Impact Explanation
If confirmed, this would be a critical duplication of spendable user value across native/bank balances and IBC escrow — exactly the class of impact required by the gate (unauthorized minting/duplication of spendable value, since the same transferred amount would exist both as a refund on the source chain and as a credited balance on the destination chain). This requires no privileged access — an unprivileged user only needs to register (or already have registered, since ERC20 registration can be "PermissionlessRegistration"-enabled, see `RegisterERC20` [5](#0-4) ) a native ERC20 contract whose `transfer` function can be made to revert under attacker-controlled conditions, then send an IBC transfer of that token to trigger the OnRecvPacket auto-conversion failure.

### Likelihood Explanation
Uncertain. This hinges entirely on IBC-go's/this repo's context-branching behavior for the receive-packet middleware stack, which I could not verify within the available iterations (`ibc/module.go`, `x/erc20/ibc_middleware.go`, and `x/erc20/v2/ibc_middleware.go` reference `OnRecvPacket`/`CacheContext` patterns but I did not get to read their bodies). It's plausible — and likely, given standard Cosmos SDK/IBC-go conventions — that the message handler for `MsgRecvPacket` wraps the entire callback chain (base app + all middleware) in a single branched store that commits atomically based on the final returned acknowledgement, which would fully mitigate this concern. I cannot rule this out or confirm it with the evidence gathered.

### Recommendation
A background engineer should:
1. Read `ibc/module.go`, `x/erc20/ibc_middleware.go`, and `x/erc20/v2/ibc_middleware.go` to determine exactly how `OnRecvPacket` from the base transfer app and the erc20 middleware's post-processing are sequenced with respect to context branching/commit semantics.
2. Confirm whether a `CacheContext()`/`writeCache()` pattern exists that only commits the underlying mint if the final acknowledgement (after erc20 auto-conversion) is successful.
3. If no such atomic wrapping exists, wrap the base transfer + erc20 conversion in a single cached context in `x/erc20/keeper/ibc_callbacks.go`'s `OnRecvPacket`, only committing state changes when the final acknowledgement to be written is a success; otherwise discard all changes (including the base mint) before returning the error acknowledgement.
4. Add an integration test (extending `tests/integration/x/erc20/test_ibc_callback.go`) that registers a native ERC20 whose `transfer` reverts conditionally (similar to `contractMaliciousDelayed` already used in this repo's ERC20 test fixtures) and asserts that a failed post-mint conversion during `OnRecvPacket` does not leave the recipient with spendable bank coins while the source chain also refunds the sender.

### Proof of Concept
Not constructed — this requires confirming the context-commit semantics first (see Likelihood Explanation). Once confirmed, a PoC would: (a) register a native ERC20 with a conditionally-reverting `transfer` function, (b) perform a standard ICS20 transfer of that token to a recipient on the Cosmos EVM chain, (c) make the ERC20 revert during the middleware's auto-conversion attempt, (d) show both that the recipient's bank balance for the coin remains credited and that the source chain performs a refund upon receiving the resulting error acknowledgement.

### Citations

**File:** x/erc20/keeper/ibc_callbacks.go (L35-43)
```go
func (k Keeper) OnRecvPacket(
	ctx sdk.Context,
	packet channeltypes.Packet,
	ack exported.Acknowledgement,
) exported.Acknowledgement {
	// If ERC20 module is disabled no-op
	if !k.IsERC20Enabled(ctx) {
		return ack
	}
```

**File:** x/erc20/keeper/ibc_callbacks.go (L118-139)
```go
	// Case 2. native ERC20 token
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
			return channeltypes.NewErrorAcknowledgement(err)
		}
```

**File:** x/erc20/keeper/ibc_callbacks.go (L190-257)
```go
// ConvertCoinToERC20FromPacket converts the IBC coin to ERC20 after refunding the sender
// This function is only executed when IBC timeout or an Error ACK happens.
func (k Keeper) ConvertCoinToERC20FromPacket(ctx sdk.Context, data transfertypes.FungibleTokenPacketData) error {
	// Sender is local (source) chain address; accept local bech32 or 0x-hex
	senderBz, err := k.addrCodec.StringToBytes(data.Sender)
	if err != nil {
		return err
	}
	sender := sdk.AccAddress(senderBz)

	pairID := k.GetTokenPairID(ctx, data.Denom)
	pair, found := k.GetTokenPair(ctx, pairID)
	if !found {
		// no-op, token pair is not registered
		return nil
	}

	coin := ibc.GetSentCoin(data.Denom, data.Amount)

	switch {

	// Case 1. if pair is native coin -> no-op
	case pair.IsNativeCoin():
		// no-op, received coin is a  native coin
		return nil

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
	}

	return nil
}
```

**File:** x/erc20/keeper/msg_server.go (L324-362)
```go
// RegisterERC20 implements the gRPC MsgServer interface. Any account can permissionlessly
// register a native ERC20 contract to map to a Cosmos Coin.
func (k *Keeper) RegisterERC20(goCtx context.Context, req *types.MsgRegisterERC20) (*types.MsgRegisterERC20Response, error) {
	ctx := sdk.UnwrapSDKContext(goCtx)

	params := k.GetParams(ctx)

	if !params.PermissionlessRegistration {
		if err := k.validateAuthority(req.Signer); err != nil {
			return nil, err
		}
	}

	// Check if the conversion is globally enabled
	if !k.IsERC20Enabled(ctx) {
		return nil, types.ErrERC20Disabled.Wrap("registration is currently disabled by governance")
	}

	for _, addr := range req.Erc20Addresses {
		if !common.IsHexAddress(addr) {
			return nil, errortypes.ErrInvalidAddress.Wrapf("invalid ERC20 contract address: %s", addr)
		}

		pair, err := k.registerERC20(ctx, common.HexToAddress(addr))
		if err != nil {
			return nil, err
		}

		ctx.EventManager().EmitEvent(
			sdk.NewEvent(
				types.EventTypeRegisterERC20,
				sdk.NewAttribute(types.AttributeKeyCosmosCoin, pair.Denom),
				sdk.NewAttribute(types.AttributeKeyERC20Token, pair.Erc20Address),
			),
		)
	}

	return &types.MsgRegisterERC20Response{}, nil
}
```
