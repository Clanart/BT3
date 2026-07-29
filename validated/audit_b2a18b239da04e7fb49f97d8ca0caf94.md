## Finding

### Title
Double-crediting via inconsistent error handling between `OnRecvPacket` and `OnAcknowledgementPacket`/`OnTimeoutPacket` for native ERC20 token pairs - (File: x/erc20/keeper/ibc_callbacks.go)

### Summary
When a native ERC20 token pair packet is received (`pair.IsNativeERC20()` branch), `OnRecvPacket` returns an `ErrorAcknowledgement` if `ConvertCoinNativeERC20` fails — but this happens *after* the underlying ICS20 transfer application (which runs earlier in the middleware stack) has already unescrowed/minted the bank-side coin directly into the recipient's account and committed that state. Unlike the sibling handlers `OnAcknowledgementPacket`/`OnTimeoutPacket`, which explicitly swallow `ConvertCoinNativeERC20` failures and let the user keep the bank coin instead of erroring out (precisely to avoid re-triggering a refund), `OnRecvPacket` does not apply the same defensive pattern. [1](#0-0) 

### Finding Description
`OnRecvPacket` in `x/erc20/keeper/ibc_callbacks.go` is invoked as ERC20 middleware layered on top of the ICS20 transfer application. By the time this function runs for the `pair.IsNativeERC20()` case, the wrapped transfer app has already executed its own `OnRecvPacket`, unescrowing (or minting) the native bank coin directly to the recipient's account — a state write that is committed independently of what this middleware subsequently returns. `MintingEnabled` performs validation but the real conversion happens in `ConvertCoinNativeERC20`, which calls into the EVM to move/mint the ERC20 representation. If that EVM call fails (e.g., the ERC20 contract has self-destructed, is paused, or reverts), the function returns `channeltypes.NewErrorAcknowledgement(err)`. [2](#0-1) 

Returning an error acknowledgement here causes the destination-side write acknowledgement to signal failure, which the relayer will present to the source chain. The source chain, upon seeing `Acknowledgement_Error`, will refund the original sender by unescrowing the coins that were locked there when the transfer was sent. However, on the destination chain (this chain), the bank-side coin credit made by the underlying transfer module's `OnRecvPacket` prior to the ERC20 conversion attempt is never rolled back — there is no `CacheContext`/rollback wrapping the whole packet-processing call chain based on the final acknowledgement value; only the ack byte payload is affected by the returned value, not previously-committed state.

This is corroborated by the module's own commentary and design for the acknowledgement/timeout paths, which explicitly avoid returning an error after a `ConvertCoinNativeERC20` failure for exactly this reason ("If the ERC20 conversion fails ... the user receives the corresponding bank token from the TokenPair instead. A user may then manually re-attempt the conversion."): [3](#0-2) 

`OnRecvPacket` does not follow this same non-reverting pattern; it propagates the conversion failure as a packet-level error, corrupting the invariant that source-chain refund and destination-chain credit are mutually exclusive.

### Impact Explanation
An unprivileged user who controls (or can otherwise force a revert/self-destruct condition in) the ERC20 contract backing a registered native ERC20 token pair can:
1. Lock ERC20 tokens via `ConvertERC20ToCoin`, obtaining the native bank-coin representation.
2. IBC-transfer that bank coin out to a counterparty chain (ordinary escrow on this chain).
3. Render the ERC20 contract's transfer/mint path unusable (e.g., self-destruct, pause).
4. Send the coin back via an ordinary ICS20 transfer from the (honest) counterparty chain.
5. On receipt here, the transfer app unescrows/credits the bank coin to the attacker's account (committed), the ERC20 conversion attempt then fails, and `OnRecvPacket` returns an error acknowledgement.
6. The source (counterparty) chain, seeing the error ack, refunds the attacker there as well.

The attacker ends up holding both the destination-chain bank coin credit and the source-chain refund for the same value — a duplication of spendable value, i.e., unauthorized double-crediting of user-controlled balances. This matches the "Critical unauthorized minting/duplication of spendable user value" impact category.

### Likelihood Explanation
The path is reachable through ordinary, unprivileged IBC transfer transactions and does not require validator, relayer, or governance compromise — only that the attacker controls the ERC20 contract/token pair being reconverted (a reasonable assumption for many permissionlessly or attacker-owned native ERC20 pairs) and can make the reconversion call fail deterministically (self-destruct, revert, pause, allowance/blacklist logic, etc.). No special race condition or timing is required; the counterparty chain behaves entirely honestly throughout.

### Recommendation
Align `OnRecvPacket`'s native-ERC20 branch with the same fail-safe handling already used in `OnAcknowledgementPacket`/`OnTimeoutPacket`: if `ConvertCoinNativeERC20` fails after the underlying transfer has already credited the bank coin, do not return an error acknowledgement (which triggers a source-side refund). Instead, emit the failure event/telemetry and return the original success `ack`, leaving the user holding the bank-coin representation (recoverable manually later), exactly as the sibling handlers already do. Alternatively, wrap the entire `OnRecvPacket` reconversion attempt in an explicit `ctx.CacheContext()` and only write it back on success, while still returning `ack` (not an error) regardless of the conversion outcome, so the destination-side credit and the acknowledgement result stay consistent with what the source chain will do.

### Proof of Concept
Not independently executed (index/tool-based analysis only); the described flow is derived by tracing:
- Transfer app credit occurring in the wrapped ICS20 `OnRecvPacket` prior to this middleware call (verified indirectly via test `TestOnRecvPacketNativeErc20`, which shows the bank coin ("trapped balance") remains credited to an account after a failed `ConvertCoinNativeERC20` mint restriction, while the packet nonetheless can complete/retry independently). [4](#0-3) 

- The absence of any `CacheContext`/rollback mechanism keyed on the ack result within `x/erc20/keeper/ibc_callbacks.go`. [5](#0-4) 

A full end-to-end PoC (deploying a self-destructing ERC20, registering it as a native pair, and driving the two-chain IBC round trip) would need to be built and run in a Devin session with access to the ibctesting harness, since this environment is read-only.

### Citations

**File:** x/erc20/keeper/ibc_callbacks.go (L35-154)
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

	// return acknowledgement without conversion if receiver is a module account
	if types.IsModuleAccount(receiverAcc) {
		return ack
	}

	// parse the transferred denom
	token := transfertypes.Token{
		Denom:  transfertypes.ExtractDenomFromPath(data.Denom),
		Amount: data.Amount,
	}
	coin := ibc.GetReceivedCoin(packet, token)

	// If the coin denom starts with `factory/` then it is a token factory coin, and we should not convert it
	// NOTE: Check https://docs.osmosis.zone/osmosis-core/modules/tokenfactory/ for more information
	if strings.HasPrefix(data.Denom, "factory/") {
		return ack
	}

	// check if the coin is a native staking token
	bondDenom, err := k.stakingKeeper.BondDenom(ctx)
	if err != nil {
		return channeltypes.NewErrorAcknowledgement(err)
	}
	if coin.Denom == bondDenom {
		// no-op, received coin is the staking denomination
		return ack
	}

	pairID := k.GetTokenPairID(ctx, coin.Denom)
	pair, found := k.GetTokenPair(ctx, pairID)
	switch {
	// Case 1. token pair is not registered and is an IBC Coin
	// by checking the prefix we ensure that only coins not native from this chain are evaluated.
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

	return ack
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

**File:** evmd/tests/ibc/ibc_middleware_test.go (L782-808)
```go
				// Check un-escrowed balance on evmChainA after receiving the packet.
				escrowedBal = evmApp.BankKeeper.GetBalance(evmCtx, escrowAddr, nativeErc20.Denom)
				suite.Require().True(escrowedBal.IsZero(), "escrowed balance should be un-escrowed after receiving the packet")

				// recvAmt should be in the contractAddr upon successful recv callback
				contractAddr := common.HexToAddress(packetData.Memo)
				// Parse contract address from memo
				var memoData map[string]interface{}
				err = json.Unmarshal([]byte(packetData.Memo), &memoData)
				suite.Require().NoError(err)
				destCallback := memoData["dest_callback"].(map[string]interface{})
				contractAddrStr := destCallback["address"].(string)
				contractAddr = common.HexToAddress(contractAddrStr)

				balAfterUnescrow := evmApp.Erc20Keeper.BalanceOf(evmCtx, nativeErc20.ContractAbi, nativeErc20.ContractAddr, contractAddr)
				suite.Require().Equal(recvAmt.String(), balAfterUnescrow.String())

				bankBalAfterUnescrow := evmApp.BankKeeper.GetBalance(evmCtx, sender, nativeErc20.Denom)
				// InitialBalance half which was converted but not sent will be in the sending account's balance
				suite.Require().Equal(sendAmt.String(), bankBalAfterUnescrow.Amount.String())

				// the packet that failed conversion due to the minting restriction should instead remain as the bank token
				// and will be in the isolated address used to invoke the callback
				isolatedAddr := callbacktypes.GenerateIsolatedAddress(path.EndpointA.ChannelID,
					suite.chainB.SenderAccount.GetAddress().String())
				trappedBal := evmApp.BankKeeper.GetBalance(evmCtx, isolatedAddr, nativeErc20.Denom)
				suite.Require().Equal(recvAmt.String(), trappedBal.Amount.String())
```
