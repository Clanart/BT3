## Analysis

The reported Boba bug is about a **broken two-phase compensation pattern**: an action is provisionally executed, and if a downstream step fails, a "compensation" message is sent to undo it — but the resource needed to actually perform the undo is never reserved, so the compensation can itself fail and leave state inconsistent while the "outer" system (the other chain) has already treated the whole flow as reverted.

The Cosmos EVM `x/erc20` ICS20 middleware has the exact same structural flaw in its `OnRecvPacket` wrapper. [1](#0-0) 

`im.Module.OnRecvPacket` (the underlying ICS20 transfer app) runs first and unconditionally commits its mint/unescrow of the transferred coin to the recipient's bank balance directly on the shared, uncached `ctx`. Only *after* that commit does `im.keeper.OnRecvPacket` run the ERC20 conversion logic, and if that fails for any reason it returns a `channeltypes.NewErrorAcknowledgement`. [2](#0-1) 

Because the packet handler never wraps this in a `CacheContext`, an error acknowledgement does not roll back the already-committed underlying transfer mint. Per the IBC protocol, an error acknowledgement causes the *sending* chain to refund the original sender when it processes `OnAcknowledgementPacket`. The receiving chain, however, retains the coin/voucher it already credited. The existing test suite actually documents and asserts this exact "trapped balance" behavior as an accepted fallback rather than treating it as a bug: [3](#0-2) 

### Title
Duplicated cross-chain value from unrevoked ICS20 mint/unescrow on ERC20 conversion error acknowledgement - (File: x/erc20/ibc_middleware.go, x/erc20/keeper/ibc_callbacks.go)

### Summary
`IBCMiddleware.OnRecvPacket` calls the base ICS20 transfer `OnRecvPacket` (which commits a mint/unescrow of coins to the recipient) *before* running the erc20-specific conversion logic. If the erc20 logic (`RegisterERC20Extension`, `MintingEnabled`, or `ConvertCoinNativeERC20`) fails, an error acknowledgement is returned, but the already-committed base-transfer state change is never reverted, unlike the intended two-phase "commit only after everything succeeds" pattern.

### Finding Description
`x/erc20/ibc_middleware.go`'s `OnRecvPacket` executes:
1. `im.Module.OnRecvPacket(ctx, ...)` — the standard ICS20 transfer callback, which mints IBC vouchers or unescrows native ERC20-backed coins to the recipient, committing directly to `ctx`.
2. If that succeeds, `im.keeper.OnRecvPacket(ctx, packet, ack)` is invoked to auto-convert the coin to its ERC20 representation.

Inside `x/erc20/keeper/ibc_callbacks.go`, several code paths in this second phase can independently fail and return `channeltypes.NewErrorAcknowledgement(err)`:
- Bech32/hex `recipient` decoding failure.
- `stakingKeeper.BondDenom` lookup failure.
- `RegisterERC20Extension` failure (Case 1, new IBC denom).
- `MintingEnabled` failure or `ConvertCoinNativeERC20` failure (Case 2, native ERC20 token pair) — e.g., due to `SendEnabled=false`, insufficient module ERC20 balance, or a reverting/self-destructed ERC20 contract.

None of these failure paths undo step 1's already-committed mint/unescrow, since the whole sequence runs on a single, non-cached `ctx`. The IBC error-acknowledgement contract is that a failed receive should correspond to no state change on the receiving chain — the sending chain relies on this and refunds the original sender upon receiving the error ack in its `OnAcknowledgementPacket`. Because the destination chain's mint/unescrow is not rolled back, both sides end up holding claims on the same value: the sender is refunded on the source chain while the recipient (or, for callback packets, the isolated escrow address) still holds the coin that was already credited on the destination chain.

This is confirmed by the repository's own integration test, which explicitly asserts that after an error acknowledgement caused by a blocked internal conversion, the transferred amount remains as a real, spendable bank coin balance at the isolated recipient address: [4](#0-3) 

### Impact Explanation
This produces unauthorized duplication of spendable user value across chains: the source chain refunds the sender their original escrowed/native funds, while the destination chain still holds a fully valid, spendable balance credited to the recipient from the same packet. Repeating this (e.g., a relayer/attacker crafting or triggering conditions that make the erc20 conversion phase fail, such as toggling `SendEnabled`, exhausting module ERC20 liquidity via `ConvertERC20`, or targeting a token pair with a reverting/self-destructing contract) allows value to be minted on the destination chain without any corresponding permanent reduction on the source chain, corrupting the 1:1 accounting invariant between native coins, ERC20 views, and IBC escrows.

### Likelihood Explanation
The conditions that make the erc20-specific phase fail (module ERC20 liquidity depletion via ordinary `ConvertERC20`/`ConvertCoin` usage, `SendEnabled` toggles, or a malicious/broken registered token-pair contract) are all reachable through unprivileged, ordinary transaction flows and are directly exercised (and treated as acceptable) by the project's own test suite, indicating this is a readily reproducible condition rather than a rare edge case.

### Recommendation
Wrap the entire `OnRecvPacket` sequence (base transfer callback + erc20 conversion) in a single `ctx.CacheContext()` that is only committed if the whole pipeline, including erc20 conversion, succeeds or gracefully no-ops. If erc20 conversion fails, revert the underlying ICS20 mint/unescrow as well (so state matches the produced error acknowledgement), rather than committing the base transfer and letting the erc20 phase's own logic decide the ack independently.

### Proof of Concept
1. Register a native-ERC20 token pair and give a user an initial balance; convert part of it to bank-coin representation and IBC-transfer it out and back so it re-enters via `OnRecvPacket` Case 2 (`pair.IsNativeERC20()`), or use any path that reaches Case 1/Case 2 in `x/erc20/keeper/ibc_callbacks.go`.
2. Before the destination chain processes the incoming `OnRecvPacket`, disable send (`BankKeeper.SetSendEnabled(denom, false)`) or otherwise ensure `ConvertCoinNativeERC20`/`RegisterERC20Extension` will fail, as done in `evmd/tests/ibc/ibc_middleware_test.go`'s `TestOnRecvPacketNativeErc20` "recipient with callback" case.
3. Submit the packet: `im.Module.OnRecvPacket` mints/unescrows the coin to the recipient (or isolated address) and commits; `im.keeper.OnRecvPacket` then fails and returns an error acknowledgement.
4. Observe (as the existing test does) that the recipient/isolated address retains the credited bank coin balance despite the returned error ack.
5. On the source chain, relay this error acknowledgement through `OnAcknowledgementPacket`, which refunds the original sender's escrowed/native funds.
6. Result: both the refunded sender (source chain) and the recipient (destination chain) now hold spendable value derived from the same single transfer.

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

**File:** x/erc20/keeper/ibc_callbacks.go (L95-139)
```go
	pairID := k.GetTokenPairID(ctx, coin.Denom)
	pair, found := k.GetTokenPair(ctx, pairID)
	switch {
	// Case 1. token pair is not registered and is an IBC Coin
	// by checking the prefix we ensure that only coins not native from this chain are evaluated.
	case !found && strings.HasPrefix(coin.Denom, "ibc/"):
		tokenPair, err := k.RegisterERC20Extension(ctx, coin.Denom)
		if err != nil {
			return channeltypes.NewErrorAcknowledgement(err)
		}

		ctx.EventManager().EmitEvents(
			sdk.Events{
				sdk.NewEvent(
					types.EventTypeRegisterERC20Extension,
					sdk.NewAttribute(types.AttributeCoinSourceChannel, packet.SourceChannel),
					sdk.NewAttribute(types.AttributeKeyERC20Token, tokenPair.Erc20Address),
					sdk.NewAttribute(types.AttributeKeyCosmosCoin, tokenPair.Denom),
				),
			},
		)
		return ack

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

**File:** evmd/tests/ibc/ibc_middleware_test.go (L748-808)
```go
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
