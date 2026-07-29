Now I have enough to confirm the analog vulnerability. This maps cleanly to the reported bug class: an overly strict/error-returning code path in a per-item processing flow causes a hard failure where the reference/intended behavior is to gracefully continue, and here the consequence is far more severe — duplication of transferred value across chains.

### Title
IBC ERC20 auto-conversion returns an error acknowledgement after the underlying transfer already succeeded, causing duplicated funds via source-chain refund (`x/erc20/keeper/ibc_callbacks.go: OnRecvPacket`)

### Summary
`IBCMiddleware.OnRecvPacket` first executes the standard ICS20 transfer (`im.Module.OnRecvPacket`), which credits/unescrows native coins to the recipient's bank balance on this chain, and only then calls `k.keeper.OnRecvPacket` to auto-convert those coins into their ERC20 representation. If that secondary conversion step fails (e.g. `MintingEnabled` or `ConvertCoinNativeERC20` returns an error), the middleware returns a `channeltypes.NewErrorAcknowledgement`, even though the underlying transfer already completed and mutated bank state in the same message execution. [1](#0-0) [2](#0-1) 

### Finding Description
This is the same bug class as the analog beacon-chain report: a nested/secondary validation step that can fail independently of the primary operation is allowed to abort the overall flow with an error, instead of gracefully degrading (continuing with the already-valid state), which is what the code's own documentation says should happen: "If conversion fails, then the user will receive the bank token instead." [3](#0-2) 

In practice, the "Case 2" branch for native ERC20 pairs does not honor that contract. If `k.MintingEnabled(...)` fails (e.g., ERC20 module gets disabled, token pair disabled, recipient blocked, or bank send-enabled toggled off for the coin) or `k.ConvertCoinNativeERC20(...)` fails (e.g., EVM call reverts, `ErrBalanceInvariance` check fails, self-destructed ERC20 contract, out-of-gas), the function returns an **error acknowledgement**: [4](#0-3) 

But by the time this code runs, the ICS20 transfer (`im.Module.OnRecvPacket`) has already unescrowed/minted the native coin to the recipient's account as part of the same message execution — this state change is not rolled back merely because the application later returns a failure acknowledgement object (Cosmos SDK/IBC-go only roll back state on a genuine Go `error` from the message handler, not on an `Acknowledgement_Error` payload, which is treated as a valid/successful message execution outcome that is committed).

An error acknowledgement written into the packet commitment is then relayed back to the source chain, which processes it via `OnAcknowledgementPacket`/timeout logic and refunds the original sender (unescrows or re-mints the sent amount back to them on the source chain).

Net effect: the recipient on the destination chain keeps the credited bank coins (since that state was never reverted), AND the sender on the source chain gets refunded the same amount — the value now exists twice.

### Impact Explanation
This directly matches the required Critical impact category: "unauthorized minting, burning, duplication ... of spendable user value across native balances ... IBC escrows." An IBC relayer (an ordinary/unprivileged transaction flow — no special permissions needed to relay a packet) transferring a registered native-ERC20-backed denom can trigger this if any of the downstream conversion-failure conditions are met (which are influenced by ordinary governance toggles, `BlockedAddr` status, self-destructed pair contracts, or any transient EVM-call failure such as an out-of-gas/insufficient balance during the internal `transfer` call). The result is a duplication of escrowed/circulating value between two chains, corrupting the invariant that IBC transfers preserve 1:1 conservation of value between escrow and voucher/mint.

### Likelihood Explanation
Triggering requires only an ordinary IBC transfer of a token pair backed by a native ERC20 contract, combined with one of several realistic failure conditions in `MintingEnabled`/`ConvertCoinNativeERC20` (disabled token pair, blocked recipient, self-destructed ERC20 contract, EVM call failure/insufficient gas budget, or bank send-disabled toggling) that are plausible in production operation and can, in some cases (e.g., targeting a recipient address that is blocked, or waiting for a token pair to be disabled/contract to self-destruct), be deliberately engineered by any user controlling the transfer's receiver/denom.

### Recommendation
Align the ERC20 IBC-callback conversion failure paths with the documented behavior: on failure of `MintingEnabled` or `ConvertCoinNativeERC20` in `OnRecvPacket`, return the original successful `ack` (letting the recipient keep the plain bank/voucher coin) instead of `channeltypes.NewErrorAcknowledgement(err)`, consistent with how `ConvertCoinToERC20FromPacket` handles conversion failures by returning `nil` (no-op, funds remain as-is) rather than propagating an error that triggers a refund. Only return an error acknowledgement here if the ICS20 transfer state itself can be guaranteed to be rolled back together with it (e.g., by branching/caching the context for the whole combined operation and only committing on full success).

### Proof of Concept
1. Register a native ERC20 token pair (`pair.IsNativeERC20()`), enabled for IBC conversion.
2. Add the intended recipient address to the bank module's blocked-address list (or disable `IsSendEnabledCoin` for the pair's denom, or let the ERC20 contract self-destruct after registration).
3. Relay an ICS20 transfer packet targeting that recipient/denom to this chain.
4. `im.Module.OnRecvPacket` succeeds, crediting the native coin to the recipient's bank balance (`SendCoinsFromModuleToAccount`/unescrow).
5. `k.keeper.OnRecvPacket` reaches Case 2, calls `k.MintingEnabled`, which fails due to the blocked address / disabled send, and returns `channeltypes.NewErrorAcknowledgement(err)`.
6. The relayer relays this error acknowledgement back to the source chain, which refunds the original sender.
7. Verify: recipient's bank balance on the destination chain remains credited (never rolled back) while the sender's balance on the source chain is also restored — the transferred amount now exists on both chains simultaneously.

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

**File:** x/erc20/keeper/ibc_callbacks.go (L50-52)
```go
		return channeltypes.NewErrorAcknowledgement(err)
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
