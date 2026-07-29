### Title
IBC token-pair registration race allows an attacker to force an ERC20-registration failure *after* funds are already minted, causing unauthorized double-crediting across the IBC escrow — analogous to the Velodrome `setGauge` front-run - (File: `x/erc20/keeper/ibc_callbacks.go`, `x/erc20/keeper/token_pairs.go`, `x/erc20/ibc_middleware.go`)

### Summary
The Velodrome bug is a race between two supposedly-atomic setup steps (`Bribe` deploy → `Gauge` constructor calling `setGauge`), where an attacker inserts a transaction in between to permanently break the linkage. The Cosmos EVM analog is the two-step, **non-atomic** flow used when receiving a brand-new IBC denomination: `x/erc20`'s IBC middleware first lets the core transfer app **mint/unescrow the Cosmos coin to the receiver**, and only *afterward*, in a separate, revertible-looking but actually non-rolled-back step, tries to register the deterministic ERC20 "extension" for that denom. An attacker who front-runs that second step (by occupying the deterministic ERC20 address with contract code beforehand) can force that step to fail, which causes the middleware to return an **error acknowledgement** for a packet whose underlying coin transfer has *already been committed* to the receiver's bank balance.

### Finding Description
`IBCMiddleware.OnRecvPacket` in [1](#0-0)  first calls the core transfer application (`im.Module.OnRecvPacket`), which performs the actual bank mint/unescrow of the received coin to the recipient. Only if that succeeds does it call `im.keeper.OnRecvPacket(ctx, packet, ack)` — a **second, separate step** that is not wrapped in any `CacheContext`/rollback mechanism.

Inside `Keeper.OnRecvPacket` [2](#0-1) , when the coin denom is a brand-new `ibc/...` voucher with no existing token pair, the keeper calls `RegisterERC20Extension`, which in turn calls `CreateNewTokenPair`: [3](#0-2) 

`CreateNewTokenPair` derives a **fully deterministic** ERC20 contract address from the denom string via `types.NewTokenPairSTRv2(denom)` and then checks whether an account with code already exists at that predicted address:
```go
account := k.evmKeeper.GetAccount(ctx, pair.GetERC20Contract())
if account != nil && account.HasCodeHash() {
    return types.TokenPair{}, errorsmod.Wrapf(types.ErrTokenPairAlreadyExists, ...)
}
```
Because the target address is a pure function of the public `ibc/<hash>` denom (channel + base denom are public IBC-relayer information, or the attacker can trivially self-transfer one unit of the token first to learn/derive the denom), an attacker can pre-compute this address off-chain and grind a `CREATE`/`CREATE2` deployment (vanity-address mining, a well-known, fully public/unprivileged technique) to place *any* contract bytecode at that exact address before the real IBC transfer packet for that denom is ever relayed.

When the real packet later arrives:
1. `im.Module.OnRecvPacket` executes normally and the recipient's bank balance is credited with the transferred coin (this state change is committed, not reverted).
2. `k.OnRecvPacket` → `RegisterERC20Extension` → `CreateNewTokenPair` sees the attacker's pre-planted code at the deterministic address and returns `ErrTokenPairAlreadyExists`.
3. `Keeper.OnRecvPacket` converts this into `channeltypes.NewErrorAcknowledgement(err)` [4](#0-3) , which becomes the **final acknowledgement written back to the counterparty chain**.

An error acknowledgement causes the source chain to treat the transfer as failed and **refund/unescrow the sender's original funds** there. Meanwhile, the receiving (Cosmos EVM) chain has already committed the mint/unescrow to the receiver in step 1, since there is no cache-context rollback tying the acknowledgement outcome to the earlier bank state change.

### Impact Explanation
This produces unauthorized duplication of spendable value across chains: the sender is refunded on the source chain while the receiver retains the freshly minted/unescrowed coins on the destination chain, for the exact same IBC transfer. This directly matches the in-scope Critical impact of "unauthorized minting, burning, duplication ... of spendable user value across native balances ... IBC escrows." It is triggered by an ordinary, unprivileged sequence of EVM transactions (grinding and deploying a contract to a predictable address) with no validator/relayer/governance privilege required — a first-transfer of a new IBC denom is a normal, permissionless event that any user (including an attacker) can also trigger to learn the target denom in advance.

### Likelihood Explanation
Likelihood is moderate: the attacker must (a) predict or learn the exact new `ibc/<hash>` denom that will first traverse a channel, and (b) grind a `CREATE`/`CREATE2` salt to deploy bytecode at the deterministic target address before the real (often relayer-driven) first transfer packet lands. Both are unprivileged, purely computational tasks with no special access, and the attacker fully controls timing since they can initiate their own dummy transfer of the same denom first to learn/derive it, then race the registration step for a larger subsequent transfer. This is directly analogous in spirit and mechanics to the referenced Velodrome `setGauge` front-run: exploiting the non-atomicity between "asset arrives" and "asset's derived-address registration succeeds."

### Recommendation
- Wrap the entire receive-and-convert flow (`im.Module.OnRecvPacket` mint + `k.OnRecvPacket` registration/conversion) in a single `ctx.CacheContext()` that is only written to the parent store if *both* steps succeed; otherwise return the error acknowledgement without having committed the mint.
- Alternatively/additionally, decouple the ack outcome from the ERC20-extension registration: if `RegisterERC20Extension` fails, do not fail the entire IBC receive (do not convert the successful transfer ack into an error ack) — instead leave the coin as a plain bank-only voucher (no ERC20 view) and emit a failure event, matching the pattern already used elsewhere in this codebase for `ConvertCoinNativeERC20`/`MintingEnabled` failures where the ack still succeeds and the user gets the bank token.
- Consider deriving/reserving the ERC20 extension address in a way that cannot be squatted via ordinary `CREATE`/`CREATE2` (e.g., checking and reserving the account slot at channel-open time, or using a namespace not reachable by normal contract-creation address derivation).

### Proof of Concept
1. Attacker observes (or triggers via their own minimal transfer) the future `ibc/<hash>` denom for a given channel/base-denom pair that will be used for a legitimate large transfer.
2. Attacker computes the deterministic ERC20 address `addr = NewTokenPairSTRv2(denom).GetERC20Contract()`.
3. Attacker grinds a `CREATE`/`CREATE2` deployment from an attacker-controlled factory to place trivial bytecode at `addr`, committing this before the legitimate transfer's packet is relayed.
4. The legitimate relayer relays the real transfer packet for `denom`. `im.Module.OnRecvPacket` mints the coin to the receiver's bank balance (committed).
5. `Keeper.OnRecvPacket` → `RegisterERC20Extension` → `CreateNewTokenPair` finds `account.HasCodeHash() == true` at `addr` and returns `ErrTokenPairAlreadyExists`.
6. Middleware returns `channeltypes.NewErrorAcknowledgement(err)` as the final ack.
7. Source chain processes the error ack and refunds/unescrows the sender's original funds, while the receiver already holds the minted coins on the destination chain — a duplicated value across the two chains for a single transfer.

Note: I was not able to execute this end-to-end in a live network from the index alone (no terminal/tooling access here), so the exact refund mechanics on the *source* chain's ack-error handling should be independently verified against the ibc-go version vendored in this repo, but the destination-chain non-atomicity (mint before registration, with no rollback on registration failure) is directly confirmed in the cited code.

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
