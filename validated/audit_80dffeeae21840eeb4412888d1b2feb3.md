This confirms the analog. The base transfer module's `OnRecvPacket` (`im.Module.OnRecvPacket`) mints the IBC voucher coins to the recipient and returns a success acknowledgement *before* the erc20 middleware's conversion logic runs. Only if that succeeds does `im.keeper.OnRecvPacket` get called to convert the coin into its ERC20 representation via `ConvertCoinNativeERC20`.

### Title
Unrecoverable duplication of transferred value when ERC20 conversion fails after IBC voucher mint and escrow in `OnRecvPacket` - (File: `x/erc20/keeper/ibc_callbacks.go`)

### Summary
`OnRecvPacket` mirrors the report's pattern of an action ("mint receipt"/here "mint IBC voucher + escrow coin") being finalized independently of a later, related action ("claim reward"/here "unescrow ERC20 token") that can fail, and the failure path (error acknowledgement) does not roll back the funds already moved in the first step.

### Finding Description
`IBCMiddleware.OnRecvPacket` first calls the base ICS20 transfer module, which mints IBC voucher coins to the recipient and returns a *success* acknowledgement [1](#0-0) . Only after that success is `k.OnRecvPacket` invoked to convert the coin to its ERC20 representation via `MintingEnabled` + `ConvertCoinNativeERC20` [2](#0-1) .

Inside `ConvertCoinNativeERC20`, coins are escrowed from the recipient into the erc20 module account (`SendCoinsFromAccountToModule`) *before* the EVM `transfer` call that unescrows the ERC20 token to the recipient [3](#0-2) . If that EVM call fails or returns invariant errors afterward, the function returns a Go `error`, and the caller wraps it into `channeltypes.NewErrorAcknowledgement(err)` — an `Acknowledgement` value, not a returned Go `error` from the ABCI message handler [4](#0-3) . Because IBC's core packet-receive processing only reverts application state when `OnRecvPacket` returns a `nil` acknowledgement panic/error at a higher level — returning a non-nil `Acknowledgement` object (even one that "fails") is treated as a normal, committed state transition; only the ack bytes written into the packet receipt communicate failure to the counterparty. That means the escrow of coins into the erc20 module account already occurred and is persisted, and the original voucher mint from the base transfer app is also persisted.

Meanwhile, the sending (source) chain, upon receiving this `Acknowledgement_Error`, will refund the original locked/burned tokens back to the original sender via its own `OnAcknowledgementPacket` handling — exactly like the unbacked "receipt path" in the report where the owner already reclaimed funds while a receipt could still be minted later. Here, the destination chain retains coins locked in the erc20 module's escrow bucket (never burned, since the ERC20 `transfer` step failed and `BurnCoins` in `ConvertCoinNativeERC20` is never reached) while the source chain treats the transfer as failed and unwinds its own escrow/burn, i.e., the same value now exists as both a refunded balance on the source chain and a stranded/escrowed balance on the destination chain — a duplication of spendable value across chains, matching the "Critical unauthorized minting/duplication of spendable user value across ... IBC escrows" impact category.

This is a good structural analog to the original finding (state committed for one leg of a two-step user flow, while the second leg's failure is only communicated advisory and doesn't unwind the first leg) but I was **not able to fully verify** two crucial facts in the time available:
1. Whether Cosmos SDK's/ibc-go's packet-receive processing actually treats a non-nil `Acknowledgement` value that signals failure as "commit state, but write error ack" versus reverting all execution when the ack is an error. This distinction is on the ibc-go/cosmos-sdk side and I did not trace through ibc-go's channel keeper `RecvPacket` handling in this codebase to confirm whether OnRecvPacket state changes are cached/rolled back on ack failure. Existing test coverage (`TestOnRecvPacketRegistered`) exercises this scenario, and its assertions would clarify whether coins are actually stuck in escrow versus refunded to the receiver — I could not fully inspect the full test malleate/expectations to confirm the final balance outcome.
2. Whether `ConvertCoinNativeERC20`'s failure modes (post-escrow) are practically reachable by an unprivileged/ordinary user (e.g., forcing the ERC20 `transfer` call to fail after tokens have already been escrowed in module custody from a prior `ConvertERC20`, such as a malicious/blacklisting ERC20 token contract, or a contract that reverts transfers to specific addresses) without requiring a privileged actor.

### Impact Explanation
If confirmed, this results in coins being permanently escrowed/stranded in the erc20 module account with no corresponding ERC20 tokens delivered to the user, while the source chain independently refunds the sender — a duplication/inflation of spendable value across the two chains' accounting, which is Critical under the "unauthorized minting, duplication ... across IBC escrows" impact gate.

### Likelihood Explanation
Likelihood depends on being able to reliably make the ERC20 `transfer` step fail (e.g., via a malicious/blacklist ERC20 token contract registered as a token pair) after `SendCoinsFromAccountToModule` already succeeded, and depends on ibc-go's actual acknowledgement-state-commit semantics, both of which I was unable to fully confirm from the code alone within scope.

### Recommendation
Given the uncertainty above, I cannot produce a fully substantiated Critical finding with a verified proof of concept. I recommend a background engineering task to:
1. Trace ibc-go's `channelKeeper.RecvPacket` / `WriteAcknowledgement` flow to confirm whether returning an `Acknowledgement_Error` from `OnRecvPacket` persists prior module state changes (mint + escrow) or is rolled back.
2. If state is persisted, add an explicit refund/burn of the escrowed coins (mirroring `ConvertCoinToERC20FromPacket`'s cleanup) inside the error path of `k.OnRecvPacket`/`ConvertCoinNativeERC20`, so that a failed ERC20 conversion always returns the recipient's bank coins rather than leaving them escrowed in the module account.

### Proof of Concept
Not constructed — reaching a definitive Critical PoC requires confirming ibc-go's state-commit behavior on error acknowledgements and constructing a token pair backed by an ERC20 contract that reliably reverts its `transfer` function only when called from the module address after tokens are already escrowed (e.g., a blacklist-based ERC20). This should be validated in the sandbox before treating this as a confirmed Critical.

### Citations

**File:** x/erc20/ibc_middleware.go (L53-66)
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
