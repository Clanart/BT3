## Title
Native ERC20 IBC round-trip: base ICS20 unescrow/mint commits before ERC20 conversion, and a subsequent conversion failure still emits an ErrorAcknowledgement — causing double-credit across chains - (File: `x/erc20/ibc_middleware.go`, `x/erc20/keeper/ibc_callbacks.go`)

### Summary
`erc20.IBCMiddleware.OnRecvPacket` first calls the wrapped ICS20 transfer app's `OnRecvPacket` (which mints/unescrows Cosmos coins directly to the receiver's bank balance), and only *afterwards* attempts the bank→ERC20 conversion via `keeper.OnRecvPacket`. If that conversion step fails, the middleware returns an `ErrorAcknowledgement` even though the underlying bank credit from the base transfer was never rolled back. [1](#0-0) 

### Finding Description
The transfer stack is composed as `channel.RecvPacket -> callbacks.OnRecvPacket -> erc20.OnRecvPacket -> transfer.OnRecvPacket` [2](#0-1) .

`erc20.IBCMiddleware.OnRecvPacket` runs `im.Module.OnRecvPacket` (the base ICS20 transfer logic, which performs the mint/unescrow directly on the passed-in, non-branched `ctx`) and only returns early if that ack already failed: [3](#0-2) 

If the base transfer succeeds, `k.OnRecvPacket` (the ERC20 conversion step) is invoked on the *same* `ctx` — i.e. any state written by the base transfer is already committed, not branched via `CacheContext`. In the "native ERC20" case, if `MintingEnabled` or `ConvertCoinNativeERC20` fails (e.g. `SendEnabled=false` for that denom, a reverting/self-destructed ERC20 contract, or any other conversion failure), the function returns a fresh `ErrorAcknowledgement`: [4](#0-3) 

This `ErrorAcknowledgement` becomes the final acknowledgement written to the destination chain's IBC ack store and is what gets relayed back to the source chain. On the source chain, `OnAcknowledgementPacket`/`OnTimeoutPacket` treat an error ack as "the receive failed" and refund/re-credit the original sender (unescrow or re-mint on the source side): [5](#0-4) [6](#0-5) 

But on the destination chain, the coins from the base ICS20 transfer were **already unescrowed/minted to the receiver and never reverted** — the code comment itself acknowledges this design: "If conversion fails, then the user will receive the bank token instead." The repository's own integration test explicitly demonstrates and asserts this behavior: a packet whose ERC20 conversion fails (`SendEnabled=false`) returns `errAck.Success() == false`, yet the destination-side bank credit is **not** rolled back — it is described in the test as "trapped" in the recipient/isolated account, and is checked to be present and spendable: [7](#0-6) [8](#0-7) 

Because the acknowledgement returned up through core IBC is an `Acknowledgement_Error`, standard ICS20 semantics on the source chain interpret this as "receive failed, nothing was credited" and refund the sender. Since the destination side's credit is real and not reverted, this produces a double-credit of the same transferred value: once as a retained (trapped but real, spendable) bank balance on the destination chain, and once as a refund/re-mint on the source chain.

### Impact Explanation
This breaks the fundamental IBC/ICS20 invariant that an error acknowledgement implies no value was created on the destination chain. The result is unbacked duplication of value: the same transferred amount exists simultaneously as spendable balance on the destination chain and as a refunded/re-minted balance on the source chain, corrupting the escrow/1:1 backing invariant between native coins, ERC20 views, and IBC escrows described in the audit's asset-representation pivot. This matches the "Critical unauthorized minting/duplication of spendable user value" impact category.

### Likelihood Explanation
Triggering the ERC20-conversion failure branch after a successful base transfer is realistic and does not require validator, relayer, or governance privileges beyond the ordinary ability to relay one's own packets (which any IBC user can do) and to control conditions that make `ConvertCoinNativeERC20`/`MintingEnabled` fail for a specific denom on a round trip (e.g., an attacker-deployed/owned native ERC20 whose `transfer` can be made to revert, or timing recv against a token-pair/enable-state toggle). The repo's own test suite reproduces exactly this state divergence, confirming the behavior is real and reproducible in-code, not merely theoretical.

### Recommendation
Ensure atomicity between the base ICS20 transfer effects and the ERC20 conversion step: run both under a single `CacheContext`/branch and only write state (and only allow the final acknowledgement to be a success) if the entire pipeline — base transfer mint/unescrow AND ERC20 conversion — completes successfully. Alternatively, when the ERC20 conversion step fails, do not return an `ErrorAcknowledgement`; instead return a success acknowledgement (since the ICS20 leg genuinely succeeded and the user legitimately keeps the bank coin), so the source chain does not also refund the sender.

### Proof of Concept
The repository's own test demonstrates the state divergence directly: `TestOnRecvPacketNativeErc20` ("recipient with callback" case) sets `SendEnabled=false` for the native ERC20 denom, sends `packet1`, and asserts `errAck.Success() == false` [9](#0-8) , yet after re-enabling and processing `packet2`, the test confirms a "trapped" balance from `packet1` still sits as real spendable bank coin in the isolated receiver address [10](#0-9) . In a live cross-chain deployment, this same error ack (`errAck`) would be relayed to the source chain, triggering `OnAcknowledgementPacket`/`ConvertCoinToERC20FromPacket` there to refund the original sender [11](#0-10) , while the destination-chain balance from `packet1` remains uncredited-back — a double-credit of the same value across both chains.

### Citations

**File:** x/erc20/ibc_middleware.go (L53-67)
```go
func (im IBCMiddleware) OnRecvPacket(
	ctx sdk.Context,
	channelVersion string,
	packet channeltypes.Packet,
	relayer sdk.AccAddress,
) exported.Acknowledgement {
	ack := im.Module.OnRecvPacket(ctx, channelVersion, packet, relayer)

	// return if the acknowledgement is an error ACK
	if !ack.Success() {
		return ack
	}

	return im.keeper.OnRecvPacket(ctx, packet, ack)
}
```

**File:** evmd/app.go (L496-509)
```go
	/*
		Create Transfer Stack

		transfer stack contains (from bottom to top):
			- IBC Callbacks Middleware (with EVM ContractKeeper)
			- ERC-20 Middleware
			- IBC Transfer

		SendPacket, since it is originating from the application to core IBC:
		 	transferKeeper.SendPacket ->  erc20.SendPacket -> callbacks.SendPacket -> channel.SendPacket

		RecvPacket, message that originates from core IBC and goes down to app, the flow is the other way
			channel.RecvPacket -> callbacks.OnRecvPacket -> erc20.OnRecvPacket -> transfer.OnRecvPacket
	*/
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

**File:** x/erc20/keeper/ibc_callbacks.go (L156-178)
```go
// OnAcknowledgementPacket responds to the success or failure of a packet
// acknowledgement written on the receiving chain. If the acknowledgement was a
// success then nothing occurs. If the acknowledgement failed, then the sender
// is refunded and then the IBC Coins are converted to ERC20.
// If the ERC20 conversion fails for whatever reason, such as an attempt to call
// a self-destructed ERC20 contract or an invalid function, OnAcknowledgementPacket
// still succeeds, but the user receives the corresponding bank token from the
// TokenPair instead. A user may then manually re-attempt the conversion.
func (k Keeper) OnAcknowledgementPacket(
	ctx sdk.Context, _ channeltypes.Packet,
	data transfertypes.FungibleTokenPacketData,
	ack channeltypes.Acknowledgement,
) error {
	switch ack.Response.(type) {
	case *channeltypes.Acknowledgement_Error:
		// convert the token from Cosmos Coin to its ERC20 representation
		return k.ConvertCoinToERC20FromPacket(ctx, data)
	default:
		// the acknowledgement succeeded on the receiving chain so nothing needs to
		// be executed and no error needs to be returned
		return nil
	}
}
```

**File:** x/erc20/keeper/ibc_callbacks.go (L190-253)
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
```

**File:** evmd/tests/ibc/ibc_middleware_test.go (L728-754)
```go
			if tc.withCallback {
				suite.evmChainA.NextBlock()

				// SendEnabled=false will cause the conversion of bank tokens to erc20 tokens to fail,
				// but not send them back to escrow
				evmApp.BankKeeper.SetSendEnabled(evmCtx, nativeErc20.Denom, false)
				isSendEnabled := evmApp.BankKeeper.IsSendEnabledDenom(evmCtx, nativeErc20.Denom)
				suite.Require().False(isSendEnabled)

				packet1 := channeltypes.Packet{
					Sequence:           1,
					SourcePort:         path.EndpointB.ChannelConfig.PortID,
					SourceChannel:      path.EndpointB.ChannelID,
					DestinationPort:    path.EndpointA.ChannelConfig.PortID,
					DestinationChannel: path.EndpointA.ChannelID,
					Data:               packetData.GetBytes(),
					TimeoutHeight:      suite.evmChainA.GetTimeoutHeight(),
					TimeoutTimestamp:   0,
				}

				errAck := transferStack.OnRecvPacket(
					evmCtx,
					sourceChan.Version,
					packet1,
					suite.evmChainA.SenderAccount.GetAddress(),
				)
				suite.Require().False(errAck.Success())
```

**File:** evmd/tests/ibc/ibc_middleware_test.go (L799-808)
```go
				bankBalAfterUnescrow := evmApp.BankKeeper.GetBalance(evmCtx, sender, nativeErc20.Denom)
				// InitialBalance half which was converted but not sent will be in the sending account's balance
				suite.Require().Equal(sendAmt.String(), bankBalAfterUnescrow.Amount.String())

				// the packet that failed conversion due to the minting restriction should instead remain as the bank token
				// and will be in the isolated address used to invoke the callback
				isolatedAddr := callbacktypes.GenerateIsolatedAddress(path.EndpointA.ChannelID,
					suite.chainB.SenderAccount.GetAddress().String())
				trappedBal := evmApp.BankKeeper.GetBalance(evmCtx, isolatedAddr, nativeErc20.Denom)
				suite.Require().Equal(recvAmt.String(), trappedBal.Amount.String())
```
