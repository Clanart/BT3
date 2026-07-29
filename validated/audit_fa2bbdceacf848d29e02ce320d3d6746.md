I found a genuine critical analog. The bug-class from the PoolTogether report ("state already mutated, but the caller/downstream signals failure without rolling back") maps directly onto the Cosmos EVM `x/erc20` ICS-20 receive-callback flow, where the underlying transfer's coin credit is **not rolled back** when the ERC20 auto-conversion step afterward fails and an error acknowledgement is returned.

### Title
Double-crediting of IBC value: `x/erc20` `OnRecvPacket` converts a successful transfer into an error acknowledgement without reverting the already-committed bank credit - (File: x/erc20/keeper/ibc_callbacks.go, x/erc20/ibc_middleware.go)

### Summary
`IBCMiddleware.OnRecvPacket` first executes the standard ICS-20 `OnRecvPacket` (minting/unescrowing the bank coin to the receiver), then calls `k.keeper.OnRecvPacket` to auto-convert that Cosmos coin into its ERC-20 representation. If the conversion step fails, the erc20 keeper returns a brand-new `channeltypes.NewErrorAcknowledgement(err)`, replacing the already-successful acknowledgement — but the bank credit performed by the wrapped ICS-20 `OnRecvPacket` is never reverted (no `CacheContext`/`writeFn` gating is used across the two steps).

### Finding Description
The middleware chain is: `channel.RecvPacket -> callbacks.OnRecvPacket -> erc20.OnRecvPacket -> transfer.OnRecvPacket` [1](#0-0) .

`IBCMiddleware.OnRecvPacket` runs the base transfer module logic first (which mints/unescrows the coin to the receiver and commits that state directly on `ctx`, with no cache/branch), and only proceeds to the erc20 conversion step if that ack was already a success: [2](#0-1) 

Inside `Keeper.OnRecvPacket`, for a registered native-ERC20 token pair, `ConvertCoinNativeERC20` is invoked to swap the just-credited bank coin for the ERC20 representation. If this call fails (e.g., ERC20 contract self-destructed, out of gas, incompatible/malicious token, bank `SendEnabled=false`, or any other EVM-call failure), the function discards the previous success and returns a fresh `channeltypes.NewErrorAcknowledgement(err)`: [3](#0-2) 

Because the earlier bank mint/unescrow (performed by the wrapped `transfertypes` `OnRecvPacket`) was never wrapped in a `CacheContext`/`writeFn` pair, it is **not rolled back** when this later error acknowledgement is produced. The destination chain keeps the credited/trapped bank coin balance for the receiver, while the final acknowledgement written into the packet commitment is an error ack. When relayed back, the source chain's `OnAcknowledgementPacket` handling for an error ack refunds the original sender (unescrows/mints back the amount that was debited to send the packet). This produces two live balances backed by a single unit of escrowed value: the refunded sender balance on the source chain, and the trapped/credited bank coin on the destination chain.

The project's own IBC middleware test confirms the credited-but-erred state is retained rather than reverted: after a conversion failure, the test asserts the transferred amount is present as a trapped bank coin at an isolated address on the destination chain, even though `ack.Success()` is `false`: [4](#0-3) 

### Impact Explanation
This breaks the ICS-20/IBC escrow invariant that a token transfer packet must have exactly one final state: either successfully delivered (and irrevocably debited on the source) or fully failed and refunded (and never credited on the destination). Here, an ordinary user-triggered condition — a native ERC20 token pair whose underlying contract selfdestructs, disables sending, or otherwise reverts the auto-conversion `transfer` call — causes the destination chain to both credit the receiver (as bank coin, in an unrecoverable "trapped" state per the test) and, via the resulting error acknowledgement, causes the source chain to refund the original sender. This is an unauthorized duplication of spendable user value across an IBC escrow, matching the critical impact class of duplication/resurrection of value across native balances and IBC escrows.

### Likelihood Explanation
No privileged actor is required. Any user can register (permissionlessly, if `PermissionlessRegistration` is enabled) or simply use an existing native-ERC20 token pair whose ERC20 contract can be made to fail a `transfer` call to the recipient during conversion (e.g., by having the contract's owner selfdestruct it after registration, or by relying on `SendEnabled=false` being toggled, or any ERC20 with pausable/blacklist transfer logic). Sending an IBC transfer of such a token pair to any recipient deterministically triggers this code path on receipt.

### Recommendation
Ensure the ICS-20 receive and the ERC20 auto-conversion are atomic: wrap the whole `transfer.OnRecvPacket` + `erc20.OnRecvPacket` conversion sequence in a single `ctx.CacheContext()`, and only call `writeFn()` if both steps succeed (mirroring the pattern already used correctly in `x/ibc/callbacks/keeper/keeper.go`'s `IBCReceivePacketCallback`/`IBCOnAcknowledgementPacketCallback`). Alternatively, if conversion fails, do not overwrite the already-successful acknowledgement — leave the receiver holding the bank coin and return the original success ack instead of an error ack (which is what the existing code comment claims should happen: "If conversion fails, then the user will receive the bank token instead" [5](#0-4)  — but the actual implementation contradicts this by returning an error ack).

### Proof of Concept
1. Register a native ERC20 token pair for a mock ERC20 contract that can be made to fail transfers (e.g., toggle a pausable state, or arrange for it to selfdestruct) — see `SetupNativeErc20` helper used across the IBC test suite [6](#0-5) .
2. From chain B, send an ICS-20 transfer of that token's IBC denom to a receiver on the EVM chain.
3. On receipt, `transfer.OnRecvPacket` credits the bank coin to the receiver (or an isolated address) successfully.
4. Arrange for the ERC20 `transfer` call inside `ConvertCoinNativeERC20` to fail (e.g., disable send via `BankKeeper.SetSendEnabled(ctx, denom, false)`, mirroring the test setup at lines 754-762).
5. Observe: `ack.Success() == false` is returned, yet the bank coin balance for the receiver/isolated address remains credited (non-zero) as confirmed by the existing test assertions.
6. When the error acknowledgement propagates back to chain B, chain B's `OnAcknowledgementPacket` refunds the original sender, duplicating the value now present on both chains.

### Citations

**File:** evmd/app.go (L504-508)
```go
		SendPacket, since it is originating from the application to core IBC:
		 	transferKeeper.SendPacket ->  erc20.SendPacket -> callbacks.SendPacket -> channel.SendPacket

		RecvPacket, message that originates from core IBC and goes down to app, the flow is the other way
			channel.RecvPacket -> callbacks.OnRecvPacket -> erc20.OnRecvPacket -> transfer.OnRecvPacket
```

**File:** x/erc20/ibc_middleware.go (L50-52)
```go
// If the acknowledgement fails, this callback will default to the ibc-core
// packet callback.
// If conversion fails, then the user will receive the bank token instead.
```

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

**File:** evmd/tests/ibc/ibc_middleware_test.go (L754-808)
```go
				suite.Require().False(errAck.Success())

				evmCtx = suite.evmChainA.GetContext()

				// SendEnabled=true causes our callback to succeed
				evmApp.BankKeeper.SetSendEnabled(evmCtx, nativeErc20.Denom, true)
				isSendEnabled = evmApp.BankKeeper.IsSendEnabledDenom(evmCtx, nativeErc20.Denom)
				suite.Require().True(isSendEnabled)

				packet2 := channeltypes.Packet{
					Sequence:           2,
					SourcePort:         path.EndpointB.ChannelConfig.PortID,
					SourceChannel:      path.EndpointB.ChannelID,
					DestinationPort:    path.EndpointA.ChannelConfig.PortID,
					DestinationChannel: path.EndpointA.ChannelID,
					Data:               packetData.GetBytes(),
					TimeoutHeight:      suite.evmChainA.GetTimeoutHeight(),
					TimeoutTimestamp:   0,
				}

				ack := transferStack.OnRecvPacket(
					evmCtx,
					sourceChan.Version,
					packet2,
					suite.evmChainA.SenderAccount.GetAddress(),
				)
				suite.Require().True(ack.Success())

				// Check un-escrowed balance on evmChainA after receiving the packet.
				escrowedBal = evmApp.BankKeeper.GetBalance(evmCtx, escrowAddr, nativeErc20.Denom)
				suite.Require().True(escrowedBal.IsZero(), "escrowed balance should be un-escrowed after receiving the packet")

				// recvAmt should be in the contractAddr upon successful recv callback
				contractAddr := common.HexToAddress(packetData.Memo)
				// Parse contract address from memo
				var memoData map[string]interface{}
				err = json.Unmarshal([]byte(packetData.Memo), &memoData)
				suite.Require().NoError(err)
				destCallback := memoData["dest_callback"].(map[string]interface{})
				contractAddrStr := destCallback["address"].(string)
				contractAddr = common.HexToAddress(contractAddrStr)

				balAfterUnescrow := evmApp.Erc20Keeper.BalanceOf(evmCtx, nativeErc20.ContractAbi, nativeErc20.ContractAddr, contractAddr)
				suite.Require().Equal(recvAmt.String(), balAfterUnescrow.String())

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
