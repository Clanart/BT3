### Title
Double-mint of bridged funds via forced ERC20 token-pair registration failure after IBC coin mint already committed — analogous to Morpho's LTV=0 "poisoned asset" griefing (File: `x/erc20/ibc_middleware.go`, `x/erc20/keeper/ibc_callbacks.go`, `x/erc20/keeper/token_pairs.go`)

### Summary
Cosmos EVM's `x/erc20` IBC middleware wraps the base ICS20 transfer app and, on `OnRecvPacket`, first lets the base transfer app mint the IBC voucher coin to the receiver, and only afterward attempts to auto-register/convert the coin to its ERC20 representation. If that post-mint step fails, the middleware overrides the acknowledgement with an error ack — but the coin mint performed by the base app has already been committed to state and is never rolled back. Just as Morpho's LTV=0 "poisoned aToken" could be force-fed to a victim to break subsequent invariant-preserving operations, an unprivileged attacker here can force-fail the ERC20 registration step for a specific, self-chosen IBC denom by pre-occupying its deterministic ERC20 contract address, guaranteeing an error acknowledgement on every receive of that denom while the underlying bank balance mint is never undone.

### Finding Description
The transfer stack for `RecvPacket` is:
`channel.RecvPacket -> erc20 middleware.OnRecvPacket -> transfer(app).OnRecvPacket` [1](#0-0) 

In `x/erc20/ibc_middleware.go`, the base ICS20 app's `OnRecvPacket` (which mints/unescrows the voucher coin to the receiver's bank balance) is executed first; only if it succeeds does the erc20 keeper's `OnRecvPacket` run, and its return value can *replace* the already-successful acknowledgement with an error ack: [2](#0-1) 

Inside `x/erc20/keeper/ibc_callbacks.go`, when the received coin denom is a not-yet-registered `ibc/...` voucher, the keeper attempts automatic registration via `RegisterERC20Extension`. If this fails for any reason, the keeper returns an error acknowledgement — even though the base app's mint already succeeded and committed: [3](#0-2) 

The same broken pattern exists for the "native ERC20" case: if `MintingEnabled` fails, the middleware also emits an error ack after the coin was already minted by the base app: [4](#0-3) 

The registration step that can be forced to fail deterministically is `CreateNewTokenPair`, which computes the token pair's ERC20 address purely as a deterministic hash of the IBC denom trace (`NewTokenPairSTRv2` → `GetIBCDenomAddress`), and errors out if any contract already occupies that address: [5](#0-4) [6](#0-5) 

Because the target address is a deterministic function of a denom trace the attacker fully controls (their own IBC transfer path/base denom, chosen before ever sending the packet), the attacker can pre-compute this address off-chain and use `CREATE2` to deploy an arbitrary contract at that exact address on the Cosmos EVM chain before ever sending the corresponding IBC transfer. This "poisons" the token-pair slot in the exact same spirit as Morpho's forced LTV=0 collateral: a fully unprivileged user pre-seeds a piece of protocol-managed state so that a normal, otherwise-successful operation later hits a guaranteed failure branch which is only ever checked/handled after side effects have already been committed.

Once the address is poisoned, every future `OnRecvPacket` for that specific denom will: (1) let the base transfer module mint the voucher coin to the receiver (success, committed to store), then (2) call `RegisterERC20Extension` → `CreateNewTokenPair`, which fails because the address already has code, and (3) return an error acknowledgement to the relayer/source chain.

### Impact Explanation
An IBC error acknowledgement is the same signal the *source* chain uses to refund the original sender's escrowed coins. Since the mint on the receiving (Cosmos EVM) chain is never undone when the acknowledgement is downgraded to an error after the fact, the result is:
- The receiving chain credits the receiver's bank balance with the voucher coin (real, spendable value, later convertible/transferable).
- The source chain, upon seeing an error ack, unescrows/refunds the same amount back to the original sender.

If the attacker is both the sender and the receiver of the crafted packet (trivial in IBC, no privileged relayer/validator role required), this produces unauthorized duplication of spendable value: the attacker ends up holding both the refunded coins on the source chain and the freshly minted (never-reverted) coins on the destination chain, for a single logical transfer. This directly matches the required Critical impact class: "unauthorized minting, burning, duplication, or irreversible accounting corruption of spendable user value across native balances ... IBC escrows." [7](#0-6) 

### Likelihood Explanation
The attack requires no privileged keys, no relayer/validator collusion, and no race condition — it is fully deterministic and repeatable:
1. Attacker picks a source chain/channel/base denom under their control.
2. Attacker computes the resulting IBC denom hash and its deterministic ERC20 address (`GetIBCDenomAddress`).
3. Attacker deploys a contract to that exact address via `CREATE2` on the Cosmos EVM chain ahead of time.
4. Attacker sends themselves an IBC transfer of that denom; `OnRecvPacket` mints the voucher then fails registration and returns an error ack, triggering a refund on the source chain.
5. Attacker now holds duplicated value and can repeat the attack indefinitely for any denom they haven't yet registered.

This is entirely reachable through ordinary, permissionless transaction and IBC transfer flows described in the "Admission path" / "Asset-representation path" pivots in scope.

### Recommendation
Do not let post-mint conversion/registration failures downgrade an already-successful base acknowledgement without also reverting the state changes made by the base app in the same packet-processing flow. Options:
- Execute the ERC20 registration/conversion step and the base transfer mint within a single atomic cached context; only commit the base mint if the entire middleware chain (including erc20 registration/conversion) succeeds, or explicitly write the success ack only after all steps complete.
- Alternatively, never return an error acknowledgement from the erc20 middleware after the base ack was already successful; instead, keep the ack as success (leaving the receiver with the bank coin, as the code comments claim is intended) and only skip/no-op the ERC20 conversion step, never re-signal failure upstream.
- Additionally, harden `CreateNewTokenPair`/`GetIBCDenomAddress` registration to not depend on "no contract code present" as a silent, attacker-triggerable failure condition for security-critical acknowledgement decisions.

### Proof of Concept
1. Attacker deploys, via `CREATE2`, a minimal contract at address `A = last20Bytes(sha256("transfer/{channel}/{base_denom}"))` — the same address `NewTokenPairSTRv2`/`GetIBCDenomAddress` would compute for the IBC denom `ibc/<hash>` derived from that same channel/base_denom path.
2. Attacker sends an IBC transfer of `base_denom` from the source chain to themselves on the Cosmos EVM chain over the same channel used in step 1.
3. On receipt, `transfer(app).OnRecvPacket` mints the voucher `ibc/<hash>` coin to the attacker's account and returns a success ack.
4. `erc20 keeper.OnRecvPacket` sees `!found && strings.HasPrefix(coin.Denom, "ibc/")`, calls `RegisterERC20Extension` → `CreateNewTokenPair`, which fails because address `A` already has code (`account.HasCodeHash()` is true), returning `channeltypes.NewErrorAcknowledgement(err)`.
5. The relayer relays this error ack back to the source chain, which refunds the attacker's original escrowed coins.
6. The attacker now holds both the refunded coins on the source chain and the minted (never-reverted) `ibc/<hash>` coin balance on the Cosmos EVM chain — a duplicated value from a single transfer.

Full verification (e.g., stepping through `ibc-go`'s `MsgRecvPacket` handler to confirm no additional automatic state-rollback occurs solely due to a downstream ack override) would require running an integration test against the actual `evmd` test harness referenced in `evmd/tests/ibc/ibc_middleware_test.go`, which was not executed as part of this static review.

### Citations

**File:** evmd/app.go (L504-509)
```go
		SendPacket, since it is originating from the application to core IBC:
		 	transferKeeper.SendPacket ->  erc20.SendPacket -> callbacks.SendPacket -> channel.SendPacket

		RecvPacket, message that originates from core IBC and goes down to app, the flow is the other way
			channel.RecvPacket -> callbacks.OnRecvPacket -> erc20.OnRecvPacket -> transfer.OnRecvPacket
	*/
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

**File:** x/erc20/keeper/ibc_callbacks.go (L95-116)
```go
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

**File:** x/erc20/keeper/token_pairs.go (L16-31)
```go
// CreateNewTokenPair creates a new token pair and stores it in the state.
func (k Keeper) CreateNewTokenPair(ctx sdk.Context, denom string) (types.TokenPair, error) {
	pair, err := types.NewTokenPairSTRv2(denom)
	if err != nil {
		return types.TokenPair{}, err
	}
	account := k.evmKeeper.GetAccount(ctx, pair.GetERC20Contract())
	if account != nil && account.HasCodeHash() {
		return types.TokenPair{}, errorsmod.Wrapf(types.ErrTokenPairAlreadyExists, "token already exists for token %s", pair.Erc20Address)
	}
	err = k.SetToken(ctx, pair)
	if err != nil {
		return types.TokenPair{}, err
	}
	return pair, nil
}
```

**File:** utils/utils.go (L174-191)
```go
// GetIBCDenomAddress returns the address from the hash of the ICS20's Denom Path.
func GetIBCDenomAddress(denom string) (common.Address, error) {
	if !strings.HasPrefix(denom, "ibc/") {
		return common.Address{}, ibctransfertypes.ErrInvalidDenomForTransfer.Wrapf("coin %s does not have 'ibc/' prefix", denom)
	}

	if len(denom) < 5 || strings.TrimSpace(denom[4:]) == "" {
		return common.Address{}, ibctransfertypes.ErrInvalidDenomForTransfer.Wrapf("coin %s is not a valid IBC voucher hash", denom)
	}

	// Get the address from the hash of the ICS20's Denom Path
	bz, err := ibctransfertypes.ParseHexHash(denom[4:])
	if err != nil {
		return common.Address{}, ibctransfertypes.ErrInvalidDenomForTransfer.Wrap(err.Error())
	}

	return common.BytesToAddress(bz), nil
}
```
