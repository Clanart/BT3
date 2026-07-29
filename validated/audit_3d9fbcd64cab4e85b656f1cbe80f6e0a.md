This confirms the analog. The isolated-address balance check in `IBCReceivePacketCallback` uses a strict absolute-zero equality check that an unprivileged attacker can grief by pre-funding a deterministic, publicly-computable address.

### Title
Griefing of IBC Receive Callbacks via Strict Zero-Balance Check on Predictable Isolated Address - (File: x/ibc/callbacks/keeper/keeper.go)

### Summary
`IBCReceivePacketCallback` requires that, after invoking the destination contract's callback, the isolated receiver address holds **exactly zero** ERC20 tokens: `if receiverTokenBalance.Cmp(big.NewInt(0)) != 0 { return ... }` [1](#0-0) . This mirrors the reported `LoanVault` bug: a fixed/absolute equality check on a balance that a third party can manipulate by donating tokens directly to the checked address, causing a legitimate operation to permanently revert.

### Finding Description
The isolated address used for a given `(destChannel, sender)` pair is deterministically computed via `GenerateIsolatedAddress(channelID, sender)`, which simply hashes `Module("ibc-callbacks", channelID, sender)` [2](#0-1) . This value is fully public: anyone who observes a pending/pre-negotiated IBC transfer with a destination callback (or who simply knows the sender's Cosmos-side address and destination channel) can precompute the exact EVM hex address the isolated receiver will use, before the packet is even relayed.

In `IBCReceivePacketCallback`, the flow is:
1. Verify `receiver == isolatedAddr` [3](#0-2) .
2. `approve` the destination contract to pull `amountInt` tokens from `receiverHex` [4](#0-3) .
3. Call the destination contract, which is expected to `transferFrom(receiverHex, contract, amount)` for the *entire* current balance [5](#0-4) .
4. Assert the isolated address balance is exactly zero afterward [1](#0-0) .

An attacker can, prior to the relayed packet's execution, send an arbitrary nonzero amount of the same ERC20-represented token directly to the precomputed `isolatedAddrHex` (a normal, unprivileged ERC20 `transfer`). The approval set in step 2 only covers `amountInt` (the packet's own amount), so the contract's `transferFrom` call for `amountInt` succeeds but leaves the attacker-donated dust behind. Step 4's absolute-zero check then fails, and `IBCReceivePacketCallback` returns an error, which per the module's documented flow causes the `OnRecvPacket` state changes to be reverted and an error acknowledgement returned [6](#0-5) . Because the isolated address is a fixed, program-derived address (not a fresh contract created per-packet) and receives the tokens outside of the destination contract's control before the tx runs, this failure is deterministic and repeatable for every future packet routed to that same `(channel, sender)` pair.

### Impact Explanation
Every subsequent IBC receive-with-callback packet destined for that isolated address is permanently unable to complete: the callback keeps returning an error, the receive-callback flow reverts, and (per docs) an error acknowledgement is generated. Depending on how the surrounding transfer/refund flow handles the failure, this either strands the transferred value or forces packets for that account to never successfully invoke the destination contract logic, denying use of the receive-callback feature indefinitely with a one-time, permissionless dust transfer. This matches the "permanent freezing/locking of user funds or functionality via an unprivileged, irreversible strict-equality griefing vector" impact class, analogous to the original `LoanVault` liquidation/repay griefing.

### Likelihood Explanation
High. `GenerateIsolatedAddress` is a pure deterministic function of public data (module name, channel ID, sender address) [2](#0-1) , so no privileged access or race condition against the mempool is even required — the attacker can precompute the address at any time before the packet is relayed and send a trivial-cost token transfer to it. This requires no special permissions beyond holding/using the ERC20 token pair in question.

### Recommendation
Do not require the isolated address balance to be exactly zero. Instead:
- Track and compare the *delta* consumed by the callback (balance before minus balance after should be ≥ the packet's `amountInt`), similar to the delta-based invariant checks already used elsewhere in `x/erc20/keeper/msg_server.go` (`convertERC20IntoCoinsForNativeToken`, `ConvertCoinNativeERC20`) rather than an absolute value check [7](#0-6) ; or
- Sweep/track only the amount that was actually part of the packet (approve+pull exactly `amountInt`, and verify that specific amount left the isolated address) instead of asserting the address's total balance is zero.

### Proof of Concept
1. Attacker computes `isolatedAddr = GenerateIsolatedAddress(destChannelID, senderBech32)` for a victim's known sender address and destination channel (public IBC transfer parameters).
2. Attacker sends `1` unit of the token pair's ERC20 representation directly to `common.BytesToAddress(isolatedAddr.Bytes())` via a normal `transfer` call — no special access required.
3. Victim's IBC transfer with a `dest_callback` memo is relayed and processed by `OnRecvPacket` → `IBCReceivePacketCallback`.
4. The keeper approves and calls the destination contract for the packet's `amountInt`; the contract's `transferFrom` succeeds for that amount, but the attacker's extra `1` unit remains in `receiverHex`.
5. `receiverTokenBalance.Cmp(big.NewInt(0)) != 0` is true → the function returns `ErrEVMCall`, the receive callback fails, and the packet processing errors out [1](#0-0) .
6. Repeat step 2 for any future packet to the same isolated address to keep the DoS in effect indefinitely.

### Citations

**File:** x/ibc/callbacks/keeper/keeper.go (L152-155)
```go
	// Ensure receiver address is equal to the isolated address.
	if receiverHex.Cmp(isolatedAddrHex) != 0 {
		return errorsmod.Wrapf(types.ErrInvalidReceiverAddress, "expected %s, got %s", isolatedAddrHex.String(), receiverHex.String())
	}
```

**File:** x/ibc/callbacks/keeper/keeper.go (L192-212)
```go
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

**File:** x/ibc/callbacks/keeper/keeper.go (L214-224)
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
```

**File:** x/ibc/callbacks/keeper/keeper.go (L234-239)
```go
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

**File:** x/ibc/callbacks/README.md (L99-103)
```markdown
If an ICS-20 packet is not directed towards EVM callbacks, EVM callbacks doesn't do anything.
If an ICS-20 packet is directed towards EVM callbacks, and is formatted incorrectly, then EVM
callbacks returns an error and the recv packet application state changes are reverted and an
error acknowledgement is returned.

```

**File:** x/erc20/keeper/msg_server.go (L113-130)
```go
	// Check expected escrow balance after transfer execution
	// NOTE: coin fields already validated in the ValidateBasic() of the message
	coins := sdk.Coins{sdk.Coin{Denom: pair.Denom, Amount: msg.Amount}}
	tokens := coins[0].Amount.BigInt()
	balanceTokenAfter := k.BalanceOf(ctx, erc20, contract, types.ModuleAddress)
	if balanceTokenAfter == nil {
		return nil, sdkerrors.Wrap(types.ErrEVMCall, "failed to retrieve balance")
	}

	expToken := big.NewInt(0).Add(balanceToken, tokens)

	if r := balanceTokenAfter.Cmp(expToken); r != 0 {
		return nil, sdkerrors.Wrapf(
			types.ErrBalanceInvariance,
			"invalid token balance - expected: %v, actual: %v",
			expToken, balanceTokenAfter,
		)
	}
```
