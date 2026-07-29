## Analog Found: Stale ERC20 Approval in IBC Destination Callback Persists After Callback Failure

### Title
Unreset ERC20 approval to attacker-controlled destination-callback contract allows theft of IBC-received funds - (File: `x/ibc/callbacks/keeper/keeper.go`)

### Summary
`ContractKeeper.IBCReceivePacketCallback` mirrors the Connext `fulfill` bug pattern: it `approve()`s an arbitrary, attacker-supplied destination contract for the full transferred amount, invokes that contract with attacker-controlled calldata, and only *after* committing all resulting state via `writeFn()` does it check whether the callback actually consumed the allowance. If the destination contract does not fully drain the balance (whether maliciously or accidentally), the function returns an error — but the ERC20 `approve()` and any partial `transferFrom` executed by the callback contract have already been permanently committed to chain state, leaving the attacker's contract with a live, unconsumed allowance over the isolated receiver address.

### Finding Description [1](#0-0) 
The isolated receiver address is deterministic, derived only from `(destChannel, sender)` via `types.GenerateIsolatedAddress`, so an attacker who controls the packet `sender` field on the counterparty chain fully controls this address's identity and can trigger it repeatedly. [2](#0-1) 
The keeper approves `contractAddr` (an address chosen by the attacker in the packet memo's `dest_callback.address`) for the entire `amountInt` of the transferred token, on behalf of the isolated address, using a cached context. [3](#0-2) 
The keeper then executes the attacker-supplied calldata against `contractAddr` and — critically — calls `writeFn()` (committing the approval and any side effects of the arbitrary call to the real state) *before* checking whether the isolated address's balance was fully drained. Only after this commit does it check `receiverTokenBalance == 0` and return an error if funds remain. This ordering is the direct analog of the Connext bug: approval is granted before the external call, and there is no code path that resets/revokes the approval when the external call fails to consume it — instead, everything up to that point (including the stale allowance) has already been persisted via `writeFn()`.

The module's own documentation acknowledges the "stuck" invariant but does not account for the allowance escape hatch: [4](#0-3) 
The intended invariant is that funds left in the isolated address become permanently unrecoverable (nobody can sign for that address). The stale, uncleared approval breaks this invariant in the attacker's favor: while ordinary users can never move funds out of the isolated address, the attacker's own contract — holding the leftover allowance — can call `transferFrom(isolatedAddr, attacker, amount)` at will to extract the tokens that are supposedly "stuck."

### Impact Explanation
This is unauthorized extraction of user/escrowed value:
- The attacker's contract retains a valid ERC20 allowance over the isolated address after a failed/aborted callback.
- The attacker can call `transferFrom` on the token pair's ERC20 contract/precompile to drain the tokens that were routed to that isolated address by the IBC transfer, even though the receive callback formally returned an error acknowledgement.
- Because the isolated address is derived deterministically from `(channel, sender)` and the attacker controls `sender`, the attacker can also reuse the same isolated address across multiple packets, each time refreshing/exploiting the allowance, effectively creating a repeatable fund-extraction primitive against any tokens landing at that isolated address.
- This matches the "Critical … theft of user funds … unauthorized extraction of … IBC escrows … token-pair-backed balances" allowed-impact category.

### Likelihood Explanation
High. The attacker fully controls all inputs needed to trigger this: the packet `sender`, the `dest_callback.address` (the malicious contract), and the `calldata` executed against it. No relayer or validator privilege is required — the attacker only needs to originate (or have relayed) an ICS-20 transfer with a `dest_callback` memo pointing to their own contract that intentionally does not fully `transferFrom` the approved amount.

### Recommendation
- Move the final balance check (`receiverTokenBalance == 0`) to occur *before* `writeFn()` commits state, so a failing callback aborts the entire cached context (including the `approve`) rather than persisting partial state.
- Alternatively/additionally, explicitly revoke the allowance (`approve(contractAddr, 0)`) on the isolated address after the callback executes, regardless of success or failure, before any commit.
- Consider bounding the approval to being consumed atomically within the same cached-context flow so it can never outlive a single callback invocation.

### Proof of Concept
1. Attacker on the counterparty chain sends an ICS-20 transfer to `evmChainA` with:
   - `receiver` = the isolated address computed from `(destChannel, attackerSenderAddr)`.
   - `memo.dest_callback.address` = attacker's malicious contract `M`.
   - `calldata` for `M` that does nothing (or transfers less than the full amount).
2. `IBCReceivePacketCallback` runs: it calls `approve(M, amountInt)` on behalf of the isolated address (committed via `writeFn()`), then calls `M` with the attacker's calldata (`M` deliberately does not call `transferFrom` for the full amount).
3. The final check `receiverTokenBalance == 0` fails since `M` didn't drain the balance; the function returns an error and an error acknowledgement is produced — but the `approve(M, amountInt)` and any effects of the `M` call are already committed (writeFn() already executed).
4. Attacker calls `M.transferFrom(isolatedAddr, attacker, amountInt)` in a subsequent, ordinary transaction, using the still-valid allowance to drain the tokens that are nominally "stuck" in the isolated address, completing the theft.

### Citations

**File:** x/ibc/callbacks/keeper/keeper.go (L145-164)
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

	contractAddr := common.HexToAddress(contractAddress)

	// Check if the contract address contains code.
	// This check is required because if there is no code, the call will still pass on the EVM side,
	// but it will ignore the calldata and funds may get stuck.
	if !k.evmKeeper.IsContract(ctx, contractAddr) {
		return errorsmod.Wrapf(types.ErrContractHasNoCode, "provided contract address is not a contract: %s", contractAddr)
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

**File:** x/ibc/callbacks/keeper/keeper.go (L214-239)
```go
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

**File:** x/ibc/callbacks/README.md (L200-211)
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
can move the funds to a final desired address.
```
