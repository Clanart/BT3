## Title
IBC ERC20 middleware returns an error acknowledgement after the underlying transfer has already committed, enabling double-minting of transferred value - (File: x/erc20/ibc_middleware.go, x/erc20/keeper/ibc_callbacks.go)

### Summary
The GMX bug is about a fallback path executed after a sub-operation (the PnL swap) fails: funds are delivered directly without re-validating the invariant (`minOutputAmount`) that the failed sub-step was supposed to guarantee. The Cosmos EVM analog is `x/erc20`'s ICS20 `OnRecvPacket` middleware: it lets the underlying transfer succeed (crediting the recipient's bank balance) and only *afterwards* attempts an unrelated, unprotected side-step (auto-converting the received coin to its ERC20 representation). If that side-step fails, the middleware returns an *error acknowledgement* for the whole packet — even though the primary transfer already irreversibly committed — causing the source chain to refund/un-escrow the same funds it already sent.

### Finding Description
`IBCMiddleware.OnRecvPacket` in [1](#0-0)  first calls the wrapped ICS20 transfer application (`im.Module.OnRecvPacket`), which mints/un-escrows the voucher coin directly to the recipient's bank balance and returns a success acknowledgement. This call is **not** wrapped in `ctx.CacheContext()`, so once it returns success, the balance mutation is already committed to the real state.

The middleware then calls `im.keeper.OnRecvPacket(ctx, packet, ack)` (`x/erc20/keeper/ibc_callbacks.go`) using the *same, already-mutated* context, to auto-convert the received coin into its ERC20 representation for "native ERC20 token" pairs: [2](#0-1) 

If `k.MintingEnabled(...)` or `k.ConvertCoinNativeERC20(...)` fails (e.g., ERC20 contract paused, `SendEnabled=false` for the denom, a self-destructed/invalid ERC20 implementation, or any other reason the ERC20-side execution reverts), the function returns `channeltypes.NewErrorAcknowledgement(err)`. This error acknowledgement is written back to the relaying chain as the packet's acknowledgement.

Per IBC semantics, an error acknowledgement instructs the **source chain** to treat the transfer as failed and refund (un-escrow) the sender's originally-sent tokens. However, on the **destination chain**, the bank-coin credit to the recipient from the successful `im.Module.OnRecvPacket` call was never rolled back — there is no cache/revert boundary between the successful transfer and the subsequent failing ERC20 conversion attempt. The result: the recipient keeps the newly-credited bank coin on the destination chain, AND the sender gets their original tokens un-escrowed back on the source chain — the same value now exists in two places.

This is structurally identical to the GMX pattern: a side-effect operation whose failure should have caused a revert of the overall action instead silently allows already-delivered value to remain while the top-level operation is reported as failed.

### Impact Explanation
This breaks the fundamental 1:1 escrow/mint accounting invariant between the source chain's escrowed balance and the destination chain's minted/unescrowed representation, which is explicitly called out as in-scope ("Asset-representation path: x/erc20 ... ICS20 ... flows must preserve 1:1 accounting between native coins, ERC20 views, escrows, and precompile-visible balances"). An unprivileged relayer/user can trigger the erc20-conversion failure path deterministically (e.g., by having the recipient hold a native-ERC20-backed token pair whose ERC20 contract is temporarily paused, or where `SendEnabled` is false for that denom — as demonstrated in the repo's own test `TestOnRecvPacketNativeErc20` at [3](#0-2) ), resulting in duplicated spendable value across chains — a critical unauthorized duplication/accounting-corruption impact.

### Likelihood Explanation
The conversion failure condition (`SendEnabled=false`, paused/invalid ERC20 contract, insufficient minting permission) is something that can occur in ordinary operation for "native ERC20" token pairs, and the repository's own test suite (`TestOnRecvPacketNativeErc20`) exercises exactly this scenario, confirming it is a reachable, well-known code path rather than a hypothetical edge case. Any user/relayer completing an ordinary IBC transfer of a native-ERC20-backed denom to a recipient whose ERC20 side is (even temporarily) unable to accept the mint can trigger the duplicate-credit condition.

### Recommendation
Wrap the underlying transfer application's `OnRecvPacket` call and the subsequent `im.keeper.OnRecvPacket` ERC20-conversion step in a single `ctx.CacheContext()` boundary, only committing (`write()`) if both the transfer and the ERC20 conversion succeed. Alternatively, if the ERC20 conversion step fails, do not return an error acknowledgement for the whole packet (since the underlying transfer already succeeded); instead, keep the bank-coin credit as the final state (as documented for `OnAcknowledgementPacket`/`OnTimeoutPacket`, which already treat conversion failure as a no-op that leaves the bank token in place) and return the original success acknowledgement.

### Proof of Concept
1. Register a "native ERC20" token pair (`pair.IsNativeERC20()`), whose ERC20 contract or denom's `SendEnabled` can be toggled.
2. Disable `SendEnabled` for the denom (or otherwise make `ConvertCoinNativeERC20`/`MintingEnabled` fail) on the destination chain.
3. Relay an ICS20 `MsgTransfer` packet from the source chain to a recipient on the destination chain for this denom.
4. Observe (as in `TestOnRecvPacketNativeErc20`, packet1 case) that: (a) `im.Module.OnRecvPacket` succeeds and credits the recipient's bank balance with the voucher/native coin, (b) `k.OnRecvPacket`'s native-ERC20 branch fails ERC20 conversion and returns an error acknowledgement, (c) the resulting error ack is relayed back to the source chain, causing it to un-escrow/refund the sender's original tokens — while the destination chain's recipient still holds the bank-coin credit from step (a), producing duplicated value across both chains.

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
