## Analog Identified: Permanently Stuck Funds in IBC Callback Isolated Address

### Title
Tokens Delivered via ICS-20 Destination Callback Become Permanently Unrecoverable if the Target Contract Callback Fails to Fully Pull the Approved Amount - (File: x/ibc/callbacks/keeper/keeper.go)

### Summary
The external report describes bundle NFTs becoming permanently locked in `ImmutableBundle` when transferred via the wrong function, with no admin rescue path covering that specific token class. The Cosmos EVM analog is the `IBCReceivePacketCallback` flow in `x/ibc/callbacks/keeper/keeper.go`, where tokens received via an ICS-20 transfer with destination-callback memo are delivered to a deterministic "isolated address" that has no private key and can never sign a transaction. If the destination contract's callback does not fully `transferFrom` the approved amount, the tokens permanently remain at that address with no rescue mechanism, no owner, and no way for the original sender to reclaim them.

### Finding Description
`IBCReceivePacketCallback` computes a receiver address deterministically via [1](#0-0) , and this address is exactly `types.GenerateIsolatedAddress`, which is a module-derived hash address with no associated private key: [2](#0-1) .

Once the IBC transfer module delivers/mints the token to that isolated receiver (this happens in the underlying transfer/ERC20 `OnRecvPacket` path prior to the callback being invoked), the callback keeper `approve`s the destination contract to pull the tokens via `transferFrom`, then calls the contract with its calldata: [3](#0-2) .

Critically, `writeFn()` — which commits the cached EVM/bank state changes (the `approve` call and the contract's callback execution) back into the real `ctx` — is invoked *before* the code verifies that the isolated address balance is fully drained: [4](#0-3) . The code comment itself acknowledges the exact risk class from the external report: *"This check is here to prevent funds from getting stuck in the isolated address, since they would become irretrievable."*

However, per the IBC-Go callbacks/ADR-8 design (which this middleware implements), callback execution errors are treated as best-effort side effects and do not cause the surrounding ICS-20 `OnRecvPacket` acknowledgement to fail or roll back the underlying token transfer. That means:
1. The transfer module still mints/unescrows the token to the isolated address (this state change is independent of and precedes the callback keeper logic).
2. If the target contract's callback does not call `transferFrom` for the *entire* approved amount (partial pull, buggy/malicious contract, revert-then-catch, etc.), `IBCReceivePacketCallback` returns `ErrEVMCall`, but this error is only used for logging/telemetry at the callback middleware level — it does not unwind the token delivery to the isolated address.
3. The isolated address has no private key (it is a module-derived hash), so the residual balance can never be spent, transferred, or converted by anyone — not the original sender, not the destination contract, not an admin.

This mirrors the `ImmutableBundle` bug precisely: funds that land at an address via a legitimate-looking flow become permanently locked because the code that would let them be moved (the `approve`+`transferFrom` pattern relying on the contract's cooperation) is the *only* path out, and there is no fallback rescue for partial failures.

### Impact Explanation
This results in permanent freezing/locking of user funds with no possible recovery — the isolated address is a hash with no signing key, so any residual ERC20/token-pair balance left there after a partial or failed contract callback is permanently inaccessible. This satisfies the "Critical permanent freezing, locking, theft, or unauthorized extraction of user funds" impact category, since an ordinary/unprivileged relayer-forwarded IBC packet combined with any destination contract that does not exactly drain its full `approve`d allowance (a very easy, non-malicious condition to hit — partial fills, contract bugs, contracts that intentionally leave a remainder, or contracts under gas pressure) causes irreversible fund loss for the depositor.

### Likelihood Explanation
Likelihood is high in ordinary usage: destination-callback ICS-20 transfers rely entirely on the destination contract correctly calling `transferFrom` for the exact full amount. Any contract that reverts internally after partial work, has a bug, intentionally takes a fee and leaves a remainder, or is called with insufficient gas (bounded by `cbData.CommitGasLimit`) will leave a nonzero residual balance in an address nobody can ever control. No malicious actor is even required — an honest but imperfect destination contract triggers this deterministically, and no privileged operator has been assumed.

### Recommendation
Do not commit (`writeFn()`) the cached EVM state before verifying that the isolated address balance is fully drained; verify first, then commit only on success, and revert/refund (e.g., convert any leftover token-pair balance in the isolated address back to a coin and forward it to a genuinely recoverable address, such as the original packet sender or receiver) on failure instead of merely erroring after the point of no return. Alternatively, add a mechanism that periodically or lazily sweeps any residual balance from a given isolated address back to the receiver address once it's clear the callback interaction is complete, since the mapping isolated-address → intended receiver is deterministic and known.

### Proof of Concept
1. Deploy a destination contract that implements a callback function but only calls `IERC20(token).transferFrom(isolatedAddr, address(this), amount/2)` (pulling only half of the approved allowance), and does not revert.
2. Perform an ICS-20 transfer from chain B to a Cosmos EVM chain, with `memo` specifying a `dest_callback` pointing to this contract, for a token registered in the ERC20 `TokenPair` (native ERC20 or native coin representation).
3. `OnRecvPacket`/transfer middleware credits the isolated address (`GenerateIsolatedAddress(destChannel, sender)`) with the full amount.
4. `IBCReceivePacketCallback` approves the contract for the full amount and invokes its calldata; the contract pulls only half.
5. `writeFn()` has already committed the approve + partial pull to `ctx`.
6. The subsequent balance check finds `receiverTokenBalance != 0` and returns `ErrEVMCall`, but per callback middleware semantics this does not cause the packet ack or the already-committed token delivery to be rolled back.
7. The remaining half of the tokens are now permanently stuck at `isolatedAddr`, an address with no private key, with no way for the sender, the contract, or any admin to retrieve them.

### Citations

**File:** x/ibc/callbacks/keeper/keeper.go (L145-147)
```go
	// Generate secure isolated address from sender.
	isolatedAddr := types.GenerateIsolatedAddress(packet.GetDestChannel(), data.Sender)
	isolatedAddrHex := common.BytesToAddress(isolatedAddr.Bytes())
```

**File:** x/ibc/callbacks/keeper/keeper.go (L185-224)
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

	// NOTE: use the cached ctx for the EVM calls.
	res, err = k.evmKeeper.CallEVMWithData(cachedCtx, receiverHex, &contractAddr, cbData.Calldata, true, remainingGas)
	if err != nil {
		return errorsmod.Wrapf(types.ErrEVMCallFailed, "EVM returned error: %s", err.Error())
	}

	// Consume the actual gas used on the original callback context.
	ctx.GasMeter().ConsumeGas(res.GasUsed, "callback function")
	if ctx.GasMeter().IsOutOfGas() {
		return errorsmod.Wrapf(types.ErrOutOfGas, "out of gas")
	}
```

**File:** x/ibc/callbacks/keeper/keeper.go (L226-239)
```go
	// Write cachedCtx events back to ctx.
	writeFn()

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

**File:** x/ibc/callbacks/types/keys.go (L13-17)
```go
// GenerateIsolatedAddress generates an isolated address for the given channel ID and sender address.
// This provides a safe address to call the receiver contract address with custom calldata
func GenerateIsolatedAddress(channelID string, sender string) sdk.AccAddress {
	return sdk.AccAddress(address.Module(ModuleName, []byte(channelID), []byte(sender))[:20])
}
```
