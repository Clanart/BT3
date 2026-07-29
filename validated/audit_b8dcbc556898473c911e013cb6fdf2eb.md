## Title
ICS20 auto-conversion failure after successful mint causes error acknowledgement and duplicate refund (double-mint) - (File: `x/erc20/keeper/ibc_callbacks.go`, `x/erc20/ibc_middleware.go`)

### Summary
`x/erc20`'s IBC middleware wraps the core ICS20 transfer application to automatically convert a received IBC coin into its native ERC20 representation. The middleware calls the core transfer module's `OnRecvPacket` first (which mints/unescrows the coin into the recipient's bank balance on the *live* context, not a cached one), and only afterward attempts `ConvertCoinNativeERC20`. If this second, auxiliary step fails, the middleware returns a `channeltypes.NewErrorAcknowledgement`, but the bank-coin credit performed by the core transfer app in step one is never rolled back because it was not executed in a cache context that gets discarded on failure.

### Finding Description
In `x/erc20/ibc_middleware.go`:
```go
func (im IBCMiddleware) OnRecvPacket(...) exported.Acknowledgement {
	ack := im.Module.OnRecvPacket(ctx, channelVersion, packet, relayer) // mints/unescrows coin on ctx
	if !ack.Success() {
		return ack
	}
	return im.keeper.OnRecvPacket(ctx, packet, ack) // may still fail afterwards
}
``` [1](#0-0) 

And in `x/erc20/keeper/ibc_callbacks.go`, the "Case 2. native ERC20 token" branch runs *after* the coin has already been minted by the core module, and can independently fail (e.g. due to `SendEnabled=false`, a paused/blacklisting native-ERC20 contract, or any revert in the registered ERC20's `transfer`), at which point an error acknowledgement is manufactured:
```go
case found && pair.IsNativeERC20():
    ...
    if err := k.ConvertCoinNativeERC20(ctx, pair, coin.Amount, common.BytesToAddress(recipient.Bytes()), recipient); err != nil {
        return channeltypes.NewErrorAcknowledgement(err)
    }
``` [2](#0-1) 

This mirrors the structural pattern in the external report: a downstream, optional step of a multi-stage protocol interaction is allowed to fail independently of an already-committed primary state change, and the calling layer treats that failure as if nothing had happened yet (issuing an error acknowledgement), when in fact value has already been credited. This was directly reproduced in the test suite, which shows the coin remaining credited (as bank tokens trapped at the isolated/receiver address) after the auto-conversion step fails, while the packet-level acknowledgement returned is an error: [3](#0-2) [4](#0-3) 

Under the standard ICS20 acknowledgement contract, an error acknowledgement returned from `OnRecvPacket` is supposed to mean "no value was credited on the destination chain," and the *source* chain's `OnAcknowledgementPacket` handler unconditionally refunds (unescrows/mints back) the sender's original amount when it observes an error ack. If the destination chain's middleware returns an error ack **after** value has already been irreversibly credited (via the core module's non-cached mint/unescrow), the sender receives a refund on the source chain while the destination chain retains the credited value — resulting in duplicated spendable value across chains.

### Impact Explanation
This falls under the allowed "Critical unauthorized minting/duplication ... of spendable user value across native balances ... or IBC escrows" impact category. If exploitable, an attacker can effectively double their transferred balance: once via the (irreversibly) minted/unescrowed coin on the destination chain, and once via the automatic refund triggered on the source chain by the resulting error acknowledgement.

### Likelihood Explanation
The trigger conditions (native-ERC20 token pairs, `SendEnabled=false` for a denom, or a permissionlessly-registered native ERC20 contract whose `transfer` function can be made to revert under attacker-chosen conditions) are all reachable by an ordinary, unprivileged IBC packet sender/relayer without any special keys, matching the "unprivileged trigger" requirement. However, I was not able to fully verify within the available tool budget (1) whether `im.Module.OnRecvPacket` (the core ICS20 app call) executes on a cache-context that is itself discarded when the *overall* `IBCMiddleware.OnRecvPacket` returns an error ack (i.e., whether some outer layer in `ibc-go`'s channel keeper or the `ibc/module.go` wrapper already provides this atomicity guarantee), or (2) the exact code path and refund logic of `im.Module.OnAcknowledgementPacket`/`OnTimeoutPacket` for the core transfer app that would confirm the double-mint on error ack. My search for `RefundPacketToken`/`refundPacketToken` in this repo returned no results, suggesting that logic may live in the vendored `ibc-go` dependency rather than in this repository, which I could not inspect further within the remaining budget.

### Recommendation
- Perform the ERC20 auto-conversion (`ConvertCoinNativeERC20`) inside a cached/branched context (`ctx.CacheContext()`) before finalizing the acknowledgement, and only commit that cache (`writeFn()`) together with the core mint/unescrow when both steps succeed together.
- Alternatively, if the auto-conversion fails, do not return an error acknowledgement (which triggers source-chain refund); instead, always return the success ack from the core module (as the coin is already correctly credited in its bank form) and simply leave the coin as a bank coin for the user to convert manually — i.e., treat conversion failure as best-effort/non-fatal, never surfacing it as a packet-level error acknowledgement.
- Add an explicit invariant test that asserts: whenever the middleware returns an error acknowledgement from `OnRecvPacket`, no bank balance change persisted from the same packet-processing call.

### Proof of Concept
Not independently reproduced end-to-end due to tool/time constraints (in particular, could not confirm the exact atomicity behavior of the wrapped `ibc-go` `OnAcknowledgementPacket` refund path in this repo). The existing test `TestOnRecvPacketNativeErc20` (`evmd/tests/ibc/ibc_middleware_test.go`) already demonstrates the first half of the chain: forcing `SendEnabled=false` for a native-ERC20 denom causes `OnRecvPacket` to return an error acknowledgement while the packet's `recvAmt` remains credited as a bank coin trapped at the isolated/receiver address on the destination chain [5](#0-4) . A complete PoC would additionally need to show that relaying this specific error acknowledgement back to the source chain triggers `OnAcknowledgementPacket`'s refund path there, which I could not verify from the indexed code within the available budget — this remains a gap the user/background agent should independently confirm by running the full round-trip test with an attacker-controlled or governance-toggled failure of `ConvertCoinNativeERC20` and checking the source-chain balance after the error ack is relayed.

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

**File:** evmd/tests/ibc/ibc_middleware_test.go (L731-761)
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

				evmCtx = suite.evmChainA.GetContext()

				// SendEnabled=true causes our callback to succeed
				evmApp.BankKeeper.SetSendEnabled(evmCtx, nativeErc20.Denom, true)
				isSendEnabled = evmApp.BankKeeper.IsSendEnabledDenom(evmCtx, nativeErc20.Denom)
				suite.Require().True(isSendEnabled)
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
