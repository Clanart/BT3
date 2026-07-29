Based on the investigation, the key finding is in `x/erc20/ibc_middleware.go`: `IBCMiddleware.OnRecvPacket` first calls `im.Module.OnRecvPacket` (the standard ICS20 transfer app, which mints/unlocks the voucher coins to the recipient's bank balance), and only *after* that success does it call `im.keeper.OnRecvPacket` for ERC20 conversion/registration [1](#0-0) . If `k.OnRecvPacket` returns an error acknowledgement (e.g. from `RegisterERC20Extension` failing), that error acknowledgement is returned to the relayer/source chain as the final ack for the packet [2](#0-1) .

The `RegisterERC20Extension`/`CreateNewTokenPair` path can fail because the ERC-20 contract address for a given IBC denom is **fully deterministic and computable in advance** — it's derived only from `sha256(denom trace path)`, with no dependency on any deployer account/nonce/salt [3](#0-2) . `CreateNewTokenPair` checks whether that predicted address already `HasCodeHash()` and, if so, rejects registration with `ErrTokenPairAlreadyExists` [4](#0-3) . This is confirmed as an intentionally tested collision scenario in `test_ibc_callback.go`, where a pre-existing contract at the "collided" address causes `ackSuccess: false` [5](#0-4) .

This is the same bug class as the ETH2.0 report: a publicly-predictable identity/address slot can be front-run by an unprivileged attacker (anyone can compute the address for an IBC denom that a legitimate user is about to receive for the first time and deploy a contract to it via CREATE2 ahead of time), which then desynchronizes two systems that are supposed to move atomically — the underlying transfer mint (already committed) and the acknowledgement sent back to the source chain (which reports failure).

**What I could not fully verify:** whether the underlying `im.Module.OnRecvPacket` mint that already occurred is rolled back when the subsequent `k.OnRecvPacket` call returns an error acknowledgement. The IBC-Go core relayer path typically treats a returned `Acknowledgement.Error()` as informational only (written into store, not a state revert), meaning state changes from `im.Module.OnRecvPacket` (i.e. the bank-side mint of the ibc/ voucher into the recipient's balance) are **not automatically undone** just because a later middleware layer in the same call stack returns an error ack — unless the module explicitly wraps this in a `CacheContext`. I did not find any `ctx.CacheContext()` wrapping around the two-step `im.Module.OnRecvPacket` + `im.keeper.OnRecvPacket` sequence in `x/erc20/ibc_middleware.go` (unlike the callbacks keeper, which explicitly uses `ctx.CacheContext()` for its EVM callback execution [6](#0-5) ). If mint state is not rolled back while an error ack is returned to the source chain, the source chain would treat the transfer as failed and refund the sender's escrowed coins — while the destination chain has already credited the same value to the recipient's bank balance, producing a duplicate/double-spend of the transferred value. Confirming this requires tracing whether the SDK's `ibctransferkeeper.OnRecvPacket` implementation, or the message handler that invokes the full middleware stack, applies a cache-and-write-on-success pattern at the transaction level (i.e., whether `baseapp`/msg-service handling reverts all state changes for a `MsgRecvPacket` if the final acknowledgement is an error). This is standard IBC-Go core behavior (`RecvPacket` handler does NOT revert application state based on ack content — acks are advisory data written to state, not automatic rollback triggers) but I was not able to directly inspect the `04-channel` `RecvPacket` handler in this repo to confirm it hasn't been customized.

### Title
Predictable ERC20 address for IBC denoms allows front-run of token-pair registration, desynchronizing mint state from acknowledgement outcome - (File: x/erc20/keeper/ibc_callbacks.go)

### Summary
The ERC-20 contract address used for auto-registering an IBC-received coin is deterministically derived from `sha256(denom-trace)` with no dependency on any deployer identity, similar to how the ETH2.0 deposit contract's identity (pubkey) is not uniquely bound to a specific initial depositor. An unprivileged attacker can precompute this address for any IBC denom that a victim is about to receive for the first time, deploy code to it beforehand, and cause `RegisterERC20Extension` to fail on the victim's `OnRecvPacket`, which propagates an error acknowledgement back to the source chain while the underlying ICS20 transfer's bank-side mint may have already been committed.

### Finding Description
`GetIBCDenomAddress` computes the address purely as a hash of the public denom-trace string [3](#0-2) , and `NewTokenPairSTRv2`/`CreateNewTokenPair` use this address as the canonical, sole identity for the token pair, checking only whether an account already has code at that address [7](#0-6) [4](#0-3) . Because the address is fully predictable from public information (port, channel, base denom), any address is "claimable" ahead of time by depositing/deploying a contract (e.g., via `CREATE2` from a controlled deployer, or via any EVM tx that ends up assigning code to that address) before the first IBC packet for that denom arrives. When the legitimate packet arrives, `k.OnRecvPacket`'s Case 1 branch calls `RegisterERC20Extension`, which fails and returns an error acknowledgement [2](#0-1) . This error ack is returned as the final acknowledgement for the packet from `IBCMiddleware.OnRecvPacket`, even though the underlying transfer app's `OnRecvPacket` (which performs the actual coin mint/escrow release) already executed and returned success before the erc20 keeper's callback runs [1](#0-0) .

### Impact Explanation
If the mint performed by the underlying ICS20 transfer app is not rolled back when the erc20-module's post-processing returns an error acknowledgement, the result is a duplication of value: the destination chain credits the recipient with the received coin (now stuck as a raw bank denom instead of being converted, but still spendable/transferable), while the source chain, upon seeing the error acknowledgement, refunds the sender's escrowed coins as if the transfer failed. This produces two spendable balances from a single cross-chain transfer of value — a critical, unauthorized duplication of user funds across native/IBC balances, matching the required Critical impact class.

### Likelihood Explanation
The predicted contract address depends solely on public data (denom trace / channel), so a sophisticated attacker who knows a victim intends to bridge a new denom for the first time (e.g., is about to relay a `MsgTransfer` for a not-yet-seen source denom) can precompute the target address and pre-deploy code there. This requires no privileged access, only ordinary EVM transactions, making the trigger unprivileged and reachable via normal traffic.

### Recommendation
Wrap the sequential `im.Module.OnRecvPacket` + `im.keeper.OnRecvPacket` calls in `x/erc20/ibc_middleware.go` in a `ctx.CacheContext()` so that any error acknowledgement from the erc20-registration path atomically reverts the underlying transfer app's state changes (mint/unescrow) before the error ack is returned, ensuring the acknowledgement outcome and actual state changes stay consistent — analogous to the pattern already used in `x/ibc/callbacks/keeper/keeper.go`.

### Proof of Concept
Conceptual PoC (requires live-chain/testnet verification, which I could not execute here):
1. Attacker observes a pending or soon-to-be-initiated first-time IBC transfer of a new source denom to the target chain, computing the destination `ibc/<hash>` denom and its deterministic ERC20 address via `GetIBCDenomAddress`.
2. Attacker deploys any contract to that exact address ahead of time (e.g., using `CREATE2` with an appropriate salt search, or another mechanism that assigns code to that specific address).
3. Victim's IBC transfer packet is relayed; `IBCMiddleware.OnRecvPacket` executes the underlying transfer app's `OnRecvPacket` (minting/unescrowing coins to the victim on the destination chain) successfully.
4. `k.OnRecvPacket` then calls `RegisterERC20Extension` → `CreateNewTokenPair`, which fails because the target address already `HasCodeHash()`, producing an error acknowledgement.
5. The source chain, upon relaying the error acknowledgement, executes its own `OnAcknowledgementPacket` refund logic, crediting the sender's escrowed balance back — while the destination chain retains the minted bank-denom credit to the victim, yielding two live balances for one transferred value.

This last step (confirming the source-chain refund logic executes despite the mint being already committed on the destination chain, i.e., that there is no atomic rollback) requires further verification via a live integration test or Devin session with full repository/test execution access, which was not available in this ask-only investigation.

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

**File:** x/erc20/keeper/ibc_callbacks.go (L98-116)
```go
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

**File:** tests/integration/x/erc20/test_ibc_callback.go (L220-244)
```go
		},
		{
			name: "error - pair is not registered but address has code",
			malleate: func() {
				transfer := transfertypes.NewFungibleTokenPacketData(erc20Denom, "100", secpAddrCosmos, ethsecpAddrEvmos, "")
				bz := transfertypes.ModuleCdc.MustMarshalJSON(&transfer)
				packet = channeltypes.NewPacket(bz, 1, transfertypes.PortID, sourceChannel, transfertypes.PortID, cosmosEVMChannel, timeoutHeight, 0)
				collidedAddr, err := utils.GetIBCDenomAddress(transfertypes.NewDenom(erc20Denom, hop).IBCDenom())
				s.Require().NoError(err)
				s.Require().False(s.network.App.GetErc20Keeper().IsERC20Registered(ctx, collidedAddr))
				err = s.network.App.GetEVMKeeper().SetAccount(ctx, collidedAddr, statedb.Account{
					Nonce:    0,
					Balance:  nil,
					CodeHash: []byte{1, 2, 3, 4, 5, 6, 7, 8, 9, 10},
				})
				s.Require().NoError(err)
				acct := s.network.App.GetEVMKeeper().GetAccount(ctx, collidedAddr)
				s.Require().True(acct.HasCodeHash())
			},
			ackSuccess:    false,
			receiver:      secpAddr,
			expErc20s:     big.NewInt(0),
			expCoins:      coins,
			checkBalances: false,
		},
```

**File:** x/ibc/callbacks/keeper/keeper.go (L129-131)
```go
	cachedCtx, writeFn := ctx.CacheContext()
	cachedCtx = evmante.BuildEvmExecutionCtx(cachedCtx).
		WithGasMeter(evmtypes.NewInfiniteGasMeterWithLimit(cbData.CommitGasLimit))
```

**File:** x/erc20/types/token_pair.go (L13-29)
```go
// NewTokenPairSTRv2 creates a new TokenPair instance in the context of the
// Single Token Representation v2.
//
// It derives the ERC-20 address from the hex suffix of the IBC denomination
// (e.g. ibc/DF63978F803A2E27CA5CC9B7631654CCF0BBC788B3B7F0A10200508E37C70992).
func NewTokenPairSTRv2(denom string) (TokenPair, error) {
	address, err := utils.GetIBCDenomAddress(denom)
	if err != nil {
		return TokenPair{}, err
	}
	return TokenPair{
		Erc20Address:  address.String(),
		Denom:         denom,
		Enabled:       true,
		ContractOwner: OWNER_MODULE,
	}, nil
}
```
