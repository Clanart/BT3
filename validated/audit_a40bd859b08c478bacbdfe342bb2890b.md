## Title
Native-ERC20 IBC receive path returns an error acknowledgement *after* the underlying token has already been unescrowed to the recipient, causing double-crediting of value across chains - (File: `x/erc20/ibc_middleware.go`, `x/erc20/keeper/ibc_callbacks.go`)

### Summary
The EigenLayer report describes a pattern where a multi-step operation partially executes state changes and then a later step reverts, leaving the overall operation in a broken, inconsistent state. In Cosmos EVM's ERC20↔IBC middleware, the analogous (and more severe) pattern exists: the base ICS20 transfer step (unescrow to recipient) is executed and committed to the same `sdk.Context` *before* the erc20-specific auto-conversion step runs, and if that later step fails, the middleware still returns an **error acknowledgement**. An error acknowledgement causes the counterparty (source) chain to refund/re-mint the sender's tokens, while the destination chain has already durably credited the recipient — duplicating value across the two chains.

### Finding Description
`IBCMiddleware.OnRecvPacket` first invokes the base ICS20 transfer callback (which unescrows/mints the received coin directly against the ambient `ctx`), and only afterward calls into the ERC20 keeper's `OnRecvPacket` hook using the *same, already-mutated* context — there is no `ctx.CacheContext()` isolation between the two stages: [1](#0-0) 

Inside `Keeper.OnRecvPacket`, for the "native ERC20 token" case (i.e., this chain is the origin of the underlying asset and the packet is unescrowing previously-sent-out tokens), if `MintingEnabled` or the subsequent `ConvertCoinNativeERC20` call fails, the function returns `channeltypes.NewErrorAcknowledgement(err)`: [2](#0-1) 

At the point this error is returned, the underlying bank coin has **already** been unescrowed to the recipient by the prior, successful `im.Module.OnRecvPacket` call — that mutation is not rolled back. `ConvertCoinNativeERC20` itself can fail for several unprivileged, externally-triggerable reasons: the destination `SendEnabled` flag for the denom being false, the ERC20 contract's `transfer` reverting (e.g., a paused/blacklisting token or self-destructed contract), or a balance-invariance mismatch: [3](#0-2) 

The test suite explicitly exercises and documents this exact scenario — using `SetSendEnabled(..., false)` to force the erc20 conversion to fail — and confirms the underlying token remains credited to the recipient rather than being sent back to escrow: [4](#0-3) [5](#0-4) 

Once this destination-chain acknowledgement is relayed back as a failure, the counterparty (source) chain's core ICS20 `OnAcknowledgementPacket` will treat the transfer as failed and refund the original sender there (re-minting/unescrowing the value that was burned/escrowed at send time). Meanwhile the recipient on the Cosmos EVM chain retains the already-unescrowed bank coin. The same unit of value now exists on both chains simultaneously.

By contrast, the module's own send-side failure handling (`OnAcknowledgementPacket`/`OnTimeoutPacket` → `ConvertCoinToERC20FromPacket`) correctly avoids this problem by *never* returning an error when the ERC20 re-conversion fails — it just leaves the coin in bank form and returns `nil`, matching its own documented invariant: [6](#0-5) 

The receive-side case 2 branch is inconsistent with this documented invariant and is the root cause.

### Impact Explanation
This breaks the core IBC/ERC20 accounting invariant that a token's total supply/escrow accounting must remain 1:1 across a Cosmos EVM chain and its IBC counterparties. An unprivileged actor who controls (or targets) a token pair with a `SendEnabled=false` state, a reverting/blacklisting ERC20 implementation, or simply times a governance-driven pair-disable/param-toggle against an in-flight packet, can cause the destination chain to durably credit the recipient while the source chain independently refunds/re-mints the same value to the sender. This is unauthorized duplication of spendable value — a Critical-impact accounting corruption per the allowed impact gate.

### Likelihood Explanation
The trigger conditions (bank `SendEnabled` toggled off for a denom, a token pair being disabled via governance while a packet is in flight, or an ERC20 contract that reverts on `transfer` to specific addresses) are realistic, externally observable, and in the "SendEnabled=false" case directly demonstrated by the repository's own integration tests as producing exactly the state described (recipient retains bank coin form + destination returns a failure acknowledgement).

### Recommendation
In `Keeper.OnRecvPacket`, the "native ERC20 token" branch must not return an error acknowledgement once the base ICS20 unescrow to the recipient has already occurred; instead, mirror the send-side design (`ConvertCoinToERC20FromPacket`) by returning the original success `ack` and leaving the coin as its bank representation on conversion failure, or alternatively perform the entire receive-processing (base transfer + erc20 conversion) inside a single `ctx.CacheContext()` that is only written back if both stages succeed, guaranteeing the acknowledgement accurately reflects whether any state mutation occurred.

### Proof of Concept
1. Register a token pair `P` as `NativeERC20` on Cosmos EVM chain A, and send an amount `X` of the underlying ERC20 (converted to its bank-coin form) out via IBC to chain B, escrowing `X` on chain A.
2. On chain B, initiate a transfer of `X` back to chain A, targeting a recipient address.
3. Immediately before chain A processes `OnRecvPacket` for the return packet, cause `ConvertCoinNativeERC20` to fail — e.g., call `SetSendEnabled(ctx, P.Denom, false)` (governance/bank param, or exploit a token pair whose underlying ERC20 reverts `transfer` for the given recipient).
4. Observe on chain A: `im.Module.OnRecvPacket` already unescrowed `X` from the escrow account to the recipient's bank balance (verified by the existing test at `evmd/tests/ibc/ibc_middleware_test.go` lines 748-808, where `trappedBal` equals the received amount even though the ack fails).
5. Chain A's middleware returns `channeltypes.NewErrorAcknowledgement(err)` because `ConvertCoinNativeERC20` failed.
6. The relayer delivers the error acknowledgement to chain B, whose core ICS20 module refunds/re-mints `X` back to the original sender on chain B (standard ICS20 `OnAcknowledgementPacket` error-ack behavior).
7. Result: `X` now exists both as a spendable bank coin credited to the recipient on chain A and as re-minted/refunded value to the sender on chain B — a duplication of the original `X`.

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

**File:** x/erc20/keeper/msg_server.go (L237-297)
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
```

**File:** evmd/tests/ibc/ibc_middleware_test.go (L729-754)
```go
				suite.evmChainA.NextBlock()

				// SendEnabled=false will cause the conversion of bank tokens to erc20 tokens to fail,
				// but not send them back to escrow
				evmApp.BankKeeper.SetSendEnabled(evmCtx, nativeErc20.Denom, false)
				isSendEnabled := evmApp.BankKeeper.IsSendEnabledDenom(evmCtx, nativeErc20.Denom)
				suite.Require().False(isSendEnabled)

				packet1 := channeltypes.Packet{
					Sequence:           1,
					SourcePort:         path.EndpointB.ChannelConfig.PortID,
					SourceChannel:      path.EndpointB.ChannelID,
					DestinationPort:    path.EndpointA.ChannelConfig.PortID,
					DestinationChannel: path.EndpointA.ChannelID,
					Data:               packetData.GetBytes(),
					TimeoutHeight:      suite.evmChainA.GetTimeoutHeight(),
					TimeoutTimestamp:   0,
				}

				errAck := transferStack.OnRecvPacket(
					evmCtx,
					sourceChan.Version,
					packet1,
					suite.evmChainA.SenderAccount.GetAddress(),
				)
				suite.Require().False(errAck.Success())
```

**File:** evmd/tests/ibc/ibc_middleware_test.go (L803-808)
```go
				// the packet that failed conversion due to the minting restriction should instead remain as the bank token
				// and will be in the isolated address used to invoke the callback
				isolatedAddr := callbacktypes.GenerateIsolatedAddress(path.EndpointA.ChannelID,
					suite.chainB.SenderAccount.GetAddress().String())
				trappedBal := evmApp.BankKeeper.GetBalance(evmCtx, isolatedAddr, nativeErc20.Denom)
				suite.Require().Equal(recvAmt.String(), trappedBal.Amount.String())
```
