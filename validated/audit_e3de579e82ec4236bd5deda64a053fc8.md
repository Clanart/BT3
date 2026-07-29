Found: `ConvertCoinToERC20FromPacket` (Case 2, native ERC20) at `x/erc20/keeper/ibc_callbacks.go:216-253` does **not** check `pair.Enabled` before calling `ConvertCoinNativeERC20`, unlike every other conversion entrypoint (`MintingEnabled`, `OnRecvPacket` Case 2, ICS20 `Transfer`) which explicitly gate on `pair.Enabled`. This mirrors the report's pattern exactly: the "create" check (`MintingEnabled`/`OnRecvPacket`) enforces validity, but a later, unprivileged-triggerable path (IBC ack-error / timeout refund) omits it.

### Title
Disabled token pair conversion bypassed on IBC refund path allows minting ERC20 backed by escrow after conversion is switched off - (File: x/erc20/keeper/ibc_callbacks.go)

### Summary
`ConvertCoinToERC20FromPacket`, invoked from `OnAcknowledgementPacket` (error ack) and `OnTimeoutPacket`, converts refunded native coins into their ERC20 representation via `ConvertCoinNativeERC20` without checking `pair.Enabled`. [1](#0-0) . Every other conversion path checks this flag: `MintingEnabled` [2](#0-1) , `OnRecvPacket` Case 2 [3](#0-2) , and ICS20 `Transfer` [4](#0-3) .

### Finding Description
When governance calls `ToggleConversion` to disable a native-ERC20 token pair (e.g., because the underlying ERC20 contract is misbehaving, self-destructed-but-not-yet-cleaned-up, or otherwise unsafe), `pair.Enabled` is flipped to `false` [5](#0-4) . This is intended to halt ERC20<->coin conversions for that pair, matching the intent enforced in `MintingEnabled` and inbound IBC recv handling.

However, `ConvertCoinToERC20FromPacket` only checks `pair.IsNativeCoin()` / `pair.IsNativeERC20()`, the module-wide `EnableErc20` param, and whether the denom is registered — it never checks `pair.Enabled` — before calling `k.ConvertCoinNativeERC20(ctx, pair, coin.Amount, ...)` [6](#0-5) . This function is reached whenever:
- An outbound IBC transfer of a native-ERC20-backed coin (initiated by an ordinary unprivileged user via `MsgTransfer`) receives an error acknowledgement, or
- Such a transfer times out.

Both conditions are attacker/user-triggerable without any privilege: a user can send an IBC transfer to a channel/counterparty they control or that they know will error/time out (e.g., sending to a non-existent channel, an incompatible receiver, or simply waiting for timeout), then race governance's disabling of the pair, or simply rely on natural network timeouts occurring after the pair is disabled mid-transit.

`ConvertCoinNativeERC20` unescrows previously-escrowed native ERC20 tokens from the module account and transfers them to the user, then burns the corresponding bank coins [7](#0-6) . If the pair is disabled specifically because the ERC20 contract is compromised, no longer trusted, or its 1:1 backing invariant is broken (the exact scenario the report describes for oracle/symbol invalidation), this code path still executes the conversion, extracting tokens from the module-controlled escrow in violation of the governance decision to halt the pair.

### Impact Explanation
This breaks the intended invariant that a disabled token pair must not allow any further coin<->ERC20 conversions, undermining the very reason `Enabled` exists (to be able to halt conversions when the ERC20 contract or backing is untrustworthy). Depending on why the pair was disabled, this can let a user extract module-escrowed ERC20 tokens they should no longer be able to redeem, corrupting the 1:1 accounting between native coins and the ERC20 representation that the module is designed to preserve (per `ConvertCoinNativeERC20`'s own invariant check comment) [8](#0-7) .

### Likelihood Explanation
Any unprivileged user performing a routine IBC transfer of a native-ERC20-backed coin can trigger the refund path via a natural timeout or an error acknowledgement (both are ordinary, unprivileged-triggerable outcomes of IBC transfers). No relayer or validator collusion is required — timeouts are a standard part of IBC transfer flows.

### Recommendation
Add a `pair.Enabled` check in `ConvertCoinToERC20FromPacket` (mirroring the check in `OnRecvPacket` Case 2) before calling `ConvertCoinNativeERC20`; if disabled, the refunded amount should remain as native bank coin only (no-op on ERC20 reconversion), consistent with how `MintingEnabled` and `OnRecvPacket` already treat disabled pairs.

### Proof of Concept
1. User A sends `MsgTransfer` moving `pair.Denom` (backed by a native ERC20 token pair) over IBC to a channel/receiver destined to fail (e.g., malformed receiver address on counterparty, or a channel about to close).
2. While the packet is in flight, governance calls `ToggleConversion` to disable the pair (e.g., due to a discovered issue with the ERC20 contract).
3. The packet fails (error ack) or times out; `OnAcknowledgementPacket`/`OnTimeoutPacket` invoke `ConvertCoinToERC20FromPacket`.
4. Because `pair.Enabled` is never checked, `ConvertCoinNativeERC20` still executes, unescrowing ERC20 tokens from the module account to User A, despite the pair being disabled — bypassing the governance halt.

### Citations

**File:** x/erc20/keeper/ibc_callbacks.go (L118-123)
```go
	// Case 2. native ERC20 token
	case found && pair.IsNativeERC20():
		// Token pair is disabled -> return
		if !pair.Enabled {
			return ack
		}
```

**File:** x/erc20/keeper/ibc_callbacks.go (L209-237)
```go
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
```

**File:** x/erc20/keeper/mint.go (L43-47)
```go
	if !pair.Enabled {
		return types.TokenPair{}, errorsmod.Wrapf(
			types.ErrERC20TokenPairDisabled, "minting token '%s' is not enabled by governance", token,
		)
	}
```

**File:** x/ibc/transfer/keeper/msg_server.go (L54-58)
```go
	pair, _ := k.erc20Keeper.GetTokenPair(ctx, pairID)
	if !pair.Enabled {
		// no-op: pair is not enabled so we can proceed with regular transfer
		return k.Keeper.Transfer(ctx, msg)
	}
```

**File:** x/erc20/keeper/proposals.go (L116-138)
```go
// ToggleConversion toggles conversion for a given token pair
func (k Keeper) toggleConversion(
	ctx sdk.Context,
	token string,
) (types.TokenPair, error) {
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

	pair.Enabled = !pair.Enabled
	k.SetTokenPair(ctx, pair)
	return pair, nil
}
```

**File:** x/erc20/keeper/msg_server.go (L230-236)
```go
// ConvertCoinNativeERC20 handles the coin conversion for a native ERC20 token
// pair:
//   - escrow Coins on module account
//   - unescrow Tokens that have been previously escrowed with ConvertERC20 and send to receiver
//   - burn escrowed Coins
//   - check if token balance increased by amount
//   - check for unexpected `Approval` event in logs
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
