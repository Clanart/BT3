## Finding: Confirmed — Escrowed native coins can be permanently stranded when ERC20 reconversion fails in the IBC timeout/error-ack path

### Title
Silent Swallow of `ConvertCoinNativeERC20` Failure in `ConvertCoinToERC20FromPacket` Permanently Strands Escrowed Native Coins - (File: `x/erc20/keeper/ibc_callbacks.go`)

### Summary
`ConvertCoinToERC20FromPacket` (invoked from `OnAcknowledgementPacket` on error-ack and from `OnTimeoutPacket`) calls `ConvertCoinNativeERC20` to opportunistically convert a sender's refunded native coin back into its ERC20 representation. If `ConvertCoinNativeERC20` fails after it has already escrowed the coin via `SendCoinsFromAccountToModule` but before the compensating `transfer`/`BurnCoins` steps complete, the outer function only emits `EventTypeFailedConvertERC20` and returns `nil` — it never reverses the escrow. [1](#0-0) 

### Finding Description
`ConvertCoinNativeERC20` performs, in order: (1) `SendCoinsFromAccountToModule` to escrow the native coin from the sender into the `erc20` module account, (2) `CallEVM` to `transfer` the corresponding ERC20 tokens from the module address to the receiver, (3) `BurnCoins` on the escrowed coin. [2](#0-1) 

Step (1) is a real, committed bank-keeper state write. If step (2) fails — for example the token contract reverts the `transfer` (blacklist/pause logic, insufficient module-held ERC20 balance, or any other conditional revert), `ConvertCoinNativeERC20` returns a non-nil error without undoing the escrow.

The caller, `ConvertCoinToERC20FromPacket`, catches this error, emits `EventTypeFailedConvertERC20`, and explicitly `return nil`: [1](#0-0) 

Because the function returns `nil` (success) instead of propagating the error, the branched/cached context in which this executed (as part of the relayer's `MsgAcknowledgement`/`MsgTimeout` processing) is committed rather than rolled back. This means the earlier `SendCoinsFromAccountToModule` escrow persists in state while there is no offsetting mint of the coin back to the sender, no successful ERC20 credit to the receiver, and no `BurnCoins` call. The doc comment on `OnAcknowledgementPacket`/`OnTimeoutPacket` acknowledges conversion can fail and claims "the user receives the corresponding bank token... A user may then manually re-attempt the conversion" — but that is false for this specific failure point, since the bank token has *already left the user's account* into the module account before the failure occurs. [3](#0-2) 

This breaks the 1:1 accounting invariant between native coin escrow and ERC20 representation that the erc20 module is designed to preserve (coins are neither burned nor represented by any live ERC20 credit, and there is no found public entrypoint to reclaim funds stuck in the module account this way).

### Impact Explanation
This causes permanent, unrecoverable loss of user funds: native coins are pulled into the `erc20` module account with no corresponding ERC20 balance for the receiver and no burn/refund, and no code path was found to recover them. This matches the allowed "Critical permanent freezing/locking/theft of user funds" impact category.

### Likelihood Explanation
Triggering requires an IBC transfer of a native-ERC20-backed coin to fail (error ack) or time out — both are standard, non-privileged IBC outcomes reachable by any user's ordinary transfer (e.g., to an unreachable/invalid recipient on the counterparty chain, or via a normal timeout), combined with the paired ERC20 contract's `transfer` reverting on the module's reconversion attempt (e.g., pausable/blacklistable tokens, insufficient module-held balance due to prior activity, or any conditional revert logic in the registered contract). Since ERC20 registration can be permissionless depending on `PermissionlessRegistration` params, and even governance-approved tokens commonly implement pause/blacklist features, this is a realistic, unprivileged-triggerable condition, not one requiring validator/relayer/admin collusion — a relayer submitting a legitimate ack/timeout message is ordinary operation.

### Recommendation
`ConvertCoinToERC20FromPacket` should not swallow the error from `ConvertCoinNativeERC20`. Either: (a) refund the escrowed coin back to the sender before returning, or (b) restructure `ConvertCoinNativeERC20` so the escrow step is only committed after the EVM transfer succeeds (e.g., perform the EVM transfer first, or wrap the whole operation so a failure produces a fully-reverted state via a cached context that is only committed on total success), ensuring no partial state (escrow without burn/credit) can persist.

### Proof of Concept
1. Register a malicious/native ERC20 token pair whose `transfer` function can be made to revert conditionally when called by the module address (e.g., pausable or blacklist-style contract), backing token pair `pair`.
2. Obtain the corresponding native coin via `ConvertERC20`, then send it out over IBC.
3. Cause the IBC transfer to fail (error ack) or time out.
4. During the resulting `OnAcknowledgementPacket`/`OnTimeoutPacket` callback, ensure the malicious contract's `transfer` call inside `ConvertCoinNativeERC20` (`x/erc20/keeper/msg_server.go:263`) reverts.
5. Observe: `ConvertCoinToERC20FromPacket` returns `nil` (per `x/erc20/keeper/ibc_callbacks.go:252`), the sender's coin balance is not restored, no ERC20 balance was credited, and the coin now sits unaccounted for in the `erc20` module account with no burn and no refund — i.e., permanently stranded. [4](#0-3) [1](#0-0)

### Citations

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

**File:** x/erc20/keeper/msg_server.go (L256-305)
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

	// Check expected Receiver balance after transfer execution
	balanceTokenAfter := k.BalanceOf(ctx, erc20, contract, receiver)
	if balanceTokenAfter == nil {
		return sdkerrors.Wrap(types.ErrEVMCall, "failed to retrieve balance")
	}

	exp := big.NewInt(0).Add(balanceToken, amount.BigInt())

	if r := balanceTokenAfter.Cmp(exp); r != 0 {
		return sdkerrors.Wrapf(
			types.ErrBalanceInvariance,
			"invalid token balance - expected: %v, actual: %v", exp, balanceTokenAfter,
		)
	}

	// Burn escrowed Coins
	err = k.bankKeeper.BurnCoins(ctx, types.ModuleName, coins)
	if err != nil {
		return sdkerrors.Wrap(err, "failed to burn coins")
	}

	return nil
```
