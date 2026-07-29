Based on my investigation, I found a strong structural analog to the shutdownPool bug in `x/erc20`'s IBC receive callback logic.

### Title
Escrowed coins are permanently stuck when `ConvertCoinNativeERC20` fails mid-flight during IBC `OnRecvPacket`, while packet processing still commits as "successful" - (File: `x/erc20/keeper/ibc_callbacks.go`)

### Summary
Similar to Booster's `shutdownPool()`, which commits `pool.shutdown = true` regardless of whether the inner `withdrawAll()` try/catch actually succeeded, `x/erc20`'s IBC receive-path conversion logic performs a state-mutating escrow step and then attempts a "best effort" EVM unescrow/transfer, but if that sub-step fails, the surrounding function only returns a Go error/acknowledgement rather than atomically rolling back the escrow. Because packet processing in the IBC stack doesn't fail the underlying transaction when an app callback merely returns an `ErrorAcknowledgement`, the partial state (coins moved into the erc20 module account) is committed permanently.

### Finding Description
`IBCMiddleware.OnRecvPacket` [1](#0-0)  calls the base ICS20 transfer callback first (which mints/unescrows the voucher coin to the recipient), and if that succeeds, immediately hands off to `k.keeper.OnRecvPacket` using the same (uncached) `ctx`.

Inside `Keeper.OnRecvPacket`, for the native-ERC20 case, `ConvertCoinNativeERC20` is invoked directly against `ctx` [2](#0-1) . That function performs:
1. `SendCoinsFromAccountToModule` — escrows the just-received coin into the erc20 module account.
2. `CallEVM(... "transfer" ...)` — attempts to move ERC20 tokens from the module address to the receiver.
3. A balance-invariance check comparing before/after ERC20 balances.
4. `BurnCoins` on the escrowed amount (only reached if steps 2–3 succeed). [3](#0-2) 

If step 2 or 3 fails (EVM call reverts — e.g. paused/blacklisted ERC20, contract self-destructed, fee-on-transfer/deflationary token causing an amount mismatch, out-of-gas, or any other legitimate revert reason), `ConvertCoinNativeERC20` returns an error. `Keeper.OnRecvPacket` converts that into `channeltypes.NewErrorAcknowledgement(err)` and returns it as the *acknowledgement content* — not as a Go `error` propagated to the message handler [4](#0-3) .

Because a `MsgRecvPacket` transaction in the IBC/Cosmos SDK model succeeds at the baseapp level as long as no Go `error` is returned (an error acknowledgement is a valid packet outcome, not a tx failure), any state mutations that already happened before the failure point — specifically the `SendCoinsFromAccountToModule` escrow — are committed to the chain. There is no compensating logic anywhere in `ibc_callbacks.go` or `msg_server.go` that unwinds this escrow if the subsequent EVM unescrow-transfer or invariant check fails.

This mirrors the reported pattern exactly: an operation with a "try, and swallow the failure into a status flag" step (`try/catch {}` in the audited report vs. Go `err != nil` → `ErrorAcknowledgement` here) is treated as a terminal, committed state, while the precondition for correctness (successful sub-operation) silently failed, leaving no automated recovery path.

The parallel is reinforced by the module's own doc comment on the sibling `OnTimeoutPacket`/`ConvertCoinToERC20FromPacket` path, which explicitly acknowledges that a failed conversion leaves the user with "the corresponding bank token instead" and requires a "manual re-attempt" [5](#0-4)  — but for `OnRecvPacket`, the escrow-before-EVM-call ordering in `ConvertCoinNativeERC20` means the coin isn't left with the user at all; it is left inside the erc20 module account, with no field/event that lets governance or the user manually reclaim it.

Simultaneously, because the acknowledgement returned upstream is an error, the relaying process will cause the **source chain** to also refund/unlock the sender's original coins via its own error-ack or timeout handling — meaning the value is effectively duplicated (refunded on the source chain while also stuck in escrow on the destination chain).

### Impact Explanation
This falls under "Critical permanent freezing... of user funds... escrowed assets... token-pair-backed balances" and potentially "irreversible accounting corruption of spendable user value" from the impact gate, since:
- The recipient's coin is debited from their spendable balance into the erc20 module account, with no burn (since burn only occurs after a successful EVM transfer).
- No mechanism un-escrows or refunds these coins back to the recipient when the EVM step fails.
- The source chain independently unlocks/refunds the equivalent value to the original sender via the standard IBC ack/timeout mechanism, since the acknowledgement is an error — producing a duplicated/orphaned value on the destination chain.

### Likelihood Explanation
This is triggerable by unprivileged actors:
- `RegisterERC20` supports permissionless registration when `PermissionlessRegistration` is enabled [6](#0-5) , allowing any user to register a custom ERC20 contract as a "native ERC20" token pair.
- Such a contract can be designed (or can organically behave, e.g. pausable/blacklistable/fee-on-transfer semantics) to revert or alter balances unexpectedly on `transfer`, which is exactly the code path executed inside `ConvertCoinNativeERC20` during `OnRecvPacket`.
- Triggering only requires a normal IBC transfer of that native-ERC20-backed coin back to the origin chain — an ordinary relayer-submitted `MsgRecvPacket`, not any privileged action.

I could not fully verify from the indexed code whether ibc-go's core channel-keeper `RecvPacket` message handler wraps the entire app callback chain in a discardable cache context that reverts on `ErrorAcknowledgement` (ibc-go internals are a vendored dependency and were not present in the search index). If ibc-go's core layer does perform such branching/rollback around the whole app callback (including middleware), then the specific escrow step here would also be rolled back and this finding would not hold. This uncertainty should be confirmed by directly reviewing the vendored `ibc-go` `04-channel` message server / `RecvPacket` keeper implementation, which I was unable to access through the indexed codebase.

### Recommendation
Restructure `ConvertCoinNativeERC20` (and its caller `Keeper.OnRecvPacket`) to only mutate persistent bank state (`SendCoinsFromAccountToModule`, `BurnCoins`) after the EVM unescrow-transfer and balance invariant check succeed — e.g., perform the EVM `transfer` call and validation first, and only then execute the escrow+burn as a single atomic step, or wrap the whole `ConvertCoinNativeERC20` call in `ctx.CacheContext()` within `OnRecvPacket` and only invoke the returned `writeFn()` if the conversion fully succeeds, discarding all mutations (including the escrow) if it fails and falling back cleanly to leaving the bank coin with the recipient.

### Proof of Concept
1. Attacker permissionlessly registers a custom ERC20 contract (with e.g. a pausable/blacklist `transfer` function, or fee-on-transfer semantics) as a native ERC20 token pair via `MsgRegisterERC20`.
2. Attacker converts some ERC20 tokens to the native coin representation via `ConvertERC20`, then IBC-transfers the resulting coin to a counterparty chain.
3. Attacker (or the token owner) flips the ERC20 contract into a state where `transfer` from the erc20 module address reverts or delivers a different amount than requested (e.g., pause the contract, blacklist the recipient, or rely on fee-on-transfer deduction).
4. Attacker (or any relayer) relays the coin back via IBC to the origin chain, triggering `OnRecvPacket`.
5. Base ICS20 logic credits the recipient with the voucher coin; `Keeper.OnRecvPacket` → `ConvertCoinNativeERC20` escrows that coin into the erc20 module account, then the EVM `transfer` step fails/mismatches, function returns error, and `OnRecvPacket` returns an `ErrorAcknowledgement`.
6. The `MsgRecvPacket` transaction still commits (since the app returned only an ack-level error), permanently leaving the coin in the erc20 module's escrow account with no recovery path, while the source chain refunds the original sender based on the returned error acknowledgement — resulting in duplicated economic value and permanently frozen escrow funds.

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

**File:** x/erc20/keeper/ibc_callbacks.go (L183-188)
```go
// a self-destructed ERC20 contract or an invalid function, OnTimeoutPacket still
// succeeds, but the user receives the corresponding bank token from the TokenPair
// instead. A user may then manually re-attempt the conversion.
func (k Keeper) OnTimeoutPacket(ctx sdk.Context, _ channeltypes.Packet, data transfertypes.FungibleTokenPacketData) error {
	return k.ConvertCoinToERC20FromPacket(ctx, data)
}
```

**File:** x/erc20/keeper/msg_server.go (L256-303)
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
```

**File:** x/erc20/keeper/msg_server.go (L324-345)
```go
// RegisterERC20 implements the gRPC MsgServer interface. Any account can permissionlessly
// register a native ERC20 contract to map to a Cosmos Coin.
func (k *Keeper) RegisterERC20(goCtx context.Context, req *types.MsgRegisterERC20) (*types.MsgRegisterERC20Response, error) {
	ctx := sdk.UnwrapSDKContext(goCtx)

	params := k.GetParams(ctx)

	if !params.PermissionlessRegistration {
		if err := k.validateAuthority(req.Signer); err != nil {
			return nil, err
		}
	}

	// Check if the conversion is globally enabled
	if !k.IsERC20Enabled(ctx) {
		return nil, types.ErrERC20Disabled.Wrap("registration is currently disabled by governance")
	}

	for _, addr := range req.Erc20Addresses {
		if !common.IsHexAddress(addr) {
			return nil, errortypes.ErrInvalidAddress.Wrapf("invalid ERC20 contract address: %s", addr)
		}
```
