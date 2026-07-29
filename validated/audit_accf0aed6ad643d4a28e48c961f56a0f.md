### Title
Potential double-credit on IBC receive when native-ERC20 auto-conversion fails after successful token mint - ([File: x/erc20/keeper/ibc_callbacks.go])

### Summary
I was not able to fully verify this finding due to tool errors on my final iteration (the `read_file` calls to `x/erc20/ibc_middleware.go` and `x/erc20/keeper/ibc_callbacks.go` failed because of a missing parameter, and I could not re-issue them). Based on the code I *did* retrieve earlier, I identified a plausible analog to the USDC-blacklist bug class, but I cannot confirm the actual middleware wiring (whether the ERC20 conversion step runs on the same, uncommitted state as the base ICS-20 mint, or on a cached/reverted context) which is the crux of whether this is exploitable.

### Finding Description
The external report describes a scenario where an irreversible cross-chain step (burn on source chain) is followed by a state-dependent operation on the destination chain (mint/transfer respecting a blacklist) that can fail asynchronously, permanently stranding value.

In this codebase, `Keeper.OnRecvPacket` in [1](#0-0)  implements an analogous two-step flow for "native ERC20" token pairs: after the base ICS-20 transfer app has already minted/unescrowed bank coins to the recipient, this hook attempts to additionally auto-convert those coins into the underlying ERC20 representation via `ConvertCoinNativeERC20`. If that conversion call fails (e.g., because the target ERC20 contract's `transfer` reverts — which is exactly the blacklist scenario from the original report, applicable if the wrapped native ERC20 is a blacklist-capable token like USDC), the function returns `channeltypes.NewErrorAcknowledgement(err)`.

Returning an error acknowledgement from an `OnRecvPacket` middleware hook signals to the relayer/source chain that the packet failed, which in standard ICS-20/IBC-go semantics causes the **source chain to unescrow and refund the sender** upon receiving that acknowledgement. The critical question — which I could not verify before running out of tool calls — is whether the underlying bank mint/unescrow performed by the base ICS-20 transfer app (which happens before this hook runs) is rolled back when this hook returns an error acknowledgement, or whether it has already been durably committed to the destination chain's state.

If the base mint is already committed (not wrapped in a `CacheContext`/`writeFn` pattern that only commits on success) at the point this hook can still cause an error acknowledgement to be returned, the result would be:
- Destination chain: recipient retains the bank coins from the (already completed) ICS-20 receive.
- Source chain: sees `ErrorAcknowledgement` and unescrows/refunds the original sender.

This would produce a duplication of spendable value across chains — a critical accounting break matching the "Critical unauthorized minting/duplication" impact bucket in scope.

### Impact Explanation
If confirmed, this would be a critical, unauthorized duplication of user funds: value present both on the destination chain (as bank coins) and refunded on the source chain (as unescrowed coins), breaking the 1:1 IBC escrow invariant with no privileged actor required — merely a blacklist-capable/reentrant/reverting ERC20 registered as a native-ERC20 token pair recipient.

### Likelihood Explanation
Likelihood depends entirely on wiring details I could not verify: (1) whether `x/ibc/transfer`'s wrapping middleware commits the base transfer's state changes before invoking `Erc20Keeper.OnRecvPacket`, and (2) whether any token pair registered as "native ERC20" wraps a contract capable of reverting a `transfer` call to an arbitrary recipient (e.g., blacklist, pausable, or fee-on-transfer tokens). Both conditions need confirmation.

### Recommendation
This requires further investigation before being reported as a confirmed finding:
1. Trace the exact middleware stack registration for `x/ibc/transfer` and confirm whether `Erc20Keeper.OnRecvPacket`'s conversion attempt executes on a cached context that is discarded (not written) when returning an error acknowledgement, or on the already-committed application state.
2. If the mint is already committed and the ack can still fail, the fix would be to either (a) run the conversion attempt on a `CacheContext` and, on failure, simply keep the bank coin (return a success ack, as is documented for `OnTimeoutPacket`/`OnAcknowledgementPacket` — "the user receives the corresponding bank token... instead") rather than returning an error acknowledgement that triggers a source-chain refund, or (b) ensure that returning an error acknowledgement here provably reverts the state changes from the base transfer.

### Proof of Concept
Not constructed — I was unable to complete tracing of the middleware composition (`ibc/module.go`, `x/erc20/ibc_middleware.go`) to determine whether this is exploitable, due to tool call failures on the final iteration. This should be verified with a full read of `x/erc20/ibc_middleware.go`, `ibc/module.go`, and the `OnRecvPacket` wiring before treating this as a confirmed vulnerability.

### Citations

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
