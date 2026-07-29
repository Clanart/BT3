### Title
Non-atomic native-ERC20 IBC-receive conversion allows permanently stuck escrowed coins and duplicated (double-refunded) value - (File: `x/erc20/keeper/ibc_callbacks.go`, `x/erc20/keeper/msg_server.go`)

### Summary
This is the direct analog of the Halborn "blacklisted-USDC-bidder" bug class: a multi-step transfer/accounting flow that is *not atomic* can partially commit state and then fail on a later step, permanently stranding value with no way to unwind it. In `AuctionUpgradeable`, the stranded value was the sell tokens behind a reverting `transfer()`. In Cosmos EVM's native-ERC20 IBC receive path (`x/erc20/keeper/ibc_callbacks.go:OnRecvPacket`, Case 2, calling `x/erc20/keeper/msg_server.go:ConvertCoinNativeERC20`), the equivalent stranded value is bank coins escrowed into the `erc20` module account that are never burned and never returned to the user, while the IBC acknowledgement is simultaneously converted to an error, triggering a refund on the source chain — producing duplicated value across chains.

### Finding Description
`IBCMiddleware.OnRecvPacket` (`x/erc20/ibc_middleware.go:53-67`) first calls the underlying ICS20 transfer `im.Module.OnRecvPacket`, which — on success — has already **committed** the bank-coin credit to the recipient directly on the live `ctx` (no `CacheContext`/`writeFn` pattern is used anywhere in this path, unlike `x/ibc/callbacks/keeper/keeper.go` which explicitly uses `ctx.CacheContext()` + `writeFn()` for its EVM callback flow). Only after that commit does it call `k.keeper.OnRecvPacket` [1](#0-0) .

Inside `x/erc20/keeper/ibc_callbacks.go`, for "Case 2. native ERC20 token" [2](#0-1) , `MintingEnabled` is checked and then `ConvertCoinNativeERC20` is invoked. If either fails, the function returns `channeltypes.NewErrorAcknowledgement(err)` — turning what was already a *successful* underlying transfer into an *error* acknowledgement.

`ConvertCoinNativeERC20` (`x/erc20/keeper/msg_server.go:237-306`) itself performs a non-atomic three-step sequence directly on the live context:
1. `SendCoinsFromAccountToModule` — escrows the recipient's bank coins into the `erc20` module account [3](#0-2) .
2. `CallEVM(... "transfer" ...)` — unescrows ERC20 tokens from the module's EVM balance to the receiver [4](#0-3) .
3. Only if steps 1–2 succeed and balance invariants check out, `BurnCoins` finally burns the escrowed coins [5](#0-4) .

If step 2 or the post-transfer invariant check fails (e.g., EVM call reverts, unexpected `Approval` event, self-destructed/misbehaving ERC20 contract, out-of-gas, or any other CallEVM failure), the function returns an error **after step 1 has already been committed** — the coins are now sitting escrowed in the `erc20` module account, permanently un-burned and never delivered to the user as ERC20 tokens.

That error is what propagates back up through `OnRecvPacket` as `NewErrorAcknowledgement`. Because IBC core does not treat "error acknowledgement content" as a signal to roll back application-level state mutations already performed during the (successful, `ack.Success()==true`) `OnRecvPacket` call, the destination chain keeps: (a) coins now permanently stuck in the `erc20` module account (never minted as ERC20, never burned, never returned), while (b) the error acknowledgement, once relayed, causes the **source chain** to refund the original sender via the standard ICS20 `OnAcknowledgementPacket` error path. This yields duplicated value: the sender is refunded on the source chain while equivalent value is trapped/unaccounted-for on the destination chain.

The design intent, per the doc comment on `IBCMiddleware.OnRecvPacket`, is: *"If conversion fails, then the user will receive the bank token instead"* [6](#0-5)  — i.e., conversion failure should fall back to leaving the recipient with the bank coin (matching how "Token pair is disabled" returns the original success `ack` at [7](#0-6) ). But the actual Case 2 code path does not follow this design when `MintingEnabled`/`ConvertCoinNativeERC20` fail — it overwrites the ack to an error instead of returning the already-successful `ack`, and by that point coins may have already been escrowed away from the recipient in `ConvertCoinNativeERC20`.

### Impact Explanation
This maps to the Critical impact gate for "irreversible accounting corruption... across native balances... or precompile-mediated assets" and "permanent freezing/locking... of user funds... or token-pair-backed balances." Coins escrowed into the `erc20` module account during a failed `ConvertCoinNativeERC20` call are never burned and never delivered as ERC20 to the recipient — they are permanently orphaned in module-account balance with no code path to recover or re-trigger conversion for that specific already-received amount. Simultaneously, the error acknowledgement causes the sending chain to refund the original sender, so total spendable value across the two chains increases beyond what should exist for that single transfer — a duplication of user value.

### Likelihood Explanation
Trigger conditions are directly reachable by any unprivileged IBC packet participant transferring a token whose `TokenPair.IsNativeERC20()` is true: any failure in the EVM `transfer` call inside `ConvertCoinNativeERC20` (contract paused/self-destructed, unexpected `Approval` event validation failure, mismatched invariant check, or simply a poorly-behaved/upgraded ERC20 contract) is sufficient, and no attacker privilege is required — only that the destination-chain ERC20 contract backing the token pair reverts or misbehaves on `transfer` at the time a receive is processed. This mirrors the audit report's trigger condition (a token where a downstream transfer step can fail) exactly, just replacing "USDC blacklist" with "any native-ERC20 contract-level transfer failure."

### Recommendation
Wrap the entirety of `k.keeper.OnRecvPacket`'s Case 2 conversion logic (`MintingEnabled` + `ConvertCoinNativeERC20`) in a `ctx.CacheContext()`/`writeFn()` pattern, only committing state if the full conversion (including `BurnCoins`) succeeds; otherwise discard all partial mutations and return the original **success** acknowledgement (matching the documented fallback behavior of leaving the recipient with the bank coin), rather than converting it into an error acknowledgement that both strands the escrowed value and triggers a source-chain refund. Additionally, make `ConvertCoinNativeERC20`'s escrow/unescrow/burn sequence atomic (single cached-context commit) so a mid-sequence failure never leaves coins escrowed in the module account without a corresponding follow-up mint/refund path.

### Proof of Concept
Not independently executed/verified in a live testnet; based on static code tracing:
1. Register a `TokenPair` where the ERC20 contract can be made to fail its `transfer` function to a specific receiver post-deployment (e.g., a pausable/upgradeable ERC20, or one that reverts under certain conditions) and mark it as `IsNativeERC20()`.
2. Relay an ICS20 transfer of this token's IBC-representation to the destination chain, targeting a receiver for which the ERC20 `transfer` step in `ConvertCoinNativeERC20` will fail (contract paused, or manipulated to revert) after the escrow (`SendCoinsFromAccountToModule`) step succeeds.
3. Observe `OnRecvPacket`: underlying ICS20 transfer commits coin credit to receiver; `ConvertCoinNativeERC20` escrows the coin into the `erc20` module account, then the EVM `transfer` call fails; the error propagates up and `OnRecvPacket` returns `NewErrorAcknowledgement`.
4. Confirm on the destination chain that the escrowed coins sit in the `erc20` module account balance, are not burned, and the receiver holds neither the bank coin nor the ERC20 token.
5. Confirm the relayed error acknowledgement causes the source chain's `OnAcknowledgementPacket` error path to refund the original sender's escrowed tokens, completing the duplication (destination-side coins stuck in `erc20` module account + source-side sender refunded).

### Citations

**File:** x/erc20/ibc_middleware.go (L46-52)
```go
// OnRecvPacket implements the IBCModule interface.
// It receives the tokens through the default ICS20 OnRecvPacket callback logic
// and then automatically converts the Cosmos Coin to their ERC20 token
// representation.
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

**File:** x/erc20/keeper/msg_server.go (L256-260)
```go
	// Escrow Coins on module account
	coins := sdk.Coins{{Denom: pair.Denom, Amount: amount}}
	if err := k.bankKeeper.SendCoinsFromAccountToModule(ctx, sender, types.ModuleName, coins); err != nil {
		return sdkerrors.Wrap(err, "failed to escrow coins")
	}
```

**File:** x/erc20/keeper/msg_server.go (L262-282)
```go
	// Unescrow Tokens and send to receiver
	res, err := k.evmKeeper.CallEVM(ctx, erc20, types.ModuleAddress, contract, true, nil, "transfer", receiver, amount.BigInt())
	if err != nil {
		return err
	}

	// Check unpackedRet execution
	var unpackedRet types.ERC20BoolResponse
	if len(res.Ret) == 0 {
		// if the token does not return a value, check for the transfer event in logs
		if err := validateTransferEventExists(res.Logs, contract); err != nil {
			return err
		}
	} else {
		if err := erc20.UnpackIntoInterface(&unpackedRet, "transfer", res.Ret); err != nil {
			return err
		}
		if !unpackedRet.Value {
			return sdkerrors.Wrap(errortypes.ErrLogic, "failed to execute unescrow tokens from user")
		}
	}
```

**File:** x/erc20/keeper/msg_server.go (L299-303)
```go
	// Burn escrowed Coins
	err = k.bankKeeper.BurnCoins(ctx, types.ModuleName, coins)
	if err != nil {
		return sdkerrors.Wrap(err, "failed to burn coins")
	}
```
