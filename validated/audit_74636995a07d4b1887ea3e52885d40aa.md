Confirmed: `im.Module.OnTimeoutPacket(...)` runs first (default ICS20 refund, unescrowing/minting the native coin back to `data.Sender` on the same `ctx`, no cache/rollback wrapper), and then `im.keeper.OnTimeoutPacket` / `OnAcknowledgementPacket` runs `ConvertCoinToERC20FromPacket` on that *same, uncommitted-but-shared* `ctx`. Inside it, `ConvertCoinNativeERC20` first does `SendCoinsFromAccountToModule` (debiting the just-refunded coins from the sender into the erc20 module account) and only afterward calls the ERC20 contract's `transfer` to unescrow tokens to the sender. If that EVM call reverts, the function returns an error, but `ConvertCoinToERC20FromPacket` catches it, emits a `EventTypeFailedConvertERC20` event, and returns `nil` — there is no `CacheContext`/rollback anywhere in this chain, so the prior `SendCoinsFromAccountToModule` debit is **not undone**. Both `OnTimeoutPacket` and `OnAcknowledgementPacket` (error-ack) callback paths share this exact same code path via `ConvertCoinToERC20FromPacket`. [1](#0-0) [2](#0-1) [3](#0-2) 

Because `RegisterERC20` is permissionless when `PermissionlessRegistration=true` [4](#0-3) , and `NewTokenPair(..., types.OWNER_EXTERNAL)` accepts arbitrary attacker-deployed ERC20 bytecode [5](#0-4) , an attacker can deploy and register a malicious "native ERC20" whose `transfer` function reverts on unescrow to arbitrary recipients (analogous to the FactoryDAO malicious-reward-token that reverts on `transfer` to `globalBeneficiary`). This is a direct analog worth reporting as it produces Critical fund-freezing impact via the IBC timeout/ack path, unlike the original low-severity finding.

### Title
Non-atomic IBC timeout/ack ERC20 reconversion permanently locks refunded user funds when a malicious native-ERC20 token reverts unescrow transfer - (File: x/erc20/keeper/ibc_callbacks.go)

### Summary
`ConvertCoinToERC20FromPacket`, invoked from both `OnTimeoutPacket` and `OnAcknowledgementPacket` (error-ack) of the `x/erc20` IBC middleware, escrows a user's just-refunded native coin into the module account before attempting to unescrow the corresponding ERC20 token via an EVM call. If the ERC20 `transfer` call reverts, the function swallows the error, emits a failure event, and returns `nil`, without a `CacheContext` rollback anywhere in the call chain. The coin debit into the module account therefore persists permanently while the user receives neither the coin back nor the ERC20 token.

### Finding Description
On IBC timeout or error acknowledgement, the ICS20 transfer module first refunds the sender's native coin on the same `sdk.Context` [6](#0-5) . The erc20 keeper callback then attempts to auto-convert that refunded coin back into its native-ERC20 representation via `ConvertCoinToERC20FromPacket` → `ConvertCoinNativeERC20` [7](#0-6) .

`ConvertCoinNativeERC20` performs the operations non-atomically at the module boundary:
1. `SendCoinsFromAccountToModule` — debits the sender's just-refunded coin into the erc20 module account.
2. `CallEVM(... "transfer" ...)` — calls the registered ERC20 contract's `transfer` to move the corresponding tokens from the module account to the sender. [3](#0-2) 

Because `RegisterERC20` is permissionless (governed by `params.PermissionlessRegistration`) [4](#0-3) , any user can deploy and register their own "native ERC20" contract whose `transfer` function selectively reverts (e.g., when called from the module address, or when the recipient is not an allow-listed address), analogous to the FactoryDAO malicious reward token that reverted on `transfer` to `globalBeneficiary`.

When step 2 reverts, `k.evmKeeper.CallEVM` returns an error, and `ConvertCoinNativeERC20` propagates it. `ConvertCoinToERC20FromPacket` catches this error, records a `EventTypeFailedConvertERC20` event, and explicitly returns `nil` [2](#0-1) . Since there is no `ctx.CacheContext()`/`commit()` pattern wrapping this call anywhere in `x/erc20/ibc_middleware.go`'s `OnTimeoutPacket`/`OnAcknowledgementPacket`, the store mutation from step 1 (the coin debit into the module account) is committed as part of the overall successful IBC packet processing, while the corresponding ERC20 credit never happens.

### Impact Explanation
The affected user's native coin balance is permanently moved into the erc20 module account with no code path to recover it: they do not receive the ERC20 token (transfer reverted) and they no longer hold the native coin (already debited). This is an irreversible, unauthorized extraction/freezing of user funds triggerable by an ordinary, permissionless action (deploying and registering a malicious token contract, then having any counterparty's IBC transfer of that denom time out or receive an error ack) — meeting the Critical "permanent freezing/theft of user funds" bar.

### Likelihood Explanation
Likelihood is high given `PermissionlessRegistration` support requires no privileged access, and IBC timeouts/error acks are routine, unprivileged occurrences (network congestion, relayer downtime, receiving-chain rejection) that an attacker can also intentionally induce by controlling both chain ends or simply waiting for a natural timeout on a channel carrying their malicious-token-backed coin.

### Recommendation
Wrap the reconversion attempt (`ConvertCoinNativeERC20`) inside `ConvertCoinToERC20FromPacket` in a `ctx.CacheContext()` and only call `write()`/commit if the ERC20 unescrow succeeds; on failure, discard the cached context entirely so the coin debit is never persisted, leaving the user simply holding the refunded native coin (consistent with the documented intended fallback behavior in the function's own doc comments).

### Proof of Concept
1. With `params.PermissionlessRegistration = true`, attacker deploys `EvilERC20` where `transfer(to, amount)` reverts whenever `msg.sender == types.ModuleAddress` (i.e., reverts specifically on the module's unescrow call) — or reverts unconditionally after a delay/toggle the attacker controls.
2. Attacker calls `MsgRegisterERC20` to permissionlessly register `EvilERC20` as a native-ERC20 token pair [8](#0-7) .
3. Attacker converts some native coin to `EvilERC20` tokens via `MsgConvertCoin`, then sends the resulting bank-coin representation over IBC via `MsgTransfer` to a channel that will time out (or triggers an error ack, e.g. targeting a receiving chain/module that rejects the packet).
4. On timeout/error-ack, `im.Module.OnTimeoutPacket` refunds the native coin to the attacker's account on-chain.
5. `im.keeper.OnTimeoutPacket` → `ConvertCoinToERC20FromPacket` → `ConvertCoinNativeERC20` executes `SendCoinsFromAccountToModule` (debiting the refunded coin), then calls `EvilERC20.transfer(...)`, which reverts.
6. The error is swallowed; `ConvertCoinToERC20FromPacket` returns `nil`; the IBC packet processing completes successfully; the debited coin remains stuck in the erc20 module account permanently, and the attacker's own account balance is now provably reduced with no code path in the module to reclaim the funds.

Note: this repository's index does not show whether any higher-level Cosmos SDK ADR-008 IBC callback wrapper (outside the files inspected) independently applies a `CacheContext` around the entire `OnTimeoutPacket`/`OnAcknowledgementPacket` invocation chain; based on the files reviewed (`x/erc20/ibc_middleware.go`, `x/erc20/keeper/ibc_callbacks.go`), no such rollback exists at the erc20-module level. A Devin session with full repo access should verify the IBC core `channelkeeper`/`ibc-go` packet-processing entrypoint to confirm no external cache-context safety net exists before treating this as conclusively exploitable.

### Citations

**File:** x/erc20/ibc_middleware.go (L99-115)
```go
func (im IBCMiddleware) OnTimeoutPacket(
	ctx sdk.Context,
	channelVersion string,
	packet channeltypes.Packet,
	relayer sdk.AccAddress,
) error {
	var data transfertypes.FungibleTokenPacketData
	if err := transfertypes.ModuleCdc.UnmarshalJSON(packet.GetData(), &data); err != nil {
		return errorsmod.Wrapf(errortypes.ErrUnknownRequest, "cannot unmarshal ICS-20 transfer packet data: %s", err.Error())
	}

	if err := im.Module.OnTimeoutPacket(ctx, channelVersion, packet, relayer); err != nil {
		return err
	}

	return im.keeper.OnTimeoutPacket(ctx, packet, data)
}
```

**File:** x/erc20/keeper/ibc_callbacks.go (L216-253)
```go
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

**File:** x/erc20/keeper/msg_server.go (L324-361)
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

		pair, err := k.registerERC20(ctx, common.HexToAddress(addr))
		if err != nil {
			return nil, err
		}

		ctx.EventManager().EmitEvent(
			sdk.NewEvent(
				types.EventTypeRegisterERC20,
				sdk.NewAttribute(types.AttributeKeyCosmosCoin, pair.Denom),
				sdk.NewAttribute(types.AttributeKeyERC20Token, pair.Erc20Address),
			),
		)
	}

	return &types.MsgRegisterERC20Response{}, nil
```

**File:** x/erc20/keeper/proposals.go (L16-41)
```go
// RegisterERC20 creates a Cosmos coin and registers the token pair between the
// coin and the ERC20
func (k Keeper) registerERC20(
	ctx sdk.Context,
	contract common.Address,
) (*types.TokenPair, error) {
	// Check if ERC20 is already registered
	if k.IsERC20Registered(ctx, contract) {
		return nil, errorsmod.Wrapf(
			types.ErrTokenPairAlreadyExists, "token ERC20 contract already registered: %s", contract.String(),
		)
	}

	metadata, err := k.CreateCoinMetadata(ctx, contract)
	if err != nil {
		return nil, errorsmod.Wrap(
			err, "failed to create wrapped coin denom metadata for ERC20",
		)
	}

	pair := types.NewTokenPair(contract, metadata.Name, types.OWNER_EXTERNAL)
	err = k.SetToken(ctx, pair)
	if err != nil {
		return nil, err
	}
	return &pair, nil
```
