# Title
Silent failure swallowing in `ConvertCoinToERC20FromPacket` permanently locks refunded IBC coins in the `x/erc20` module account without minting the corresponding ERC20 — (File: `x/erc20/keeper/ibc_callbacks.go`)

## Summary
When an IBC transfer of a native-ERC20-backed coin fails (error acknowledgement) or times out, `Keeper.OnAcknowledgementPacket` / `Keeper.OnTimeoutPacket` call `ConvertCoinToERC20FromPacket` to automatically re-convert the refunded Cosmos coin back into its ERC20 representation for the sender [1](#0-0) . This re-conversion delegates to `ConvertCoinNativeERC20`, which first escrows the sender's coins into the `x/erc20` module account (`SendCoinsFromAccountToModule`) and only afterward attempts to transfer the equivalent ERC20 tokens out of the module's own ERC20 balance to the sender, finally burning the escrowed coins [2](#0-1) . If the ERC20-side transfer step fails (e.g., the token pair's contract is self-destructed, paused, or blacklists an address), `ConvertCoinNativeERC20` returns an error — but that error is caught and swallowed in `ConvertCoinToERC20FromPacket`, which unconditionally returns `nil` [3](#0-2) . Because no `CacheContext`/rollback wraps `ConvertCoinNativeERC20`'s own state writes, the already-executed `SendCoinsFromAccountToModule` escrow step is not undone, while the sender never receives the ERC20 tokens and the escrowed coin is never burned. The refunded funds become permanently trapped in the `erc20` module account.

## Finding Description
The function's own doc-comment states the intended fallback: "the user receives the corresponding bank token from the TokenPair instead" [4](#0-3) . This implies that on conversion failure the sender should simply keep the bank coin they were refunded. However, the actual code path first moves that bank coin out of the sender's account into module escrow, and only reverses this if the subsequent ERC20 unescrow-and-burn steps all succeed:

```go
// x/erc20/keeper/msg_server.go
// Escrow Coins on module account
coins := sdk.Coins{{Denom: pair.Denom, Amount: amount}}
if err := k.bankKeeper.SendCoinsFromAccountToModule(ctx, sender, types.ModuleName, coins); err != nil { ... }

// Unescrow Tokens and send to receiver
res, err := k.evmKeeper.CallEVM(ctx, erc20, types.ModuleAddress, contract, true, nil, "transfer", receiver, amount.BigInt())
if err != nil {
    return err   // escrow already committed to ctx; not rolled back here
}
...
// Burn escrowed Coins
err = k.bankKeeper.BurnCoins(ctx, types.ModuleName, coins)
``` [2](#0-1) 

Since `ctx` is not a `CacheContext` here, the `SendCoinsFromAccountToModule` write persists even when the function later returns an error. The caller, `ConvertCoinToERC20FromPacket`, then intentionally swallows that error instead of propagating it up through `Keeper.OnAcknowledgementPacket` / `im.keeper.OnAcknowledgementPacket` to the IBC core message handler, which would otherwise abort/roll back the whole relayer transaction:

```go
if err := k.ConvertCoinNativeERC20(ctx, pair, coin.Amount, common.BytesToAddress(sender), sender); err != nil {
    defer func() { telemetry.IncrCounter(1, types.ModuleName, "ibc", "error", "total") }()
    ctx.EventManager().EmitEvents(...)
    return nil   // swallowed — the partial escrow is never undone
}
``` [3](#0-2) 

By contrast, the sibling `OnRecvPacket` handler correctly propagates the same kind of failure as an error acknowledgement, letting IBC core's normal refund path apply [5](#0-4) , confirming that the ack/timeout path's error-swallowing is inconsistent and unsafe.

The ERC20-side transfer can fail for several realistic reasons that an unprivileged attacker fully controls, since native ERC20 token-pair registration is permissionless [6](#0-5) :
- Deploying and registering a token whose owner can pause transfers or blacklist an address.
- Self-destructing the registered contract (unlike `ConvertERC20`/`ConvertCoin` message handlers, which explicitly check `acc.HasCodeHash()` and delete the pair before converting [7](#0-6) , `ConvertCoinToERC20FromPacket` performs no such check).

## Impact Explanation
Any sender whose IBC transfer of a native-ERC20-backed coin errors out or times out — including honest, unrelated users transacting with an attacker-controlled or attacker-manipulable token pair — can have their refunded coin balance moved into the `x/erc20` module account and never returned, with no ERC20 minted in exchange. Because the same token pair keeps failing identically on every retry (the contract stays destroyed/paused), the funds become permanently and irrecoverably locked, breaking the required 1:1 backing invariant and causing "Critical permanent freezing/locking of user funds," which matches the allowed impact gate.

## Likelihood Explanation
Triggering requires only unprivileged actions already available in scope: permissionless registration of a native ERC20 (or use of any deployed native-ERC20 with pausable/blacklist/self-destruct semantics), an ordinary IBC transfer that fails or times out (a routine, non-malicious occurrence), and timing the contract-side failure condition (pause/selfdestruct) before the acknowledgement/timeout is processed. No validator, relayer, or governance privilege is needed.

## Recommendation
Wrap `ConvertCoinNativeERC20`'s state mutations in a `ctx.CacheContext()` inside `ConvertCoinToERC20FromPacket` (and `OnTimeoutPacket`), only calling `writeFn()` if the full escrow→unescrow→burn sequence succeeds; otherwise leave the sender's original bank balance untouched, matching the documented fallback behavior. Additionally, apply the same self-destructed/invalid-contract check used in `ConvertERC20`/`ConvertCoin` before attempting the ERC20-side transfer in the packet-callback path.

## Proof of Concept
1. Attacker calls `MsgRegisterERC20` with a self-deployed ERC20 contract implementing an owner-controlled `pause()`/blacklist function (permissionless registration) [6](#0-5) .
2. Attacker (or victim) converts ERC20 to Cosmos coin (`MsgConvertERC20`) and IBC-transfers the coin to a chain/receiver that will produce an error acknowledgement (e.g., invalid receiver) or simply lets the packet time out.
3. Before the relayer submits `MsgAcknowledgement`/`MsgTimeout`, attacker calls `pause()` (or self-destructs) the registered contract.
4. `OnAcknowledgementPacket`/`OnTimeoutPacket` → `ConvertCoinToERC20FromPacket` → `ConvertCoinNativeERC20` executes `SendCoinsFromAccountToModule` (coin moved to module), then the `CallEVM("transfer", ...)` reverts due to pause; error is swallowed and `nil` returned.
5. Assert: sender's bank balance for `pair.Denom` did not increase (no refund), sender's ERC20 balance did not increase, and `x/erc20` module account balance for `pair.Denom` increased by `amount` with no corresponding burn — demonstrating a permanent, unrecoverable backing desync and fund loss for the sender.

### Citations

**File:** x/erc20/keeper/ibc_callbacks.go (L137-139)
```go
		if err := k.ConvertCoinNativeERC20(ctx, pair, coin.Amount, common.BytesToAddress(recipient.Bytes()), recipient); err != nil {
			return channeltypes.NewErrorAcknowledgement(err)
		}
```

**File:** x/erc20/keeper/ibc_callbacks.go (L156-163)
```go
// OnAcknowledgementPacket responds to the success or failure of a packet
// acknowledgement written on the receiving chain. If the acknowledgement was a
// success then nothing occurs. If the acknowledgement failed, then the sender
// is refunded and then the IBC Coins are converted to ERC20.
// If the ERC20 conversion fails for whatever reason, such as an attempt to call
// a self-destructed ERC20 contract or an invalid function, OnAcknowledgementPacket
// still succeeds, but the user receives the corresponding bank token from the
// TokenPair instead. A user may then manually re-attempt the conversion.
```

**File:** x/erc20/keeper/ibc_callbacks.go (L164-188)
```go
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

**File:** x/erc20/keeper/msg_server.go (L42-53)
```go
	if pair.IsNativeERC20() {
		// Remove token pair if contract is suicided
		acc := k.evmKeeper.GetAccountWithoutBalance(ctx, pair.GetERC20Contract())
		if acc == nil || !acc.HasCodeHash() {
			k.DeleteTokenPair(ctx, pair)
			k.Logger(ctx).Debug(
				"deleting selfdestructed token pair from state",
				"contract", pair.Erc20Address,
			)
			// NOTE: return nil error to persist the changes from the deletion
			return nil, nil
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

**File:** x/erc20/keeper/msg_server.go (L324-350)
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
```
