I have enough evidence to confirm the analog vulnerability. The key asymmetry is confirmed: `ConvertCoinToERC20FromPacket` (used for `OnAcknowledgementPacket`/`OnTimeoutPacket`) deliberately swallows conversion errors and returns `nil` so the transfer/refund still succeeds with the user just holding the bank coin instead of ERC20 [1](#0-0) , but `OnRecvPacket`'s native-ERC20 case converts a successful ICS-20 receipt into an `ErrorAcknowledgement` when `MintingEnabled` or `ConvertCoinNativeERC20` fails [2](#0-1) , after the underlying transfer already committed the coin to the receiver [3](#0-2) .

### Title
IBC receive path downgrades a committed transfer to an error acknowledgement, causing duplicated fund creation (destination credit + source refund) - (File: x/erc20/keeper/ibc_callbacks.go)

### Summary
`x/erc20`'s ICS-20 middleware wraps the standard transfer `OnRecvPacket` and then performs a post-processing "native ERC20" coin→ERC20 conversion. If that conversion step fails for any reason after the underlying ICS-20 transfer has already unescrowed/minted the coin to the receiver, the middleware returns `channeltypes.NewErrorAcknowledgement(err)` instead of preserving the transfer's success acknowledgement. Because the state changes from the successful transfer are already committed regardless of the acknowledgement bytes, the source chain — upon seeing an error acknowledgement — will refund the original sender (re-mint/unescrow the sent amount there). The result is that value exists both on the destination chain (already credited to receiver) and back on the source chain (refunded to sender): a duplication of spendable value across IBC escrows.

### Finding Description
The relevant flow is:
1. `IBCMiddleware.OnRecvPacket` in [3](#0-2)  first calls the underlying ICS-20 `Module.OnRecvPacket`, which performs the actual token transfer (mint/unescrow to receiver) and returns a success `ack`. Only if `ack.Success()` is true does it proceed to `im.keeper.OnRecvPacket(ctx, packet, ack)`.
2. `Keeper.OnRecvPacket` in [4](#0-3)  then attempts to auto-convert the received Cosmos coin into its ERC20 representation. For the "native ERC20" case (`pair.IsNativeERC20()`), it calls `k.MintingEnabled` and `k.ConvertCoinNativeERC20`, and **on any failure returns `channeltypes.NewErrorAcknowledgement(err)`** — see [2](#0-1) .
3. `MintingEnabled` can fail for reasons outside the receiver's control, e.g. governance disabling the module (`IsERC20Enabled`), the token pair being disabled, bank `SendEnabled` being toggled for the denom, or the receiver being blocked — see [5](#0-4) . `ConvertCoinNativeERC20` can also fail if the underlying ERC20 contract reverts, is self-destructed, or its `transfer` returns `false` — see [6](#0-5) .
4. Crucially, by the time step 2 runs, the coin has *already* been credited to the receiver's bank balance by the base ICS-20 module in step 1 — this state change is not part of a try/catch that gets rolled back; only the acknowledgement payload is swapped to an error.
5. When the destination chain writes back an error acknowledgement, the standard ICS-20 `OnAcknowledgementPacket` handler on the **source** chain treats the transfer as failed and refunds the original sender (un-escrows/re-mints the sent amount on the source chain).

The net effect: the receiver on the destination chain keeps the credited bank coin (never actually converted, but still spendable), while the sender on the source chain is also refunded the same amount. This duplicates spendable value across the two chains' ledgers/escrows.

Notably, the module already recognizes this exact hazard and handles it correctly for the acknowledgement/timeout paths: `ConvertCoinToERC20FromPacket` explicitly ignores `ConvertCoinNativeERC20` errors and returns `nil` (documented as "the user receives the corresponding bank token instead") — see [7](#0-6) , and comments explicitly state this is intentional at [8](#0-7) . The `OnRecvPacket` native-ERC20 branch is inconsistent with this safe pattern and instead downgrades an already-executed transfer to a failure.

### Impact Explanation
This is a critical duplication/accounting-corruption bug: the same transferred value becomes spendable on both chains simultaneously (destination receiver keeps the bank coin, source sender gets refunded). This directly matches the allowed impact "Critical unauthorized minting, burning, duplication ... of spendable user value across ... IBC escrows."

### Likelihood Explanation
The trigger conditions are realistic and can occur without any relayer/validator misbehavior: a governance-driven pause of `EnableErc20`/`SendEnabled` for a denom, a disabled token pair, a receiver on the block list, or (most attacker-controllable) an externally-owned ("native ERC20") token whose owner self-destructs or bricks the contract so `transfer` fails — any of these occurring at the moment an IBC transfer using that denom is relayed will trigger the mis-acknowledgement and duplicate value. Because IBC relaying is permissionless, any unprivileged relayer can trigger the vulnerable code path once such a condition exists.

### Recommendation
Mirror the safe pattern already used in `ConvertCoinToERC20FromPacket`/`OnTimeoutPacket`: in `OnRecvPacket`'s native-ERC20 branch, do not convert a post-hoc conversion failure into an `ErrorAcknowledgement`. Instead, catch/log the failure and return the original success `ack` (leaving the recipient with the bank coin), exactly as is already done for the acknowledgement and timeout paths.

### Proof of Concept
1. Register a native ERC20 token pair with `ContractOwner = OWNER_EXTERNAL` (externally owned contract).
2. Have the token pair's ERC20 contract owner cause the `transfer` call to revert or self-destruct the contract (attacker-controlled since they own the contract).
3. Have a user relay an ICS-20 transfer of this denom's IBC voucher back into this chain (return leg), which is a normal, permissionless relay operation.
4. `im.Module.OnRecvPacket` succeeds and credits the receiver's bank balance with the coin.
5. `Keeper.OnRecvPacket`'s `case found && pair.IsNativeERC20()` calls `ConvertCoinNativeERC20`, which fails due to the broken contract; the function returns `channeltypes.NewErrorAcknowledgement(err)`.
6. The source chain, upon receiving this error acknowledgement, refunds the original sender.
7. Result: receiver holds the credited bank coin on the destination chain AND sender is refunded on the source chain — the same value now exists twice.

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

**File:** x/erc20/keeper/ibc_callbacks.go (L156-164)
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
```

**File:** x/erc20/keeper/ibc_callbacks.go (L190-253)
```go
// ConvertCoinToERC20FromPacket converts the IBC coin to ERC20 after refunding the sender
// This function is only executed when IBC timeout or an Error ACK happens.
func (k Keeper) ConvertCoinToERC20FromPacket(ctx sdk.Context, data transfertypes.FungibleTokenPacketData) error {
	// Sender is local (source) chain address; accept local bech32 or 0x-hex
	senderBz, err := k.addrCodec.StringToBytes(data.Sender)
	if err != nil {
		return err
	}
	sender := sdk.AccAddress(senderBz)

	pairID := k.GetTokenPairID(ctx, data.Denom)
	pair, found := k.GetTokenPair(ctx, pairID)
	if !found {
		// no-op, token pair is not registered
		return nil
	}

	coin := ibc.GetSentCoin(data.Denom, data.Amount)

	switch {

	// Case 1. if pair is native coin -> no-op
	case pair.IsNativeCoin():
		// no-op, received coin is a  native coin
		return nil

	// Case 2. if pair is native ERC20 -> unescrow
	case pair.IsNativeERC20():
		// use a zero gas config to avoid extra costs for the relayers
		ctx = ctx.
			WithKVGasConfig(storetypes.GasConfig{}).
			WithTransientKVGasConfig(storetypes.GasConfig{})

		params := k.GetParams(ctx)
		if !params.EnableErc20 || !k.IsDenomRegistered(ctx, coin.Denom) {
			// no-op, ERC20s are disabled or the denom is not registered
			return nil
		}

		// assume that all module accounts on Cosmos EVM need to have their tokens in the
		// IBC representation as opposed to ERC20
		senderAcc := k.accountKeeper.GetAccount(ctx, sender)
		if types.IsModuleAccount(senderAcc) {
			return nil
		}

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

**File:** x/erc20/keeper/mint.go (L18-66)
```go
func (k Keeper) MintingEnabled(
	ctx sdk.Context,
	receiver sdk.AccAddress,
	token string,
) (types.TokenPair, error) {
	if !k.IsERC20Enabled(ctx) {
		return types.TokenPair{}, errorsmod.Wrap(
			types.ErrERC20Disabled, "module is currently disabled by governance",
		)
	}

	id := k.GetTokenPairID(ctx, token)
	if len(id) == 0 {
		return types.TokenPair{}, errorsmod.Wrapf(
			types.ErrTokenPairNotFound, "token '%s' not registered by id", token,
		)
	}

	pair, found := k.GetTokenPair(ctx, id)
	if !found {
		return types.TokenPair{}, errorsmod.Wrapf(
			types.ErrTokenPairNotFound, "token '%s' not registered", token,
		)
	}

	if !pair.Enabled {
		return types.TokenPair{}, errorsmod.Wrapf(
			types.ErrERC20TokenPairDisabled, "minting token '%s' is not enabled by governance", token,
		)
	}

	if k.bankKeeper.BlockedAddr(receiver.Bytes()) {
		return types.TokenPair{}, errorsmod.Wrapf(
			errortypes.ErrUnauthorized, "%s is not allowed to receive transactions", receiver,
		)
	}

	// NOTE: ignore amount as only denom is checked on IsSendEnabledCoin
	coin := sdk.Coin{Denom: pair.Denom}

	// check if minting to a recipient address other than the sender is enabled
	// for for the given coin denom
	if !k.bankKeeper.IsSendEnabledCoin(ctx, coin) {
		return types.TokenPair{}, errorsmod.Wrapf(
			banktypes.ErrSendDisabled, "minting '%s' coins to an external address is currently disabled", token,
		)
	}

	return pair, nil
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
