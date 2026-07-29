## Analog Vulnerability Identified

The Atlas report's root pattern — a two-phase "escrow/mint → conditional finalize" flow where a failure in the second phase after the first phase already moved real value, with no atomic rollback and no compensating control — has a direct analog in the ERC-20 IBC middleware's `OnRecvPacket` callback.

### Title
Duplicate fund creation via inconsistent Ack semantics in ERC20 IBC middleware `OnRecvPacket` for native-ERC20 token pairs - (File: `x/erc20/keeper/ibc_callbacks.go`)

### Summary
When a native-ERC20-backed IBC voucher returns to its origin chain, the underlying ICS-20 transfer application (`im.Module.OnRecvPacket`) unescrows the coin to the receiver's bank balance *first*, on the live (non-cached) `ctx`, and only then does the ERC20 middleware attempt an additional "auto-convert coin → ERC20" step via `k.OnRecvPacket`/`ConvertCoinNativeERC20`. If that second step fails, the middleware returns a `channeltypes.NewErrorAcknowledgement`. Because this is a valid acknowledgement value (not a Go error from the message handler), the SDK does **not** roll back the unescrow that already succeeded — but the relayer still carries the error ack back to the source chain, which then executes the standard ICS-20 refund of the original sender. This produces the same coin value on both chains simultaneously.

### Finding Description
`x/erc20/ibc_middleware.go` `OnRecvPacket` first calls the wrapped transfer app: [1](#0-0) 

`im.Module.OnRecvPacket` performs the actual ICS-20 receive logic, including unescrowing tokens to the recipient's bank balance directly on `ctx` — this is a real, committed state mutation, not a dry run. Only after that succeeds (`ack.Success() == true`) does `im.keeper.OnRecvPacket` run the erc20-specific conversion attempt.

Inside `x/erc20/keeper/ibc_callbacks.go`, for `pair.IsNativeERC20()` (Case 2), the code checks `MintingEnabled` and then calls `ConvertCoinNativeERC20`; on failure of either, it returns an **error acknowledgement**: [2](#0-1) 

An error acknowledgement returned from an `IBCModule.OnRecvPacket` callback is a *successful* Go call that merely encodes packet-level failure in its payload — it does not trigger any state rollback of what `im.Module.OnRecvPacket` already committed (the unescrow to the receiver's bank balance). The packet commitment/ack write proceeds normally and is relayed back to the source chain.

On the source chain, this error acknowledgement is processed by the standard ICS-20/erc20-middleware `OnAcknowledgementPacket` flow, which refunds (unescrows/mints back) the original sender there: [3](#0-2) 

The result: the receiver on the destination chain keeps the bank-denom coins from the already-successful unescrow, while the sender on the source chain is separately refunded the same amount — a duplicate credit of the transferred value across the two chains.

Notably, the codebase already recognizes this exact class of hazard and avoids it in the *acknowledgement/timeout* direction: `ConvertCoinToERC20FromPacket`, called from `OnAcknowledgementPacket`/`OnTimeoutPacket`, deliberately **swallows** `ConvertCoinNativeERC20` errors and always returns `nil` instead of propagating an error, specifically because failing there must not affect the already-finalized refund accounting: [4](#0-3) 

The `OnRecvPacket` path was not given the same treatment — it still converts a post-hoc conversion failure into a packet-level error acknowledgement, undoing the ack's truthfulness about what already happened on-chain.

### Impact Explanation
This breaks the fundamental IBC/ICS-20 invariant that a packet's acknowledgement must accurately reflect whether value was actually moved. Because the underlying unescrow is already final by the time the erroring branch executes, sending an error ack causes the source chain to also unescrow/refund the sender — creating duplicated spendable value across the two chains' IBC escrow accounting. This matches the Critical impact class of "unauthorized minting/duplication ... across native balances ... IBC escrows."

### Likelihood Explanation
The `MintingEnabled` check can fail for reasons outside the receiver's control at genuine registration/config drift (e.g., `SendEnabledCoin` toggled, pair disabled between hops), and `ConvertCoinNativeERC20`'s EVM call can fail if the native ERC20 contract has since self-destructed or reverts on `transfer` (e.g., a blocklist-style ERC20 owned externally, or a contract deliberately made to revert for the receiver's address) — both plausible for an externally-owned (`OWNER_EXTERNAL`) native-ERC20 token pair under an attacker's control of the paired contract. This makes the failure branch reachable by a party with no special chain privileges, simply by round-tripping their own native-ERC20-backed coin through IBC while the contract-side condition is engineered to fail on the second leg.

### Recommendation
In `x/erc20/keeper/ibc_callbacks.go` `OnRecvPacket` (Case 2, native ERC20), do not return an error acknowledgement once the underlying coin unescrow by `im.Module.OnRecvPacket` has already succeeded. Mirror the pattern already used in `ConvertCoinToERC20FromPacket`: on `MintingEnabled`/`ConvertCoinNativeERC20` failure, log/emit an event and return the original success `ack` unchanged, leaving the user with the bank-denom coin for manual reconversion, exactly as the accompanying doc comment for `OnRecvPacket` claims ("If conversion fails, then the user will receive the bank token instead") but the code does not actually implement for this branch.

### Proof of Concept
1. Register a native ERC20 token pair with `ContractOwner = OWNER_EXTERNAL` (owner-controlled contract) via `RegisterERC20`.
2. Convert coin → ERC20, then IBC-transfer the resulting bank coin (escrowing the native ERC20 as part of `ConvertCoinNativeERC20`/outbound transfer) to chain B.
3. Trigger the coin's return via IBC (chain B sends it back to chain A). On chain A, `im.Module.OnRecvPacket` unescrows the coin into the receiver's bank balance and returns a success ack.
4. Before this packet is processed, self-destruct (or otherwise break) the native ERC20 contract so that `k.evmKeeper.CallEVM(..., "transfer", ...)` inside `ConvertCoinNativeERC20` fails.
5. `x/erc20/keeper/ibc_callbacks.go`'s `OnRecvPacket` Case 2 returns `channeltypes.NewErrorAcknowledgement(err)` despite the unescrow already being committed.
6. Relayer delivers this error ack to chain B; chain B's `OnAcknowledgementPacket` refunds the original sender.
7. Verify: receiver on chain A holds the unescrowed bank coin AND sender on chain B has been refunded the same amount — total circulating value increased by one transfer amount.

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

**File:** x/erc20/keeper/ibc_callbacks.go (L236-253)
```go
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
