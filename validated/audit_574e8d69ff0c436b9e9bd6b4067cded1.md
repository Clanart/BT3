### Title
Native ERC20 IBC-receive callback returns an ErrorAcknowledgement *after* the underlying transfer has already unescrowed/credited funds, causing double-crediting of value across chains - (File: x/erc20/keeper/ibc_callbacks.go)

### Summary
`x/erc20/keeper/ibc_callbacks.go`'s `OnRecvPacket` (invoked from `x/erc20/ibc_middleware.go` `IBCMiddleware.OnRecvPacket`) runs *after* the standard ICS20 transfer application has already completed the receive (unescrow/mint to the recipient) and returned a success acknowledgement. For "native ERC20" token pairs (`pair.IsNativeERC20()`), the middleware then attempts to auto-convert the just-credited bank coin into its ERC20 representation via `k.ConvertCoinNativeERC20`, which calls into the token's EVM contract with `CallEVM(..., "transfer", receiver, amount)`. If that EVM call fails for any reason intrinsic to the ERC20 contract (blacklist, pause, insufficient allowance/logic quirks, `ErrBalanceInvariance`, etc. — the direct analog of the reported blacklist-transfer issue), the function returns `channeltypes.NewErrorAcknowledgement(err)` instead of falling back to the already-successful `ack`. [1](#0-0) 

### Finding Description
`IBCMiddleware.OnRecvPacket` first calls the underlying (wrapped) ICS20 transfer module's `OnRecvPacket`, which performs the actual coin movement (unescrow for a returning native token) and yields a success acknowledgement; the erc20 middleware only proceeds to its own callback if that first ack succeeded: [2](#0-1) 

The erc20 keeper's `OnRecvPacket` then tries to convert the coin into its ERC20 representation for `pair.IsNativeERC20()` pairs. `MintingEnabled` or `ConvertCoinNativeERC20` failing (e.g., due to a receiver blocked by contract-side logic, a paused contract, or the invariant checks inside `ConvertCoinNativeERC20`) causes the function to return an `ErrorAcknowledgement`: [3](#0-2) 

`ConvertCoinNativeERC20` itself performs `SendCoinsFromAccountToModule` (escrowing the bank coin that was JUST credited to the recipient by the underlying transfer app) before attempting the EVM-side unescrow transfer; if the EVM transfer subsequently fails, the function returns an error, and — critically — this happens inside the same atomic packet-processing call, so the state mutations performed by the *outer*, already-successful transfer-module `OnRecvPacket` are not automatically undone by returning an `Acknowledgement` value (as opposed to a Go `error` from `OnRecvPacket`, which is what would trigger a message-level state revert in the IBC core handler). Returning an `ErrorAcknowledgement` here does not roll back application state; it only causes the core IBC handler to persist and relay an error acknowledgement back to the counterparty/source chain. [4](#0-3) 

On the source chain, an error acknowledgement is the standard signal to refund the original sender (re-crediting the escrowed/burned amount there). Since the destination chain already finalized its half of the transfer (the recipient's bank balance already reflects the received coin — only the *ERC20-conversion* attempt failed, not the underlying IBC receive), refunding the sender on the source chain in response to this "failed" ack creates two live claims to the same value: the recipient's bank coin balance on the destination chain, and the refunded/reminted balance on the source chain. This directly matches the report's core class of bug — a "process" step that partially completes (asset movement) but is followed by a revertible/erroring step that has no matching rollback, leaving inconsistent, unauthorized-duplicate state — except here the impact is duplication of spendable value across chains rather than a stuck state machine.

Notably, the module's own documentation comment for the parallel `OnAcknowledgementPacket`/`OnTimeoutPacket` flows explicitly states the intended, safe behavior: "If the ERC20 conversion fails for whatever reason ... OnTimeoutPacket still succeeds, but the user receives the corresponding bank token from the TokenPair instead" — i.e., failure to convert should fall back to leaving the user with bank coins, not fail the whole operation: [5](#0-4) 

But `OnRecvPacket`'s Case 2 does not follow this same safe pattern — it converts a conversion failure into a packet-level `ErrorAcknowledgement`, which is a different, higher-severity outcome tied to the packet lifecycle rather than a local no-op.

### Impact Explanation
If confirmed on ibc-go's actual acknowledgement/refund semantics (not independently verifiable from this index — see Likelihood/Uncertainty below), this bug allows unauthorized duplication of spendable value: the recipient keeps the already-unescrowed bank coin on the destination chain while the sender is refunded on the source chain for the "failed" packet, effectively minting a duplicate claim on the same underlying escrowed/ERC20-backed value. This falls squarely into the allowed Critical impact category: "unauthorized minting, burning, duplication, or irreversible accounting corruption of spendable user value across ... IBC escrows."

### Likelihood Explanation
Triggering requires only an unprivileged action: sending a "native ERC20" token pair (a token that originated on this chain via `ConvertERC20`) back to this chain via ordinary IBC transfer, addressed to a recipient for whom the underlying ERC20 contract's `transfer` call will revert (blacklist, pause, or any other business logic causing `ConvertCoinNativeERC20`/`MintingEnabled` to fail). No validator, relayer, or governance privilege is needed — an ordinary user (or the attacker acting as their own recipient/sender across two chains) can construct this scenario deterministically once they control or interact with a token pair whose ERC20 contract has any transfer-blocking condition. This is a very plausible and realistic occurrence for any compliant/blacklist-capable ERC20 registered as a native token pair, which is explicitly the scenario called out in the seed report.

### Recommendation
In `x/erc20/keeper/ibc_callbacks.go`'s `OnRecvPacket`, when `MintingEnabled` or `ConvertCoinNativeERC20` fails for an already-successfully-received native ERC20 coin, do not return an `ErrorAcknowledgement` (which signals packet failure and triggers source-chain refund). Instead, follow the same fallback pattern documented and used for `OnAcknowledgementPacket`/`OnTimeoutPacket`: log/emit the failure event and return the original successful `ack`, leaving the recipient with the bank-coin representation instead of the ERC20 token, allowing a manual re-attempt of the conversion later. This preserves 1:1 accounting between the two chains regardless of ERC20-side conversion failures.

### Proof of Concept
Conceptual PoC (not independently executed against a live ibc-go acknowledgement/refund handler in this pass — flagged as an assumption to verify):
1. Register an ERC20 contract with owner-controlled blacklist/pause logic as a native token pair (`RegisterERC20`).
2. Convert some ERC20 balance to native coin via `MsgConvertERC20`, escrowing tokens in the module account.
3. IBC-transfer the resulting coin out to chain B (`MsgTransfer`), so chain A's module account holds the ERC20 escrow and chain B credits the voucher.
4. Blacklist/pause the intended recipient address on the underlying ERC20 contract on chain A.
5. From chain B, IBC-transfer the voucher back to chain A, addressed to the blacklisted recipient.
6. On chain A, `IBCMiddleware.OnRecvPacket` → underlying transfer app unescrows the bank coin to the recipient (success ack) → `erc20 Keeper.OnRecvPacket` Case 2 attempts `ConvertCoinNativeERC20`, whose EVM `transfer` call reverts due to the blacklist, so the function returns `channeltypes.NewErrorAcknowledgement(err)`.
7. This error acknowledgement propagates back to chain B, which (per standard ICS20 semantics) refunds/re-mints the equivalent voucher back to the chain-B sender — while the recipient on chain A retains the already-unescrowed bank coin from step 6, resulting in double-counted value across chains.

Verifying step 7's exact refund behavior requires tracing ibc-go's core `RecvPacket`/`WriteAcknowledgement` and the counterparty's `OnAcknowledgementPacket` refund logic, which was not directly located in this index within the available search budget — a Devin session with full repository/dependency access should confirm the exact ibc-go v10 refund trigger conditions before finalizing severity.

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

**File:** x/erc20/keeper/ibc_callbacks.go (L156-188)
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

**File:** x/erc20/keeper/msg_server.go (L237-266)
```go
func (k Keeper) ConvertCoinNativeERC20(
	ctx sdk.Context,
	pair types.TokenPair,
	amount math.Int,
	receiver common.Address,
	sender sdk.AccAddress,
) error {
	if !amount.IsPositive() {
		return sdkerrors.Wrap(types.ErrNegativeToken, "converted coin amount must be positive")
	}

	erc20 := contracts.ERC20MinterBurnerDecimalsContract.ABI
	contract := pair.GetERC20Contract()

	balanceToken := k.BalanceOf(ctx, erc20, contract, receiver)
	if balanceToken == nil {
		return sdkerrors.Wrap(types.ErrEVMCall, "failed to retrieve balance")
	}

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
