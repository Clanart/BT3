### Title
Native ERC20 auto-conversion in `OnRecvPacket` mutates bank/ERC20 state without a cache context, so a failed conversion still triggers an IBC error-ack refund — duplicating value - (File: x/erc20/keeper/ibc_callbacks.go)

### Summary
`x/erc20/keeper/ibc_callbacks.go`'s `OnRecvPacket` calls `ConvertCoinNativeERC20` directly on the live `sdk.Context` (only `WithKVGasConfig`/`WithTransientKVGasConfig` are applied, no `CacheContext`) after the base ICS20 transfer has already minted/unescrowed the voucher coin to the recipient. If `ConvertCoinNativeERC20` fails partway (its own explicit balance-invariance check), the function returns an error and the middleware converts this into an `ErrorAcknowledgement`, but the state mutations already performed (escrow of coin from recipient to module, and the partial ERC20 `transfer` to the recipient) are **not rolled back**, because they were written directly to the same store instead of a discardable cache. An `ErrorAcknowledgement` causes the source chain to refund the original sender in full via standard ICS20 ack handling, while the destination chain has already both consumed the recipient's coin balance and released ERC20 value to the recipient — a double-payout of the same nominal transfer.

### Finding Description
The relevant control flow: [1](#0-0) 

`IBCMiddleware.OnRecvPacket` first calls the wrapped transfer app’s `OnRecvPacket` (this performs the standard ICS20 mint/unescrow of the voucher coin into the recipient's bank balance, on the live `ctx`), and only afterward, on the **same** `ctx`, calls `im.keeper.OnRecvPacket` to auto-convert the coin into its ERC20 representation. [2](#0-1) [3](#0-2) 

For the "native ERC20" case, `k.ConvertCoinNativeERC20` is invoked on this same context: [4](#0-3) 

Inside `ConvertCoinNativeERC20`:
1. `k.bankKeeper.SendCoinsFromAccountToModule` debits the recipient's coin balance to the erc20 module account (escrow) — committed immediately on the live store.
2. `k.evmKeeper.CallEVM(... "transfer" ...)` moves ERC20 tokens from the module to the recipient — also committed immediately on the live store, since this is a keeper-level EVM call, not something reverted by a later Go `error` return.
3. Only *after* both of the above, the code compares `balanceTokenAfter` against the expected value and, if they differ (e.g., the registered native ERC20 contract implements a transfer tax/fee, is upgradeable, or otherwise transfers a different amount than requested while still returning `true`), returns `types.ErrBalanceInvariance`.
4. The coin burn (`k.bankKeeper.BurnCoins`) only happens if the invariance check passes — so on failure, the previously-escrowed coin is **not returned to the recipient and not burned**; it is stranded in the erc20 module account.

Back in `OnRecvPacket`, this error causes an `ErrorAcknowledgement` to be returned: [5](#0-4) 

No `ctx.CacheContext()` / conditional `writeCache()` pattern is used anywhere in `x/erc20` to make steps 1–4 atomic with the acknowledgement outcome (`grep` for `CacheContext` in `x/erc20/**` returns no results). Per standard ICS20 semantics, an `ErrorAcknowledgement` delivered back to the sending chain causes `OnAcknowledgementPacket` there to refund (unescrow/mint) the original sender in full. Meanwhile on the receiving chain, the recipient's coin has already been debited and moved into escrow, and a live ERC20 `transfer` call to the recipient has already executed (potentially transferring a non-zero, non-matching amount) before the invariance check tripped.

### Impact Explanation
This breaks the 1:1 accounting invariant between native coin, ERC20 view, and IBC escrow that the surrounding balance-invariance checks are explicitly designed to protect (see the identical pattern and intent in `convertERC20IntoCoinsForNativeToken`/`ConvertCoinNativeERC20`, whose comments state they exist to "check if token balance increased/decreased by amount"). When the check fails, the code assumes it is "safe" to just return an error, but it does not undo the escrow/transfer already performed. Consequences of one failed receive:
- The recipient chain permanently strands the escrowed coin in the erc20 module account (funds neither burned nor returned to the recipient) — a locked/frozen value the recipient can never reclaim through this flow.
- The recipient may have already received a non-zero (partial) ERC20 balance from the module before the check tripped.
- The sender on the source chain is refunded in full once the relayer submits the resulting `ErrorAcknowledgement`.

Net effect: the same nominal transfer amount is paid out twice (refund to sender + partial/stranded value on destination side), which is a duplication of spendable user value and/or permanent freezing of escrowed coin, matching the "Critical unauthorized minting/duplication" and "permanent freezing/locking of escrowed assets" impact categories.

### Likelihood Explanation
The trigger does not require a malicious relayer, validator, or node — it only requires:
1. A "native ERC20" token pair (`OWNER_EXTERNAL`) whose underlying contract can, under some condition (fee-on-transfer, owner-adjustable tax, pausable/partial transfer, proxy upgrade after registration, or simply a non-standard-but-legitimate deflationary token), transfer a different amount than requested while still returning `true`/emitting a normal `Transfer` event.
2. Any IBC transfer of that coin denomination back into this chain that triggers the auto-conversion path.

Registering the initial token pair does require a governance action, but nothing prevents the token owner (an ordinary externally-owned contract, not the chain's governance) from later changing the contract's transfer behavior (if mutable/upgradeable) or simply operating a token that always had non-1:1 transfer semantics that the audit at registration time didn't catch. This is squarely inside the intended production surface (explicitly tested via `TestOnRecvPacketNativeErc20`, `SetupNativeErc20`, etc.), and the trigger itself (an inbound IBC packet causing a mismatch) is fully reachable by an ordinary IBC relayer forwarding a legitimate transfer — no privileged action is needed to *trigger* the bug once such a token pair exists.

### Recommendation
Wrap the entirety of `ConvertCoinNativeERC20` (escrow, ERC20 transfer, invariance check, burn) in a `ctx.CacheContext()` inside `OnRecvPacket`/`ConvertCoinToERC20FromPacket`, and only call `writeCache()` if the conversion fully succeeds (invariance check passes and burn completes). On any failure, discard the cached context so that the coin remains with the recipient in its original (voucher) denomination, matching the documented fallback behavior ("the user receives the corresponding bank token from the TokenPair instead"). Additionally, consider making the ERC20-side transfer and its invariance check strictly atomic with the escrow/burn step so a partial success can never occur without either fully completing or being entirely rolled back.

### Proof of Concept
Conceptual reproduction (not runnable without a custom malicious/fee-charging ERC20 contract deployed and registered as a native ERC20 token pair):
1. Governance registers `EvilToken` (a legitimate-looking ERC20 whose owner can later set a transfer fee/tax, or which is upgradeable) as a native ERC20 token pair via `MsgRegisterERC20`.
2. User converts `EvilToken` to its coin representation (`MsgConvertERC20`), then sends it out via IBC to chain B, and it comes back to chain A in a subsequent packet (or any inbound transfer of the coin denom hits `OnRecvPacket`).
3. Before/during the destination-chain packet processing, the token owner enables a transfer fee such that `EvilToken.transfer(module, receiver, amount)` moves `amount - fee` instead of `amount` while still returning `true`.
4. In `ConvertCoinNativeERC20` (`x/erc20/keeper/msg_server.go:237-306`), `SendCoinsFromAccountToModule` and `CallEVM(... "transfer" ...)` execute and commit directly against the live store; the subsequent balance check (`balanceTokenAfter.Cmp(exp)`) fails due to the fee, producing `ErrBalanceInvariance`.
5. `OnRecvPacket` (`x/erc20/keeper/ibc_callbacks.go:137-139`) converts this into `channeltypes.NewErrorAcknowledgement(err)`.
6. The relayer submits this ack back to the source chain; `OnAcknowledgementPacket` there refunds the sender the original full amount.
7. Result: sender is refunded in full on chain B/source, while on chain A the recipient still holds the partial `EvilToken` ERC20 balance already transferred to them, and the escrowed coin sits stranded in the erc20 module account — total value paid out exceeds the amount that was actually transferred once, violating the 1:1 accounting invariant.

Note: I could not execute this scenario end-to-end (would require a live devnet/test harness with a custom fee-charging ERC20 and a full IBC ack round-trip across two chains), so the exact refund mechanics on the source chain rely on standard ICS20 `OnAcknowledgementPacket`/`RefundPacketToken` semantics from the `ibc-go` dependency, which is outside this repository's code and not independently verified here.

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

**File:** x/erc20/keeper/ibc_callbacks.go (L35-66)
```go
func (k Keeper) OnRecvPacket(
	ctx sdk.Context,
	packet channeltypes.Packet,
	ack exported.Acknowledgement,
) exported.Acknowledgement {
	// If ERC20 module is disabled no-op
	if !k.IsERC20Enabled(ctx) {
		return ack
	}

	var data transfertypes.FungibleTokenPacketData
	if err := transfertypes.ModuleCdc.UnmarshalJSON(packet.GetData(), &data); err != nil {
		// NOTE: shouldn't happen as the packet has already
		// been decoded on ICS20 transfer logic
		err = errorsmod.Wrapf(errortypes.ErrInvalidType, "cannot unmarshal ICS-20 transfer packet data")
		return channeltypes.NewErrorAcknowledgement(err)
	}

	// use a zero gas config to avoid extra costs for the relayers
	ctx = ctx.
		WithKVGasConfig(storetypes.GasConfig{}).
		WithTransientKVGasConfig(storetypes.GasConfig{})

	// recipient (local chain address): accept hex or local bech32
	recipientBz, err := k.addrCodec.StringToBytes(data.Receiver)
	if err != nil {
		return channeltypes.NewErrorAcknowledgement(errorsmod.Wrap(err, "invalid recipient"))
	}
	recipient := sdk.AccAddress(recipientBz)

	receiverAcc := k.accountKeeper.GetAccount(ctx, recipient)

```

**File:** x/erc20/keeper/ibc_callbacks.go (L118-151)
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

		// For now the only case we are interested in adding telemetry is a successful conversion.
		telemetry.IncrCounterWithLabels(
			[]string{types.ModuleName, "ibc", "on_recv", "total"},
			1,
			[]metrics.Label{
				telemetry.NewLabel("denom", coin.Denom),
				telemetry.NewLabel("source_channel", packet.SourceChannel),
				telemetry.NewLabel("source_port", packet.SourcePort),
			},
		)
	}
```

**File:** x/erc20/keeper/msg_server.go (L237-306)
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
}
```
