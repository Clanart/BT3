## Analog Identified: Non-Atomic ERC20 Conversion Middleware Causes Double-Credit on IBC `OnRecvPacket` Failure

The Stargate report's root cause is that a downstream, potentially-reverting step runs *after* value has already moved, so a revert leaves the value duplicated/stuck instead of being atomically undone. The direct Cosmos EVM analog is in the ERC20 IBC middleware wrapping ICS-20 transfers.

### Title
Non-atomic ERC20 conversion in `IBCMiddleware.OnRecvPacket` allows duplication of transferred value across chains - (File: `x/erc20/ibc_middleware.go`, `x/erc20/keeper/ibc_callbacks.go`)

### Summary
`x/erc20/ibc_middleware.go`'s `OnRecvPacket` first invokes the underlying ICS-20 transfer module (which mints/unescrows real coins to the recipient and commits this directly to `ctx`), and only afterward calls `keeper.OnRecvPacket` to convert those coins to their ERC20 representation. If that post-transfer conversion step fails, the middleware returns an error acknowledgement, but the prior mint/unescrow performed by the transfer module is never rolled back because it was applied to the live `ctx`, not a cached context shared across both steps.

### Finding Description [1](#0-0) 

```go
func (im IBCMiddleware) OnRecvPacket(...) exported.Acknowledgement {
	ack := im.Module.OnRecvPacket(ctx, channelVersion, packet, relayer)
	if !ack.Success() {
		return ack
	}
	return im.keeper.OnRecvPacket(ctx, packet, ack)
}
```

`ctx` is passed directly (not a fresh `ctx.CacheContext()` shared across both calls) into the underlying transfer app and then reused for the ERC20 conversion. The transfer app's `OnRecvPacket` mints ICS-20 vouchers or unescrows native ERC20-backed coins to the recipient and commits this state change immediately upon returning a success acknowledgement.

`keeper.OnRecvPacket` then attempts ERC20-specific handling and can fail in at least two branches, both returning an error acknowledgement *after* the coin transfer has already been committed:

- Case 1 (new IBC coin, first-time ERC20 extension registration): [2](#0-1) 
- Case 2 (native-ERC20-backed coin returning home, re-conversion to ERC20): [3](#0-2) 

In both cases, `channeltypes.NewErrorAcknowledgement(err)` is returned once `RegisterERC20Extension` or `ConvertCoinNativeERC20` fails, but the recipient has already received the underlying bank coin from the (already-committed) transfer step.

Contrast this with `OnAcknowledgementPacket`/`OnTimeoutPacket` → `ConvertCoinToERC20FromPacket`, which deliberately swallow ERC20 conversion failures and return `nil` (success) so the user simply keeps the bank coin instead of ERC20 — the code comment even states this intentional design: [4](#0-3) . `OnRecvPacket`'s Case 1/2 branches diverge from this pattern by propagating the error as a packet-level failure.

### Impact Explanation
An error acknowledgement returned from `OnRecvPacket` is relayed back to the counterparty (source) chain. Per ICS-20 semantics, the source chain treats this as a failed transfer and unlocks/refunds the original sender's escrowed or burned tokens. Meanwhile, on the Cosmos EVM chain, the recipient has *already* received the corresponding coin (as an IBC voucher or unescrowed native token) because that state was committed by `im.Module.OnRecvPacket` before the ERC20 conversion step ran and failed. The result is that the same value now exists simultaneously as: (a) refunded balance on the source chain, and (b) credited balance (in bank-coin form) on the destination chain — an unauthorized duplication of spendable user value across chains, matching the Critical impact bar ("unauthorized minting, burning, duplication... of spendable user value across native balances... IBC escrows").

### Likelihood Explanation
No privileged access is required. Any unprivileged relayer/user can trigger the ERC20 conversion failure path — e.g., by causing `ConvertCoinNativeERC20` to revert (a paused/blacklisted/self-destructed ERC20 contract, or an ERC20 with custom transfer-hook logic that reverts), or by conditions causing `RegisterERC20Extension` to fail for a first-seen IBC denom. Since this is reachable through the standard, unprivileged ICS-20 receive-packet flow (ordinary cross-chain transfer), the likelihood of triggering it is high once such a conversion failure condition exists.

### Recommendation
Wrap the entire `OnRecvPacket` sequence — both the underlying transfer module call and the ERC20 conversion — in a single `ctx.CacheContext()`, only calling `writeFn()` (committing state) if the whole sequence succeeds. Alternatively, apply the same fail-safe pattern already used in `OnAcknowledgementPacket`/`OnTimeoutPacket`: never fail the acknowledgement due to ERC20 conversion errors; instead, leave the recipient with the bank coin and emit a failure event, exactly as the code comment states is the intended design for those two paths.

### Proof of Concept
1. Register a native ERC20 token pair, transfer it out via IBC to a counterparty chain (locking/escrowing local coins).
2. Cause the destination-chain ERC20 contract for that token pair to fail future `mint`/`transfer` calls used by `ConvertCoinNativeERC20` (e.g., pause it, or in the "new IBC coin" branch, cause `RegisterERC20Extension` to fail for a colliding/malformed denom).
3. Send the tokens back over IBC to the Cosmos EVM chain (`OnRecvPacket` case 2, or first-time receipt for case 1).
4. Observe: `im.Module.OnRecvPacket` unescrows/mints the bank coin to the recipient and commits it to `ctx`; `keeper.OnRecvPacket` then fails and returns an error acknowledgement.
5. The relayer delivers the error acknowledgement to the source chain, which refunds the original sender's escrowed balance.
6. Verify on the destination chain that the recipient's bank balance for that denom is non-zero (the unescrowed/minted amount is retained) while the source chain has also refunded the sender — confirming duplicated value.

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

**File:** x/erc20/keeper/ibc_callbacks.go (L98-116)
```go
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

**File:** x/erc20/keeper/ibc_callbacks.go (L156-163)
```go
// OnAcknowledgementPacket responds to the success or failure of a packet
// acknowledgement written on the receiving chain. If the acknowledgement was a
// success then nothing occurs. If the acknowledgement failed, then the sender
// is refunded and then the IBC Coins are converted to ERC20.
// If the ERC20 conversion fails for whatever reason, such as an attempt to call
// a self-destructed ERC20 contract or an invalid function, OnAcknowledgementPacket
// still succeeds, but the user receives the corresponding bank token from the
// TokenPair instead. A user may then manually re-attempt the conversion.
```
