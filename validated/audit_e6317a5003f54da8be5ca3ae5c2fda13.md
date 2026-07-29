Based on my investigation, I found a genuine analog of the report's "unsafe approve" bug class in the IBC callback flow.

### Title
Non-standard-ERC20-safe `approve` call in IBC receive-packet callback permanently locks user funds at an unspendable isolated address - (File: `x/ibc/callbacks/keeper/keeper.go`)

### Summary
`IBCReceivePacketCallback` sets an ERC20 allowance for the destination contract by directly calling `approve` on the token-pair's ERC20 contract, exactly the pattern flagged in the original report (no zero-reset, no tolerance for non-standard return-value/allowance semantics). Because the funds are held at a deterministically generated "isolated address" that has no known private key, a stuck/reverting `approve` call means the tokens can never be moved out again.

### Finding Description
In `IBCReceivePacketCallback`, after validating the packet and receiver, the keeper approves the destination contract to pull the received tokens on behalf of the isolated receiver: [1](#0-0) 

The call is a plain `approve(contractAddr, amountInt)` — it does not reset the allowance to zero first, and it assumes the ERC20 contract returns a `bool` that can be unpacked with `erc20.ABI.UnpackIntoInterface(&approveSuccess, "approve", res.Ret)`. `tokenPair.GetERC20Contract()` is resolved from the token pair registry and is not guaranteed to be the well-behaved `ERC20MinterBurnerDecimalsContract` — token pairs can be registered for pre-existing, externally-deployed ERC20 contracts (dynamic/native registration in `x/erc20`), which may implement non-standard, USDT-like semantics (revert on approve when current allowance is non-zero, or no return value at all).

The isolated receiver address is generated deterministically from the destination channel and the origin-chain sender string: [2](#0-1) 
This address is not backed by any private key — the only mechanism to move funds out of it is this `approve` + the target contract's own `transferFrom` pull, invoked automatically inside the callback.

If a target contract only partially consumes the granted allowance in a given callback (i.e., its `transferFrom` pulls less than the full approved `amountInt`), a non-zero residual allowance remains for that `(isolatedAddr, contractAddr)` pair. Any subsequent IBC packet arriving at the *same* isolated address (same channel + sender) that triggers another callback will again try to `approve(contractAddr, newAmount)` on top of the non-zero leftover allowance. For a non-standard ERC20 token pair requiring the allowance to be reset to zero before changing it, this second `approve` reverts, causing `IBCReceivePacketCallback` to return `ErrAllowanceFailed`: [3](#0-2) 

Because the underlying IBC token transfer itself already completed (coins credited to the isolated address as part of normal `OnRecvPacket` processing) before the callback runs, the callback failing does not roll back the transfer — it only prevents the contract-mediated extraction path from working. With the isolated address having no private key and the only extraction path now permanently reverting, the tokens already sent to that address become irretrievable.

### Impact Explanation
This satisfies the Critical "permanent freezing/locking of user funds" impact gate: an unprivileged actor (any relayer/sender submitting an ordinary IBC transfer with a destination-callback memo to a contract whose target token is a non-standard ERC20, or simply a contract that doesn't fully drain the approved allowance) can cause funds routed through the isolated address to become permanently stuck, with no admin/governance recovery path implied by the code shown.

### Likelihood Explanation
Requires: (1) a token pair backed by a non-standard ERC20 (or any contract behavior leaving residual allowance) registered in `x/erc20`, and (2) two or more IBC receive-callback packets landing on the same isolated address (same channel+sender) where the second `approve` collides with a non-zero leftover allowance. This is a plausible, unprivileged, repeatable trigger for users interacting with ERC20 tokens that don't support increasing a non-zero allowance directly — not merely a theoretical edge case, mirroring the exact USDT-style problem from the seed report.

### Recommendation
Reset the allowance to zero before setting a new value (mirroring the `safeApprove(0)` + `safeApprove(amount)` pattern from the report), and defensively tolerate non-standard return values instead of requiring `bool` unpack success:
```go
// zero out any existing allowance first
if _, err := k.evmKeeper.CallEVM(cachedCtx, erc20.ABI, receiverHex, tokenPair.GetERC20Contract(), true, remainingGas, "approve", contractAddr, big.NewInt(0)); err != nil {
    return errorsmod.Wrapf(types.ErrAllowanceFailed, "failed to reset allowance: %v", err)
}
res, err := k.evmKeeper.CallEVM(cachedCtx, erc20.ABI, receiverHex, tokenPair.GetERC20Contract(), true, remainingGas, "approve", contractAddr, amountInt.BigInt())
```
Additionally, consider providing a governance/permissionless "sweep" path for funds stranded at isolated addresses when the automated callback path is unrecoverable, so a single non-standard-token interaction cannot permanently strand user funds.

### Proof of Concept
1. Register a token pair for an externally deployed ERC20 contract `T` that reverts `approve()` unless the current allowance is zero (USDT-style semantics).
2. User A sends an IBC transfer of `T` with a destination callback to contract `C`, whose callback logic calls `transferFrom` for only part of the approved amount (leaving a residual allowance > 0). `IBCReceivePacketCallback` succeeds; tokens land at isolated address `I = GenerateIsolatedAddress(channel, senderA)`.
3. User A (or anyone triggering another packet with the same channel + sender string) sends a second IBC transfer of `T` with a callback to the same or another contract. `IBCReceivePacketCallback` computes the same isolated address `I`, and attempts `approve(contractAddr, newAmount)` while `I`'s allowance to some spender is already non-zero from step 2.
4. The `approve` call reverts (or returns without a bool for a token without spend-limit reset support), `IBCReceivePacketCallback` returns `ErrAllowanceFailed`. Because `I` has no private key, the tokens transferred in step 3 (and any un-swept residue from step 2) are now permanently unreachable — no code path exists to reset the allowance or otherwise withdraw funds from `I`.

### Citations

**File:** x/ibc/callbacks/keeper/keeper.go (L145-155)
```go
	// Generate secure isolated address from sender.
	isolatedAddr := types.GenerateIsolatedAddress(packet.GetDestChannel(), data.Sender)
	isolatedAddrHex := common.BytesToAddress(isolatedAddr.Bytes())

	acc := k.authKeeper.NewAccountWithAddress(ctx, receiver)
	k.authKeeper.SetAccount(ctx, acc)

	// Ensure receiver address is equal to the isolated address.
	if receiverHex.Cmp(isolatedAddrHex) != 0 {
		return errorsmod.Wrapf(types.ErrInvalidReceiverAddress, "expected %s, got %s", isolatedAddrHex.String(), receiverHex.String())
	}
```

**File:** x/ibc/callbacks/keeper/keeper.go (L185-212)
```go
	erc20 := contracts.ERC20MinterBurnerDecimalsContract

	remainingGas := math.NewIntFromUint64(cachedCtx.GasMeter().GasRemaining()).BigInt()

	// Call the EVM with the remaining gas as the maximum gas limit.
	// Up to now, the remaining gas is equal to the callback gas limit set by the user.
	// NOTE: use the cached ctx for the EVM calls.
	res, err := k.evmKeeper.CallEVM(cachedCtx, erc20.ABI, receiverHex, tokenPair.GetERC20Contract(), true, remainingGas, "approve", contractAddr, amountInt.BigInt())
	if err != nil {
		return errorsmod.Wrapf(types.ErrAllowanceFailed, "failed to set allowance: %v", err)
	}

	// Consume the actual used gas on the original callback context.
	ctx.GasMeter().ConsumeGas(res.GasUsed, "callback allowance")
	remainingGas = remainingGas.Sub(remainingGas, math.NewIntFromUint64(res.GasUsed).BigInt())
	if ctx.GasMeter().IsOutOfGas() || remainingGas.Cmp(big.NewInt(0)) < 0 {
		return errorsmod.Wrapf(types.ErrOutOfGas, "out of gas")
	}

	var approveSuccess bool
	err = erc20.ABI.UnpackIntoInterface(&approveSuccess, "approve", res.Ret)
	if err != nil {
		return errorsmod.Wrapf(types.ErrAllowanceFailed, "failed to unpack approve return: %v", err)
	}

	if !approveSuccess {
		return errorsmod.Wrapf(types.ErrAllowanceFailed, "failed to set allowance")
	}
```
