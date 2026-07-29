## Analysis

The reported Escrow.sol bug class (a *push* transfer to an address that can unconditionally revert — e.g., a blacklisted USDC address — bricking value that has already left an accounting-safe state) has a direct analog in Cosmos EVM's `x/erc20` IBC middleware, specifically in the interaction between `ConvertCoinNativeERC20` and the `OnRecvPacket` callback for **native ERC20 token pairs**.

### Root cause

`x/erc20/ibc_middleware.go` `OnRecvPacket` first lets the underlying ICS20 transfer app mint/unescrow the Cosmos `Coin` to the recipient, and only *after that succeeds* does it call `im.keeper.OnRecvPacket`, which for a `pair.IsNativeERC20()` token calls `k.ConvertCoinNativeERC20`: [1](#0-0) 

`ConvertCoinNativeERC20` first moves the just-received `Coin` out of the recipient's account into the module account (`SendCoinsFromAccountToModule`), and only afterwards calls into the actual ERC20 contract's `transfer` function via `CallEVM` to unescrow the ERC20 tokens to the recipient: [2](#0-1) 

If that `transfer` call reverts — which is exactly what happens when the registered native ERC20 (e.g. a bridged USDC-like contract) blacklists the recipient — the function returns an error immediately, **before** the final `BurnCoins` step, and with **no code path that reverses the earlier `SendCoinsFromAccountToModule` escrow**: [3](#0-2) 

Because IBC `OnRecvPacket` state changes are committed regardless of whether the returned acknowledgement is success or error (this is why ibc-go's own transfer module validates before minting), this partial escrow becomes permanent chain state, while the returned `channeltypes.NewErrorAcknowledgement(err)` tells the *source* chain that the transfer failed, causing it to refund the original sender.

### Title
Blacklist-triggered revert in native ERC20 unescrow during `OnRecvPacket` permanently strands the received Coin escrow while refunding the sender on the source chain — duplication of value ([File: x/erc20/keeper/ibc_callbacks.go], [File: x/erc20/keeper/msg_server.go])

### Summary
When an IBC-transferred native ERC20 token pair's recipient is blacklisted by the underlying ERC20 contract (a legitimate, governance-registered token, exactly analogous to the USDC scenario in the report), `k.OnRecvPacket` returns an error acknowledgement *after* `ConvertCoinNativeERC20` has already moved the freshly-minted Coin into the erc20 module account. That escrow step is never unwound because the failure happens later inside the same function, after the irreversible `SendCoinsFromAccountToModule` call.

### Finding Description
1. Relayer submits `MsgRecvPacket` for an ICS20 transfer of a native-ERC20-backed denom to a blacklisted recipient.
2. `ibc.Module.OnRecvPacket` (the underlying transfer app) runs first and succeeds, minting/unescrowing the Coin to the recipient — this state change is committed as part of packet processing.
3. `IBCMiddleware.OnRecvPacket` then calls `k.OnRecvPacket`, which calls `ConvertCoinNativeERC20`.
4. `ConvertCoinNativeERC20` executes `SendCoinsFromAccountToModule` (recipient → erc20 module) first, then attempts `CallEVM(... "transfer" ...)` to move the underlying ERC20 tokens out of the module address to the recipient.
5. Because the recipient is blacklisted by the ERC20 contract, the `transfer` call reverts, and `CallEVM` returns an error which is propagated straight back without reversing step 4's escrow.
6. `k.OnRecvPacket` converts this into `channeltypes.NewErrorAcknowledgement(err)`.
7. The source chain, upon receiving the error acknowledgement, refunds the escrowed amount back to the original sender.
8. Net result: the sender is refunded on the source chain **and** the destination chain retains the Coin permanently stuck in the erc20 module account (never burned, never delivered as ERC20) — value has been duplicated across chains and the destination-side portion is permanently frozen.

### Impact Explanation
This is a critical, unauthorized duplication of spendable value across chains combined with permanent freezing of user/escrowed funds on the destination chain — matching the "Critical unauthorized minting/duplication" and "permanent freezing/locking of escrowed assets/token-pair-backed balances" impact categories. No privileged action is required; any registered native-ERC20 token pair whose underlying contract implements a blacklist (a realistic and foreseeable scenario for real-world bridged stablecoins) can trigger it.

### Likelihood Explanation
Likelihood is directly tied to onboarding any native ERC20 pair whose contract has an address-blacklist or similarly revertible transfer restriction (common for stablecoins like USDC). Any unprivileged party controlling the blacklist (or the blacklisted address itself receiving a transfer, even self-triggered) can cause this by simply relaying a normal ICS20 transfer packet to a blacklisted address — no special permissions needed on the Cosmos EVM chain itself.

### Recommendation
`ConvertCoinNativeERC20` (and its caller in the `OnRecvPacket` flow) must be atomic: perform the risky external `CallEVM` transfer to the recipient *before* debiting/escrowing the Coin, or wrap the whole conversion attempt in a branched context (`ctx.CacheContext()`) that is only committed if the ERC20 transfer succeeds, falling back to leaving the Coin balance with the recipient in its Cosmos-native form when the ERC20 leg fails. This preserves the documented intended behavior ("if conversion fails ... the user receives the corresponding bank token instead") and avoids stranding funds in the module account while the source chain independently refunds the sender.

### Proof of Concept
1. Register a native ERC20 token pair (`TokenPair.IsNativeERC20() == true`) for an ERC20 contract implementing an owner-controlled blacklist (`transfer` reverts for blacklisted `to`).
2. Blacklist address `R`.
3. Relay an ICS20 `MsgTransfer` from chain B targeting recipient `R` on the Cosmos EVM chain for this denom.
4. Observe: underlying transfer succeeds (Coin minted to `R`), `ConvertCoinNativeERC20` escrows the Coin to the erc20 module account, then `CallEVM` transfer reverts because `R` is blacklisted; `k.OnRecvPacket` returns an error acknowledgement.
5. On chain B, the error acknowledgement causes the original sender's escrowed tokens to be refunded.
6. On the Cosmos EVM chain, query the erc20 module account balance for the denom — it now holds the escrowed Coin permanently, with no code path to burn it or deliver it to `R`, while chain B's sender already received their refund — demonstrating duplicated/stuck value. [4](#0-3) [5](#0-4)

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

**File:** x/erc20/keeper/ibc_callbacks.go (L35-43)
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
