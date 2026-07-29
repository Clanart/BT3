### Title
Silent error swallowing in `ConvertCoinToERC20FromPacket` permanently strands refunded bank coins escrowed to the `erc20` module account - (File: `x/erc20/keeper/ibc_callbacks.go`)

### Summary
The exploit hypothesis about a stale ERC20 total-supply/allowance entry is not quite what the code produces, but the underlying root cause it points at — `ConvertCoinToERC20FromPacket` silently swallowing a reverting/self-destructed ERC20 contract error after already mutating bank state — is real and produces a more direct impact: permanent loss of the just-refunded bank coin, which gets escrowed into the `erc20` module account and never returned to the user nor burned.

### Finding Description
When an ICS20 transfer of a native-ERC20-backed coin (`pair.IsNativeERC20()`) receives an error acknowledgement, `v2.IBCModule`/`IBCMiddleware.OnAcknowledgementPacket` first lets the embedded transfer module refund the escrowed bank coin to the sender, then calls `erc20.Keeper.OnAcknowledgementPacket`, which calls `ConvertCoinToERC20FromPacket`: [1](#0-0) 

For a native-ERC20 pair, this in turn calls `ConvertCoinNativeERC20`, which (1) escrows the just-refunded bank coin from the sender to the `erc20` module account via `SendCoinsFromAccountToModule`, and then (2) calls the ERC20 contract's `transfer` from the module to the sender to give back the token representation: [2](#0-1) 

If step (2) fails — e.g. because the token pair's ERC20 contract is malicious and reverts or has self-destructed, as explicitly documented ("an attempt to call a self-destructed ERC20 contract or an invalid function") — `ConvertCoinNativeERC20` returns the error **before** reaching the balance-invariance check and the `BurnCoins` call: [3](#0-2) 

Critically, `ConvertCoinToERC20FromPacket` catches this error, emits a failure event, and returns `nil` instead of propagating it: [4](#0-3) 

In the normal, directly-invoked `MsgConvertCoin` path (`x/erc20/keeper/msg_server.go`, `ConvertCoin`), a failure of `ConvertCoinNativeERC20` propagates as a message error, which causes the SDK to discard the branched store for that message — so the escrow step is automatically rolled back. But in the IBC-ack/timeout callback path, the error is deliberately swallowed so that the packet acknowledgement processing (and thus the whole tx) succeeds. This breaks the implicit atomicity assumption: the bank-coin escrow from the sender to the `erc20` module account is **not** rolled back, and because the function returned before `BurnCoins`, those coins are neither burned nor forwarded to the sender. They become permanently stuck in the `erc20` module account with no code path to return them.

`OnTimeoutPacket` has the identical issue since it calls the same `ConvertCoinToERC20FromPacket`: [5](#0-4) 

### Impact Explanation
This is a critical, permanent loss of user funds: the sender's bank-coin balance (which had just been refunded by the ICS20 refund logic) is silently debited into the `erc20` module account and left there forever, with no ERC20 tokens delivered and no compensating burn/mint accounting. The documented behavior ("the user receives the corresponding bank token from the TokenPair instead") is false in this scenario — the user receives nothing and the coin is effectively stuck/lost. This is a genuine violation of the "preserve 1:1 accounting" invariant for token-pair-backed balances in the asset-representation path.

### Likelihood Explanation
The attacker needs to control (or convince) an ERC20 contract used in a registered native-ERC20 token pair to revert or self-destruct its `transfer` function — a scenario explicitly anticipated by the code's own doc comments, meaning it's a realistically foreseen attack surface, not a contrived edge case. Any user (attacker or otherwise) who converts coins backed by such a malicious/self-destructing contract, sends them over IBC, and receives an error-ack or timeout, will trigger the stuck-funds condition. I was not able to fully verify within the available iterations whether native-ERC20 token-pair registration is fully permissionless or requires some privileged step (e.g., `RegisterERC20` governance gating) — this affects whether an unprivileged attacker can set up the malicious contract/pair unilaterally, and is a point of residual uncertainty that a full audit should confirm by inspecting `x/erc20/keeper/msg_server.go` `RegisterERC20` and its `ValidateBasic`/authority checks.

### Recommendation
In `ConvertCoinToERC20FromPacket`, when `ConvertCoinNativeERC20` fails, either (a) perform the coin escrow/unescrow steps inside a `CacheContext`/`writeFn` pattern (as done elsewhere for EVM callback execution, e.g. `x/ibc/callbacks/keeper/keeper.go`) so a failed conversion leaves no partial state change, and only commit on full success; or (b) reorder `ConvertCoinNativeERC20` so the bank-coin escrow only happens after the ERC20 transfer has been confirmed successful, mirroring the safe atomic behavior of the directly-invoked `MsgConvertCoin` path.

### Proof of Concept
1. Register a native-ERC20 token pair (`ContractOwner = OWNER_EXTERNAL`) backed by a contract whose `transfer` function can be made to revert or that can self-destruct.
2. Convert some native coin from that ERC20 to the Cosmos coin representation via `MsgConvertERC20`, receiving the bank-coin balance.
3. Send that bank coin over IBC via `MsgTransfer`.
4. Cause an error acknowledgement (or timeout) for that packet.
5. Before/at the point of the callback's re-conversion attempt, make the ERC20 contract revert on `transfer` (or self-destruct it).
6. Observe: the sender's bank-coin balance is debited into the `erc20` module account (via `SendCoinsFromAccountToModule` inside `ConvertCoinNativeERC20`), the ERC20 `transfer` call fails, `ConvertCoinToERC20FromPacket` swallows the error and returns `nil`, and the overall `OnAcknowledgementPacket`/`OnTimeoutPacket` call succeeds. Assert: sender's bank balance for that denom decreased with no offsetting ERC20 balance increase, and the `erc20` module account balance for that denom increased and remains un-burned — demonstrating a permanent, unrecoverable loss of the user's funds.

### Citations

**File:** x/erc20/keeper/ibc_callbacks.go (L164-178)
```go
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

**File:** x/erc20/keeper/ibc_callbacks.go (L180-188)
```go
// OnTimeoutPacket converts the IBC coin to ERC20 after refunding the sender
// since the original packet sent was never received and has been timed out.
// If the ERC20 conversion fails for whatever reason, such as an attempt to call
// a self-destructed ERC20 contract or an invalid function, OnTimeoutPacket still
// succeeds, but the user receives the corresponding bank token from the TokenPair
// instead. A user may then manually re-attempt the conversion.
func (k Keeper) OnTimeoutPacket(ctx sdk.Context, _ channeltypes.Packet, data transfertypes.FungibleTokenPacketData) error {
	return k.ConvertCoinToERC20FromPacket(ctx, data)
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

**File:** x/erc20/keeper/msg_server.go (L299-306)
```go
	// Burn escrowed Coins
	err = k.bankKeeper.BurnCoins(ctx, types.ModuleName, coins)
	if err != nil {
		return sdkerrors.Wrap(err, "failed to burn coins")
	}

	return nil
}
```
