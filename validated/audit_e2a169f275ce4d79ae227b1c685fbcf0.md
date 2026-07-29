This confirms the vulnerability. The middleware `im.keeper.OnRecvPacket` at `x/erc20/ibc_middleware.go:53-67` is called **after** `im.Module.OnRecvPacket` has already committed its state changes directly on `ctx` (not a cached context) — the ICS20 transfer application in `ibc/module.go:97-104` mints/unescrows real bank coins to the recipient as part of `ack.Success()`. If the erc20 keeper's post-processing step in `x/erc20/keeper/ibc_callbacks.go:100-139` (either `RegisterERC20Extension`, `MintingEnabled`, or `ConvertCoinNativeERC20`) then fails, the code returns `channeltypes.NewErrorAcknowledgement(err)` **without reverting** the already-committed ICS20 mint/unescrow. This is confirmed by the test at `evmd/tests/ibc/ibc_middleware_test.go:748-808`, which explicitly asserts `errAck.Success()` is `false` while the bank coins remain credited (`trappedBal.Amount` equals `recvAmt`).

### Title
IBC transfer + ERC20 middleware error-acknowledgement causes token supply duplication across chains - (File: `x/erc20/ibc_middleware.go`, `x/erc20/keeper/ibc_callbacks.go`)

### Summary
`x/erc20/keeper/ibc_callbacks.go`'s `OnRecvPacket` mirrors the Axelar `XC20Wrapper` bug class (an operation that can revert/fail after value has already moved is not safely handled), but instead of tokens being locked, it produces token duplication because the underlying ICS20 mint/unescrow is never rolled back when the later ERC20 conversion step fails.

### Finding Description
`IBCMiddleware.OnRecvPacket` (`x/erc20/ibc_middleware.go:53-67`) first calls `im.Module.OnRecvPacket`, which invokes the core ICS20 transfer app directly on the live `ctx` [1](#0-0) . That call mints new voucher coins or unescrows previously-escrowed native coins to the recipient's real bank balance and returns a **success** acknowledgement (state already committed, no cache/rollback wrapper is used anywhere in this call chain) [2](#0-1) .

Only after that committed mutation does the middleware invoke `k.OnRecvPacket`, which attempts to further convert the coin into its ERC20 representation. If `RegisterERC20Extension`, `MintingEnabled`, or `ConvertCoinNativeERC20` fails (e.g., disabled ERC20 token pair, `SendEnabled=false`, blocked receiver, self-destructed ERC20 contract, or any other legitimately-reachable, unprivileged-triggerable condition), the function returns `channeltypes.NewErrorAcknowledgement(err)` [3](#0-2) . Nothing reverts the bank-level mint/unescrow that the wrapped ICS20 app already performed.

An IBC `MsgRecvPacket` handler does not fail/revert merely because it writes an error acknowledgement — writing an error ack is itself a valid, successfully-committed state transition (`ChannelKeeper.WriteAcknowledgement`). This means:
- On the destination (this) chain: the recipient keeps the newly minted/unescrowed bank coins.
- On the source chain: upon receiving the error acknowledgement, ICS20's ack handler executes refund logic — re-minting burned vouchers or unescrowing the sender's originally-escrowed coins back to the sender, exactly as demonstrated by this repo's own test assertions (`evmd/tests/ibc/ibc_middleware_test.go:1238-1269`, `v2_transfer_test.go:437-468`).

The result is that both the sender (refunded on the source chain) and the receiver (already credited on this chain) simultaneously hold spendable value derived from a single transfer — a duplication of the underlying token supply across the two chains. This directly parallels the report's root cause class ("assume no revert can happen after value has moved, but a revert/failure is reachable"), except mitigated here into a duplication bug rather than a lock, because the developers added a "fallback to bank token" behavior (as the report's own recommended mitigation suggests) without also making the whole `OnRecvPacket` operation atomic with the ICS20 mint/unescrow it wraps.

Note: this behavior is exercised and seemingly accepted/expected by the test suite itself (`ibc_middleware_test.go` and `ibc_callbacks.go` comments explicitly describe "the user receives the corresponding bank token instead" as intended behavior), so the maintainers appear to have only reasoned about single-chain fund safety, not the two-chain consistency implied by the ICS20 acknowledgement contract.

### Impact Explanation
This breaks the IBC/ICS20 invariant that a token transfer with an error acknowledgement must be fully undone end-to-end. Concretely it causes unauthorized duplication of spendable user value across native balances and IBC escrows: the same transferred amount ends up recoverable both by the original sender (via source-chain refund) and the destination recipient (via the retained bank credit), inflating total effective supply of the affected denom without any corresponding burn — a Critical-severity supply/accounting corruption reachable by any unprivileged party who can cause the post-ICS20 ERC20-conversion step to fail (e.g., toggling/disabling a token pair's conversion via a race, hitting `SendEnabled=false`, a blocked/module receiver, or a self-destructed paired ERC20 contract) while relaying an ordinary transfer packet.

### Likelihood Explanation
Likelihood is high: any of the documented failure branches in `OnRecvPacket` (`MintingEnabled` returning an error, `RegisterERC20Extension` failing, `ConvertCoinNativeERC20` failing) is reachable through ordinary, unprivileged IBC packet relaying and is even actively tested for by the existing test suite, meaning triggering the error-ack path requires no special privilege — only conditions that already occur in production use of the module (disabled pairs, blocked addresses, misbehaving/self-destructed ERC20 contracts, or governance toggling in between packet flight).

### Recommendation
Wrap `im.Module.OnRecvPacket` and the subsequent `im.keeper.OnRecvPacket` erc20-conversion logic in a single atomic unit using `ctx.CacheContext()`, committing (`writeFn()`) only if the final acknowledgement is a success. If the erc20 conversion step fails, the entire operation — including the ICS20 mint/unescrow — must be rolled back so that the packet is treated as never having been received on this chain, consistent with the error acknowledgement being propagated back to the source chain. Apply the equivalent fix to `x/erc20/v2/ibc_middleware.go`'s `OnRecvPacket`.

### Proof of Concept
1. Set up a channel where a native ERC20 token pair exists on chain A (the analog of the "destination" chain), registered via `RegisterERC20`.
2. Send an amount `X` of the paired denom from chain B to chain A via `MsgTransfer` (standard ICS20 send on chain B; chain B escrows/burns `X`).
3. Before the packet is relayed, disable the token pair's conversion or the coin's `SendEnabled` on chain A, or otherwise cause `MintingEnabled`/`ConvertCoinNativeERC20` to fail — as exercised in `evmd/tests/ibc/ibc_middleware_test.go:731-754` (`evmApp.BankKeeper.SetSendEnabled(evmCtx, nativeErc20.Denom, false)`).
4. Relay the packet: `transferStack.OnRecvPacket` runs, the wrapped ICS20 `Module.OnRecvPacket` unescrows `X` to the receiver's bank balance (committed on the real ctx), then `k.OnRecvPacket`'s `MintingEnabled`/`ConvertCoinNativeERC20` fails and an error acknowledgement is returned, as asserted at `evmd/tests/ibc/ibc_middleware_test.go:748-808` (`suite.Require().False(errAck.Success())` while the bank balance still holds `recvAmt`).
5. Relay the resulting error acknowledgement back to chain B: chain B's `OnAcknowledgementPacket` refund logic (validated generically at `evmd/tests/ibc/ibc_middleware_test.go:1238-1269` and `v2_transfer_test.go:437-468`) re-credits the original sender with `X`.
6. Result: chain A's receiver retains `X` in bank balance (from step 4) and chain B's sender is refunded `X` (from step 5) — `X` now exists twice, one on each chain, from a single original transfer.

### Citations

**File:** ibc/module.go (L95-104)
```go
// OnRecvPacket implements the Module interface.
// It calls the underlying app's OnRecvPacket callback.
func (im Module) OnRecvPacket(
	ctx sdk.Context,
	channelVersion string,
	packet channeltypes.Packet,
	relayer sdk.AccAddress,
) exported.Acknowledgement {
	return im.app.OnRecvPacket(ctx, channelVersion, packet, relayer)
}
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

**File:** x/erc20/keeper/ibc_callbacks.go (L100-139)
```go
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
