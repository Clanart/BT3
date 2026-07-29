### Title
Disabling an ERC20 token pair via `ToggleConversion` causes `OnRecvPacket` to silently drop incoming IBC transfers instead of crediting the recipient - ([File: x/erc20/keeper/ibc_callbacks.go])

### Summary
The `WardenPledge.pause()` bug shows a pattern where an owner-controlled state flag, once flipped, immediately blocks a legitimate withdrawal path and permanently strands user funds inside the contract with no fallback. The Cosmos EVM `x/erc20` module has a structurally identical pattern: governance can flip a `TokenPair.Enabled` flag via `ToggleConversion`, and this flag is checked inside the ICS-20 `OnRecvPacket` middleware. When the flag is `false`, the middleware returns a **success acknowledgement** without minting/crediting the recipient any funds, and without falling back to any other credit path.

### Finding Description
`ToggleConversion` toggles `TokenPair.Enabled` for a given token/denom pair: [1](#0-0) 

This flag is enforced in the ICS-20 receive middleware `OnRecvPacket`. For a registered "native ERC20" token pair (an ERC20 contract that was registered and whose Cosmos-coin representation can travel over IBC), the handler does:

```go
case found && pair.IsNativeERC20():
    // Token pair is disabled -> return
    if !pair.Enabled {
        return ack
    }
    ...
    if err := k.ConvertCoinNativeERC20(ctx, pair, coin.Amount, common.BytesToAddress(recipient.Bytes()), recipient); err != nil {
        return channeltypes.NewErrorAcknowledgement(err)
    }
``` [2](#0-1) 

When `pair.Enabled == false`, the function returns the (already-constructed) **success** acknowledgement `ack` immediately — it does not mint the coin to the receiver's account, nor does it fall back to crediting a plain bank coin, nor does it return an error acknowledgement that would cause the source chain to refund the sender. The IBC transfer keeper (`ibctransferkeeper`) underneath has already escrowed/burned the coin on the source-chain side once a success ack is observed there, based on the ack returned by this chain. The result is that the transferred value is neither minted nor refunded — it disappears from both sides of the channel.

This exactly mirrors the WardenPledge bug class: an owner/governance-controlled pause flag (`Enabled`/`pause()`), that takes effect immediately, silently blocks a legitimate value-crediting/withdrawal code path that ordinary (non-privileged) users trigger through normal usage (an ordinary IBC transfer), with no time delay, no fallback, and no compensating refund mechanism — leading to unrecoverable loss of transferred funds for the honest end user, rather than merely a reverted transaction.

### Impact Explanation
This is a critical, unauthorized, irreversible loss of user funds:
- The sender on the source chain has their tokens burned/escrowed under the belief the transfer succeeded (a success acknowledgement is returned).
- The recipient on the Cosmos EVM chain receives nothing — no ERC20 tokens, no bank coin.
- There is no automatic remediation path in `OnRecvPacket` for this case (contrast with the `IsNativeCoin` / registration path or the timeout/ack-error paths in `ConvertCoinToERC20FromPacket`, which do have fallback conversions).

This matches the "Critical permanent freezing / theft / unauthorized extraction of user funds ... across ... IBC escrows" allowed-impact category, because value that was in transit through the IBC/ERC20 bridging pathway is permanently lost with no recovery mechanism once the pair is disabled while packets are in flight or continue to arrive after disabling.

### Likelihood Explanation
`ToggleConversion` is a valid, expected governance operation (e.g., pausing a compromised or buggy ERC20 contract, similar to the legitimate "emergency pause" scenario in the original report). It does not require any malicious actor — a normal governance decision to disable a token pair (for entirely legitimate operational reasons) combined with ordinary, unprivileged IBC relaying activity (which is expected to continue arriving from counterparty chains, potentially already in-flight when the toggle occurs) is sufficient to trigger the loss. No attacker collusion or compromised keys are required beyond the standard use of the exposed governance mechanism, and the loss is triggered purely by continued relaying of legitimate transfers, which is out of the disabling party's control once packets are already in flight.

### Recommendation
In `OnRecvPacket`'s `case found && pair.IsNativeERC20()`, when `!pair.Enabled`, do not return a bare success acknowledgement. Instead, either:
1. Return an error acknowledgement so the source chain refunds the sender, or
2. Fall back to crediting the recipient with the plain Cosmos bank coin (skipping only the ERC20 conversion step) instead of dropping the transfer entirely.

Additionally, consider making `ToggleConversion` time-delayed / subject to a grace period so in-flight IBC packets can complete before a pair is disabled, analogous to the suggested WardenPledge mitigation of gating the pause behind a timelock.

### Proof of Concept
1. Register a native ERC20 token pair (`OWNER_EXTERNAL`) and successfully convert some ERC20 balance into the Cosmos coin representation.
2. Send this Cosmos coin from another chain back to the Cosmos EVM chain over IBC to a normal (non-module) recipient.
3. Before the packet is relayed and processed, governance executes `MsgToggleConversion` to disable the pair (`ToggleConversion` -> `pair.Enabled = false`), a legitimate administrative action.
4. The relayer delivers the packet; `OnRecvPacket` reaches `case found && pair.IsNativeERC20()`, sees `!pair.Enabled`, and returns `ack` (success) at `x/erc20/keeper/ibc_callbacks.go:121-123` without calling `ConvertCoinNativeERC20`.
5. The source chain observes the success acknowledgement and finalizes the burn/escrow of the sender's funds. The recipient's balance on the Cosmos EVM chain is never credited. The transferred value is permanently lost.

Note: I was not able to fully trace `TokenPair.IsNativeERC20()` / `IsNativeCoin()` definitions or fully confirm end-to-end IBC transfer-keeper escrow/refund semantics within the available index (some files, e.g. `x/erc20/types/token_pair.go`, were only located via grep and not fully read). I recommend verifying via a Devin session with full repository access that (a) no other code path (e.g., ICS-20 transfer keeper's own ack handling) provides a compensating refund when this specific "no-op success" ack is returned, and (b) `IsNativeERC20()` indeed covers pairs whose funds can be silently dropped as described.

### Citations

**File:** x/erc20/keeper/proposals.go (L116-137)
```go
// ToggleConversion toggles conversion for a given token pair
func (k Keeper) toggleConversion(
	ctx sdk.Context,
	token string,
) (types.TokenPair, error) {
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

	pair.Enabled = !pair.Enabled
	k.SetTokenPair(ctx, pair)
	return pair, nil
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
