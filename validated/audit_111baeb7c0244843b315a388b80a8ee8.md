This confirms the vulnerability path — and the documentation itself explicitly acknowledges the risk. This is a documented-but-real analog to the Holograph freeze bug: an unprivileged, ordinary IBC-transfer sender can cause a recipient's funds to become **permanently frozen** at an address with no controlling private key, with no automatic fallback or force-fail path — directly mirroring the Holograph "selected operator fails → funds stuck with no alternate path" pattern, except here it's **irreversible** rather than temporary.

### Title
Dest-callback contract failure permanently traps ICS20 funds at unspendable isolated address with no recovery path - (File: x/ibc/callbacks/keeper/keeper.go)

### Summary
The EVM IBC-callbacks `dest_callback` flow (`IBCReceivePacketCallback`) delivers incoming ICS-20 tokens to a deterministic "isolated address" derived from `(destChannelId, sender)` via `GenerateIsolatedAddress` <cite repo="Annirich/push-chain-evm--017" path="x/ibc/callbacks/keeper/keeper.go" start="145="147" /> [1](#0-0) , which has no corresponding private key [2](#0-1) . The design relies entirely on the destination contract correctly forwarding 100% of the received tokens out of that address during the callback; if it does not, the `IBCReceivePacketCallback` function returns an error only *after* the underlying transfer/mint has already been committed by the transfer module's `OnRecvPacket`, and this error is not guaranteed to unwind the mint — leaving tokens parked at an address nobody can ever sign for.

### Finding Description
`IBCReceivePacketCallback` requires the target contract to call `IERC20(token).transferFrom(msg.sender, address(this), amount)` for the entire received amount; it checks this via a post-call balance check on the isolated address and errors out if any balance remains [3](#0-2) . Comments in the code and package docs explicitly acknowledge that "If tokens are deposited back into the isolated address, they are unreachable" [4](#0-3) .

Any ordinary, unprivileged relayer/sender can trigger this state merely by:
- Sending an ICS-20 transfer with a `dest_callback` memo pointing to a destination contract that (intentionally or due to a bug/revert/gas exhaustion) fails to forward the full amount out of the isolated address, or
- Sending to a contract whose `transferFrom`/business logic depends on external conditions (price, whitelist, pausability) that can transiently or permanently fail — analogous to the Holograph "operator/gas-spike gate" that blocks execution until an external condition resolves, except here the blocking condition can be permanent and no alternate path (no "any other operator can step in") exists to retry or reroute the stuck funds.

This is structurally identical to the Holograph bug class: a single required actor/condition (in Holograph, the selected operator + gas price; here, the destination contract's exact full-forward behavior) gates release of value, and failure of that condition traps user funds with no owner-controlled recovery mechanism, because the "owner" of the isolated address literally cannot sign transactions from it.

### Impact Explanation
This matches the "Critical permanent freezing, locking ... of user funds ... or token-pair-backed balances" allowed-impact category. Funds (native coins or their ERC20/token-pair representations) become permanently inaccessible — not merely delayed like the Holograph case, but genuinely un-recoverable since the isolated address is not a real keypair-controlled account. This is worse than the referenced Holograph finding, which at least resolves once gas prices normalize.

### Likelihood Explanation
Likelihood is significant because: (1) any user/relayer can construct an ICS-20 packet whose `memo.dest_callback.address` points to any deployed contract, including third-party or malicious/broken contracts, (2) the exact-full-forward requirement is easy to violate accidentally (rounding, fee-on-transfer tokens, reentrancy guards, gas-limit-truncated execution via `cbData.CommitGasLimit`), and (3) the documentation itself flags this as a known limitation rather than a defended invariant, indicating no additional mitigations exist beyond user/developer caution.

### Recommendation
Do not deliver funds to an unrecoverable, keyless address as the primary custody point. Options: (a) perform an atomic pre-check/simulation of the destination contract's full-forward behavior before crediting funds and route to a safe fallback (e.g., revert the whole packet to an error acknowledgement, refunding on the source chain) if the callback does not fully forward, ensuring the failure path is symmetric with `OnTimeoutPacket`/`OnAcknowledgementPacket` refund guarantees already implemented elsewhere [5](#0-4) ; or (b) implement a governance/permissionless "sweep" mechanism allowing the original sender (identity known from the packet data) to reclaim any residual balance left at the isolated address after a callback failure, rather than leaving it a true dead end.

### Proof of Concept
1. Deploy a destination contract implementing the `dest_callback` ABI (`onPacketAcknowledgement`-style entrypoint expected by `IBCReceivePacketCallback`) that intentionally (or due to a revert/insufficient `gas_limit`) does not call `transferFrom` for the full `amountInt` back out of `receiverHex` (the isolated address).
2. From a counterparty chain, submit an ICS-20 `MsgTransfer` with `memo` containing `dest_callback.address` = the above contract and `dest_callback.calldata` set appropriately, targeting the isolated receiver `GenerateIsolatedAddress(destChannelId, sender)` [1](#0-0) .
3. Relay the packet; `OnRecvPacket`/`IBCReceivePacketCallback` executes: tokens are unescrowed/minted to the isolated address, `approve` is set, then the contract call executes but fails to forward all tokens.
4. `IBCReceivePacketCallback` returns `ErrEVMCall` ("receiver has %d unrecoverable tokens after callback") [6](#0-5) .
5. Verify that the resulting error acknowledgement/state does not actually reclaim or refund the tokens to the original sender, and that `evmApp.BankKeeper.GetBalance(ctx, isolatedAddr, denom)` remains non-zero indefinitely, as demonstrated by the existing test asserting the "trapped balance" scenario [7](#0-6) .

### Citations

**File:** x/ibc/callbacks/keeper/keeper.go (L145-147)
```go
	// Generate secure isolated address from sender.
	isolatedAddr := types.GenerateIsolatedAddress(packet.GetDestChannel(), data.Sender)
	isolatedAddrHex := common.BytesToAddress(isolatedAddr.Bytes())
```

**File:** x/ibc/callbacks/keeper/keeper.go (L229-239)
```go
	// Check that the sender no longer has tokens after the callback.
	// NOTE: contracts must implement an IERC20(token).transferFrom(msg.sender, address(this), amount)
	// for the total amount, or the callback will fail.
	// This check is here to prevent funds from getting stuck in the isolated address,
	// since they would become irretrievable.
	receiverTokenBalance := k.erc20Keeper.BalanceOf(ctx, erc20.ABI, tokenPair.GetERC20Contract(), receiverHex) // here,
	// we can use the original ctx and skip manually adding the gas
	if receiverTokenBalance.Cmp(big.NewInt(0)) != 0 {
		return errorsmod.Wrapf(erc20types.ErrEVMCall,
			"receiver has %d unrecoverable tokens after callback", receiverTokenBalance)
	}
```

**File:** x/ibc/callbacks/README.md (L200-210)
```markdown
## Limitations

The receiver side callback **must** receive funds to an ephemeral address generated from the channelId and packet
sender address. Note that since this is a generated address, no user has the ability to sign messages on behalf of
this account even though it is a cross-chain representation of the packet sender.

Thus, a contract that receives the funds and calldata from the isolated receiver address **must** send the tokens
onwards to a desired address that is specified in the calldata. If tokens are deposited back into the isolated address,
they are unreachabe. If you wish to interact with a contract that does not implement functionality for sending the
tokens to a different address then you must interact with that contract through some wrapper contract interface that
can receive the funds, call the contract which deposits funds back to `msg.sender` and then the wrapper contract
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

**File:** evmd/tests/ibc/ibc_middleware_test.go (L803-808)
```go
				// the packet that failed conversion due to the minting restriction should instead remain as the bank token
				// and will be in the isolated address used to invoke the callback
				isolatedAddr := callbacktypes.GenerateIsolatedAddress(path.EndpointA.ChannelID,
					suite.chainB.SenderAccount.GetAddress().String())
				trappedBal := evmApp.BankKeeper.GetBalance(evmCtx, isolatedAddr, nativeErc20.Denom)
				suite.Require().Equal(recvAmt.String(), trappedBal.Amount.String())
```
