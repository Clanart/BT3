Confirmed: `GenerateIsolatedAddress` is purely deterministic based on `channelID` and `sender` string [1](#0-0) , meaning the same source-chain sender always maps to the same isolated address for a given destination channel, regardless of amount, sequence, or contract used.

### Title
Premature state commit in IBC receive-packet callback allows a persistent ERC20 allowance to be granted to an attacker-controlled contract, enabling later theft of funds sent to the isolated address - (File: `x/ibc/callbacks/keeper/keeper.go`)

### Summary
`IBCReceivePacketCallback` grants an ERC20 `approve()` allowance from a deterministic "isolated address" to an attacker-supplied `contractAddress`, then executes attacker-supplied calldata, and only afterward checks that the isolated address's token balance is zero to ensure the contract fully swept the funds. However, `writeFn()` (which commits the cached, isolated state changes back to the real context) is called *before* this balance-zero invariant check, so the approval and any partial execution are persisted even when the invariant is violated and the function returns an error.

### Finding Description
The callback flow is:
1. `approve(contractAddr, amountInt)` is executed against the ERC20 representation contract on behalf of `receiverHex` (the deterministic isolated address) [2](#0-1) .
2. Attacker-controlled `cbData.Calldata` is then executed against `contractAddr` [3](#0-2) .
3. `writeFn()` commits all of the above cached-context changes into the real `ctx` [4](#0-3) .
4. Only *after* the commit does the code check that the isolated address's ERC20 balance is zero, returning an error otherwise, with a comment stating this check exists specifically "to prevent funds from getting stuck in the isolated address" [5](#0-4) .

Because `writeFn()` runs before the balance check, the intended safety invariant is enforced too late: by the time it fires, the `approve()` call and the calldata execution are already permanently committed to chain state. An attacker who controls both the IBC transfer `sender` (hence the deterministic isolated address via `GenerateIsolatedAddress`) and the destination `contractAddress`/calldata can:
1. Send an ICS20 transfer with a destination callback to their own contract, with calldata that does not fully drain the approved amount (e.g., a no-op, or one that consumes less than the full approved allowance).
2. The `approve(contractAddr, amountInt)` allowance is committed via `writeFn()` regardless of the later failure.
3. The function then detects the nonzero balance and returns an error — but this error occurs after the state was already written, so the allowance persists on-chain.
4. Because the isolated address is deterministic per `(destChannel, sender)` and independent of amount/sequence, the attacker can later cause additional funds of the same denomination to arrive at the very same isolated address (via a further transfer to that same receiver, which is an ordinary bech32 account).
5. The attacker's contract, still holding the un-revoked `approve()` allowance, can call `transferFrom(isolatedAddr, attacker, amount)` on the token/ERC20 precompile at any later time to drain those newly arrived funds.

The permanence of the allowance is the direct native analog to the "missing `approve(0)`" bug class from the external report: the code sets an allowance without any subsequent explicit revocation/reset, and the commit ordering here defeats the one guardrail (`writeFn` ordering vs. the balance check) that was apparently intended to prevent stranded/exploitable allowances.

### Impact Explanation
This allows unprivileged, unauthorized extraction of user funds from a Cosmos-EVM native account (the isolated address) at an arbitrary future time, via a standing ERC20 allowance that was never meant to survive beyond the single callback execution. This matches the Critical impact category of "permanent freezing, locking, theft, or unauthorized extraction of user funds ... via ordinary transaction ... flow."

### Likelihood Explanation
The trigger is fully within reach of an unprivileged user: they need only control the source-chain IBC sender address and specify a destination-callback contract/calldata of their choosing (both attacker-controlled inputs in a standard ICS20 transfer with callback memo). No relayer or validator collusion, and no privileged keys, are required.

### Recommendation
- Move the "receiver token balance must be zero" check (and any other invariant checks) to occur strictly *before* calling `writeFn()`, so that if the invariant fails, none of the `approve()`/calldata-execution side effects are committed to the real state.
- Additionally, explicitly reset the allowance to zero (`approve(contractAddr, 0)`) after calldata execution regardless of success/failure, so no residual allowance can ever persist past a single callback invocation.

### Proof of Concept
Conceptual PoC (cannot be executed without a running test harness, but derivable directly from the code path):
1. Attacker sets up an ICS20 transfer from `senderX` to `destChannel` with a destination callback pointing to `MaliciousContract` and calldata that is a no-op (does not call `transferFrom`).
2. `IBCReceivePacketCallback` runs: `approve(MaliciousContract, amountInt)` executes against the isolated address `GenerateIsolatedAddress(destChannel, senderX)` [6](#0-5) , calldata executes without draining the balance, `writeFn()` commits both, then the balance check fails and the function returns an error [7](#0-6) .
3. Despite the function returning an error, the `approve()` allowance is now live in state for `MaliciousContract` over the isolated address.
4. Attacker (or anyone) later sends more of the same denom to that same isolated address (a normal Cosmos account, reachable by a plain bank/IBC transfer).
5. Attacker calls `MaliciousContract`, which invokes `transferFrom(isolatedAddr, attacker, amount)` on the ERC20 precompile/contract, draining the newly received funds using the still-valid, never-revoked allowance.

Note: I could not execute this against a live devnet/testnet to confirm the exact ack/error-handling behavior of the surrounding IBC-Go v10 callbacks middleware when `IBCReceivePacketCallback` returns a non-nil error (i.e., whether it also reverts the underlying transfer or only affects callback bookkeeping); this affects the precise packet-flow framing but does not change the core finding that `writeFn()` unconditionally commits the `approve()` grant before the invariant meant to catch this scenario is evaluated.

### Citations

**File:** x/ibc/callbacks/types/keys.go (L13-17)
```go
// GenerateIsolatedAddress generates an isolated address for the given channel ID and sender address.
// This provides a safe address to call the receiver contract address with custom calldata
func GenerateIsolatedAddress(channelID string, sender string) sdk.AccAddress {
	return sdk.AccAddress(address.Module(ModuleName, []byte(channelID), []byte(sender))[:20])
}
```

**File:** x/ibc/callbacks/keeper/keeper.go (L145-147)
```go
	// Generate secure isolated address from sender.
	isolatedAddr := types.GenerateIsolatedAddress(packet.GetDestChannel(), data.Sender)
	isolatedAddrHex := common.BytesToAddress(isolatedAddr.Bytes())
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

**File:** x/ibc/callbacks/keeper/keeper.go (L214-218)
```go
	// NOTE: use the cached ctx for the EVM calls.
	res, err = k.evmKeeper.CallEVMWithData(cachedCtx, receiverHex, &contractAddr, cbData.Calldata, true, remainingGas)
	if err != nil {
		return errorsmod.Wrapf(types.ErrEVMCallFailed, "EVM returned error: %s", err.Error())
	}
```

**File:** x/ibc/callbacks/keeper/keeper.go (L220-239)
```go
	// Consume the actual gas used on the original callback context.
	ctx.GasMeter().ConsumeGas(res.GasUsed, "callback function")
	if ctx.GasMeter().IsOutOfGas() {
		return errorsmod.Wrapf(types.ErrOutOfGas, "out of gas")
	}

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
