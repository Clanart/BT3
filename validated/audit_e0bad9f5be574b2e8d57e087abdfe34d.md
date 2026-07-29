### Title
Error acknowledgement returned after successful token unescrow in `x/erc20` IBC middleware duplicates transferred value across chains - (File: `x/erc20/keeper/ibc_callbacks.go`)

### Summary
The `x/erc20` ICS20 middleware's `OnRecvPacket` calls the underlying transfer app first, and only after it has already mutated bank state (unescrowing/crediting the recipient) does it attempt to auto-convert the coin to its ERC20 representation. If this post-hoc conversion step fails, the middleware still returns an `Acknowledgement_Error`, even though the recipient already received value from the base transfer. Because the failing conversion runs on the *same* (non-cached) `ctx` as the already-executed transfer, the earlier state mutation is not rolled back, while the error ack causes the source chain to refund the sender, duplicating spendable value across the two chains.

### Finding Description
`x/erc20/ibc_middleware.go` (`IBCMiddleware.OnRecvPacket`) is structured as: [1](#0-0) 

```go
ack := im.Module.OnRecvPacket(ctx, channelVersion, packet, relayer)
if !ack.Success() {
    return ack
}
return im.keeper.OnRecvPacket(ctx, packet, ack)
```

`im.Module.OnRecvPacket` is the standard ICS20 transfer app, which — on success — has already unescrowed/minted the transferred coin and credited it to the recipient's bank balance, using the same `ctx` that is passed onward. Only after this state mutation has already occurred does `im.keeper.OnRecvPacket` run, in `x/erc20/keeper/ibc_callbacks.go`: [2](#0-1) 

For a "native ERC20" token pair (`pair.IsNativeERC20()`), the keeper calls `k.MintingEnabled` and then `k.ConvertCoinNativeERC20`. If either fails, the function returns `channeltypes.NewErrorAcknowledgement(err)`: [3](#0-2) 

`MintingEnabled` can fail for reasons entirely outside the sender's/relayer's control and that are unrelated to whether the base transfer should be considered failed — e.g. `bankKeeper.IsSendEnabledCoin` returning false for the denom, the token pair being toggled off between send and receive, or the recipient being blocked: [4](#0-3) 

`ConvertCoinNativeERC20` itself performs a state mutation (`SendCoinsFromAccountToModule` escrow of the coin) before making the EVM call that can fail (e.g. self-destructed/paused ERC20 contract, EVM revert): [5](#0-4) 

None of this code path uses `ctx.CacheContext()`/`writeFn()` to gate the already-completed transfer-app state changes on the ultimate success of the erc20 conversion step. Standard IBC-go `RecvPacket` core handling commits whatever state changes were made by the port callback and writes the *acknowledgement byte* that is returned; it does not automatically discard application state simply because the returned acknowledgement is an error. Consequently, returning an error acknowledgement here after the transfer module already committed the unescrow to the recipient does not undo that credit.

An error acknowledgement causes the *source* chain, upon receiving it via `OnAcknowledgementPacket`/relayed ack, to refund/unescrow the original amount back to the sender there. Meanwhile the *destination* chain (this chain) retains the coin credited to the recipient from the base transfer (and, in the `ConvertCoinNativeERC20` failure case, additionally holds an amount that was moved into the `erc20` module's escrow account but never burned/returned). The net effect is that the same nominal value now exists simultaneously as: (a) a refund on the source chain, and (b) a credited/escrowed balance on the destination chain — an unauthorized duplication of spendable value across the two chains' escrows/balances.

### Impact Explanation
This breaks the fundamental 1:1 IBC escrow/mint accounting invariant between two chains connected via ICS20 transfer with the `x/erc20` middleware enabled. An attacker (any unprivileged user initiating or relaying an IBC transfer of a native-ERC20-backed denom) can trigger `MintingEnabled` failure (e.g. by targeting a denom with `SendEnabled=false`, or a blocked/module receiver) or force `ConvertCoinNativeERC20`'s EVM call to fail (e.g. against a paused/reverting/self-destructed ERC20 contract), causing this chain to both credit the recipient and cause the source chain to refund the sender for the identical amount. This matches the required Critical impact "unauthorized duplication...of spendable user value across native balances...IBC escrows, or precompile-mediated assets."

### Likelihood Explanation
The trigger conditions are reachable by unprivileged users through the normal IBC transfer flow with no special permissions required — only a native-ERC20 token pair, and either a bank `SendEnabled=false` state for the denom (governance/relayer-controlled but externally observable/exploitable in a race), or an ERC20 contract that reverts/self-destructs on transfer (which an attacker fully controls if the "native ERC20" is one they registered/deployed). The existing test suite (`evmd/tests/ibc/ibc_middleware_test.go` `TestOnRecvPacketNativeErc20`) explicitly exercises the failure path and confirms `errAck.Success()` is false while a subsequent packet exercises the success path, showing the failure/error-ack behavior is a known, reachable code path rather than a theoretical corner case.

### Recommendation
Wrap the erc20 conversion step (`k.OnRecvPacket`'s case-2 logic, i.e. `MintingEnabled` + `ConvertCoinNativeERC20`) in a `ctx.CacheContext()` so that either (a) the entire conversion succeeds and its state changes (together with the ack) are committed together, or (b) on any failure, the middleware returns the original *success* acknowledgement from the base transfer (leaving the recipient holding the bank coin representation, as documented in the function's own comments — "the user will receive the bank token instead") rather than downgrading a successful transfer into an ack-level failure. Never return an `Acknowledgement_Error` for a packet whose underlying transfer-layer effects have already been committed to state.

### Proof of Concept
1. Register a "native ERC20" token pair (`OWNER_EXTERNAL`) on chain A for an ERC20 contract the attacker controls.
2. Convert some ERC20 to the Coin representation (`MsgConvertERC20`), transfer the Coin to chain B via IBC (escrows the coin on chain A, mints/represents voucher on chain B).
3. From chain B, IBC-transfer the token back to chain A, addressed to a recipient.
4. Before the packet is relayed/received on chain A, make the conversion step fail deterministically, e.g. set `bankKeeper.SetSendEnabled(denom, false)` for the pair's denom (as exercised in `TestOnRecvPacketNativeErc20`, lines around [6](#0-5) ), or have the ERC20 contract revert on `transfer`.
5. `transferStack.OnRecvPacket` executes: the underlying transfer module unescrows the coin to the recipient (state committed), then `k.OnRecvPacket`'s `MintingEnabled`/`ConvertCoinNativeERC20` fails and returns `channeltypes.NewErrorAcknowledgement`.
6. The relayer forwards this error ack to chain B, which refunds the original sender there.
7. Query balances on chain A: the recipient (or the `erc20` module escrow account, in the `ConvertCoinNativeERC20` failure sub-case) still holds the credited/escrowed coin amount, while chain B has independently refunded the sender the same amount — demonstrating duplicated value across the two chains.

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

**File:** x/erc20/keeper/ibc_callbacks.go (L118-154)
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

		// For now the only case we are interested in adding telemetry is a successful conversion.
		telemetry.IncrCounterWithLabels(
			[]string{types.ModuleName, "ibc", "on_recv", "total"},
			1,
			[]metrics.Label{
				telemetry.NewLabel("denom", coin.Denom),
				telemetry.NewLabel("source_channel", packet.SourceChannel),
				telemetry.NewLabel("source_port", packet.SourcePort),
			},
		)
	}

	return ack
}
```

**File:** x/erc20/keeper/mint.go (L13-67)
```go
// MintingEnabled checks that:
//   - the global parameter for erc20 conversion is enabled
//   - minting is enabled for the given (erc20,coin) token pair
//   - recipient address is not on the blocked list
//   - bank module transfers are enabled for the Cosmos coin
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

**File:** x/erc20/keeper/msg_server.go (L256-266)
```go
	// Escrow Coins on module account
	coins := sdk.Coins{{Denom: pair.Denom, Amount: amount}}
	if err := k.bankKeeper.SendCoinsFromAccountToModule(ctx, sender, types.ModuleName, coins); err != nil {
		return sdkerrors.Wrap(err, "failed to escrow coins")
	}

	// Unescrow Tokens and send to receiver
	res, err := k.evmKeeper.CallEVM(ctx, erc20, types.ModuleAddress, contract, true, nil, "transfer", receiver, amount.BigInt())
	if err != nil {
		return err
	}
```

**File:** evmd/tests/ibc/ibc_middleware_test.go (L731-754)
```go
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
