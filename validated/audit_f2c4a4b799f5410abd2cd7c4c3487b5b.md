### Title
Native ERC20 token-pairs without a standards-compliant `approve()` return value permanently freeze IBC-callback funds in an unrecoverable isolated address - (File: x/ibc/callbacks/keeper/keeper.go)

### Summary
`IBCReceivePacketCallback` hard-codes the `ERC20MinterBurnerDecimalsContract` ABI (which declares `approve()` returning `bool`) to grant the destination contract an allowance over tokens held by a synthetic "isolated address," then strictly unpacks the return data as a boolean. If the token pair backing the transferred denom is a *native* ERC20 (an externally deployed, non-standard contract such as a USDT-style token that does not return a `bool` from `approve()`), the `UnpackIntoInterface` call fails and the entire callback errors out — after the ICS-20 transfer has already credited the isolated address.

### Finding Description
`k.evmKeeper.CallEVM(cachedCtx, erc20.ABI, receiverHex, tokenPair.GetERC20Contract(), true, remainingGas, "approve", contractAddr, amountInt.BigInt())` is invoked against `tokenPair.GetERC20Contract()`, which for `IsNativeERC20()` pairs is an arbitrary, externally-deployed ERC20 contract [1](#0-0) . The result is unconditionally unpacked using the fixed ABI's boolean-return `approve` signature: [2](#0-1) 

This is exactly the bug class from the external report — code assumes ERC20 `approve()` always returns a `bool`, which is false for USDT-style tokens. Here, however, the token used is not a plugin/AMO input chosen by an integrator; it is whatever native ERC20 was registered as a token pair for the IBC-transferred denom, meaning the failure is triggered purely by an unprivileged IBC packet relay/receive for that denom, with no attacker privilege beyond sending an IBC transfer with a destination callback memo.

Critically, by the time this `approve` call executes, the underlying ICS-20 transfer has already completed and credited tokens to a synthetic "isolated address" (`GenerateIsolatedAddress`), which the code's own comments confirm is not a real, key-controlled account: [3](#0-2) 

If the `approve` unpack fails, `IBCReceivePacketCallback` returns an error before ever calling the destination contract's `transferFrom`, so the tokens that were transferred into the isolated address are never moved into the target contract. Per ibc-go's callback middleware semantics, an error in the receive-side callback does not roll back the underlying transfer packet's execution/acknowledgement — the transfer itself is not reverted, only the auxiliary callback fails. Since the isolated address has no corresponding private key (it is a deterministic hash-derived address used only as a routing target, per `GenerateIsolatedAddress`), any funds already deposited there become permanently unrecoverable, which is exactly what the code comment above warns about.

### Impact Explanation
This causes permanent, irreversible freezing/locking of user funds transferred via an ICS-20 packet with a destination EVM callback whenever the target denom's token pair is backed by a native ERC20 whose `approve()` does not return a `bool` (USDT-style semantics). This matches the "Critical permanent freezing, locking ... of user funds ... token-pair-backed balances" allowed impact for this repository.

### Likelihood Explanation
Triggering requires only: (1) a token pair registered for a non-standard-return ERC20 (native ERC20 registration is a normal, not privileged, pathway in `x/erc20`), and (2) any user sending a normal ICS-20 transfer with a callback memo targeting that denom. No relayer or validator collusion, no governance action, and no privileged key are needed — an ordinary user's own transfer with a callback memo triggers the failure and loses their own funds to the isolated address.

### Recommendation
- Do not statically assume the boolean-return `approve` ABI for `tokenPair.GetERC20Contract()`; instead, mirror the fallback pattern already used in `convertERC20IntoCoinsForNativeToken` (checking for empty return data / matching event logs) when handling the `approve` result, or use a low-level call and treat empty/success execution (no revert) as sufficient for non-standard tokens.
- Alternatively, perform the allowance step through a safe-approve style call that tolerates missing/incorrectly-typed return data, only surfacing an error on an actual EVM revert.
- Ensure that if a callback contract-side action fails for a native-ERC20 token pair, the escrowed isolated-address balance can be swept/reclaimed by the original sender rather than left permanently stranded.

### Proof of Concept
1. Register a native ERC20 token pair for a token whose `approve(address,uint256)` function has no return value (e.g., mirrors USDT's ABI) via the standard `x/erc20` native ERC20 registration flow.
2. From a counterparty chain, send an ICS-20 transfer of that denom to the Cosmos EVM chain with a destination-callback memo pointing to any valid deployed contract address.
3. The transfer completes normally, crediting the deterministic isolated address derived from `GenerateIsolatedAddress(destChannel, sender)`.
4. `IBCReceivePacketCallback` calls `approve` on the token contract; because the contract's `approve` does not return ABI-encoded `bool` data, `erc20.ABI.UnpackIntoInterface(&approveSuccess, "approve", res.Ret)` fails, and the function returns `types.ErrAllowanceFailed` before any `transferFrom` occurs.
5. Because ibc-go callback errors on `onRecvPacket` do not roll back the already-executed transfer, the tokens remain permanently held at the isolated address, which has no corresponding controllable private key, resulting in irrecoverable loss of the transferred funds.

### Citations

**File:** x/ibc/callbacks/keeper/keeper.go (L185-195)
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
```

**File:** x/ibc/callbacks/keeper/keeper.go (L204-212)
```go
	var approveSuccess bool
	err = erc20.ABI.UnpackIntoInterface(&approveSuccess, "approve", res.Ret)
	if err != nil {
		return errorsmod.Wrapf(types.ErrAllowanceFailed, "failed to unpack approve return: %v", err)
	}

	if !approveSuccess {
		return errorsmod.Wrapf(types.ErrAllowanceFailed, "failed to set allowance")
	}
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
