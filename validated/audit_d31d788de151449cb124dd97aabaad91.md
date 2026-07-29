### Title
Unsafe ERC20 Approval in IBC Callback - (`x/ibc/callbacks/keeper/keeper.go`)

### Summary
The `x/ibc/callbacks` module contains a vulnerability where it performs an ERC20 `approve` call to a target contract without first resetting the allowance to zero. For non-standard ERC20 tokens like USDT, this causes the transaction to revert if a previous non-zero allowance exists, leading to a permanent denial of service (DoS) for IBC callbacks involving these tokens and potentially resulting in stuck funds in isolated addresses.

### Finding Description
In `x/ibc/callbacks/keeper/keeper.go`, the `IBCReceivePacketCallback` function is responsible for executing smart contract logic upon receiving an IBC packet. To allow the target contract to spend the received tokens, the keeper performs an EVM call to the `approve` method of the ERC20 contract associated with the token pair. [1](#0-0) 

The implementation directly calls `approve` with the `amountInt`:
```go
res, err := k.evmKeeper.CallEVM(cachedCtx, erc20.ABI, receiverHex, tokenPair.GetERC20Contract(), true, remainingGas, "approve", contractAddr, amountInt.BigInt())
```

Non-standard ERC20 tokens, most notably USDT on Ethereum and similar implementations, require the allowance to be set to zero before it can be updated to a new non-zero value. If a previous IBC callback failed or did not fully consume the allowance, a subsequent `approve` call will revert.

Furthermore, the code does not use a "safe approve" pattern. While it checks the boolean return value of the `approve` call, it does not handle tokens that do not return a boolean (which is another common non-standard behavior). [2](#0-1) 

### Impact Explanation
1.  **Permanent Denial of Service (DoS):** Any IBC packet directed to a contract using a non-standard token (like USDT) will revert if there is any residual allowance. Since the isolated address used for callbacks is deterministically generated, the state is persistent.
2.  **Stuck Funds:** The `IBCReceivePacketCallback` logic enforces that the receiver (the isolated address) must have a zero balance after the callback to prevent funds from being trapped. [3](#0-2) 
    If the `approve` call fails due to the non-zero allowance issue, the tokens remain in the isolated address. While the transaction reverts, the persistent nature of the non-zero allowance prevents future successful callbacks for that specific channel/sender pair, effectively locking the path for those assets.

### Likelihood Explanation
The likelihood is high for any chain integrating with external ERC20 tokens via IBC (e.g., through a gravity bridge or similar) where tokens like USDT are prevalent. The issue is triggered whenever a contract callback does not consume the full approved amount, which is a common occurrence in complex DeFi integrations.

### Recommendation
Use the `safeApprove` pattern or explicitly set the allowance to zero before setting the new allowance:
1.  Call `approve(spender, 0)`.
2.  Call `approve(spender, amount)`.

Alternatively, use `safeIncreaseAllowance` if the token supports it, or wrap the `CallEVM` in a utility that handles non-standard ERC20 return values and the zero-allowance requirement.

### Proof of Concept
1.  A user sends an IBC packet with a non-standard token (e.g., USDT representation) to a contract on the destination chain.
2.  The `IBCReceivePacketCallback` is triggered. It calls `approve(contractAddr, amount)`.
3.  The contract callback executes but only spends `amount - 1` tokens (e.g., due to rounding or partial fills).
4.  The callback fails the balance check at line 236 because `1` token remains. The transaction reverts, but the `approve` was successful in the EVM state (if not using `cachedCtx` correctly or if multiple interactions occur).
5.  A second IBC packet is sent. `IBCReceivePacketCallback` calls `approve(contractAddr, newAmount)`.
6.  The ERC20 contract (USDT-style) sees the existing allowance of `1` and reverts the `approve` call.
7.  The IBC callback is now permanently broken for this path until the allowance is manually cleared (which is difficult for an isolated address).

### Citations

**File:** x/ibc/callbacks/keeper/keeper.go (L192-192)
```go
	res, err := k.evmKeeper.CallEVM(cachedCtx, erc20.ABI, receiverHex, tokenPair.GetERC20Contract(), true, remainingGas, "approve", contractAddr, amountInt.BigInt())
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

**File:** x/ibc/callbacks/keeper/keeper.go (L234-239)
```go
	receiverTokenBalance := k.erc20Keeper.BalanceOf(ctx, erc20.ABI, tokenPair.GetERC20Contract(), receiverHex) // here,
	// we can use the original ctx and skip manually adding the gas
	if receiverTokenBalance.Cmp(big.NewInt(0)) != 0 {
		return errorsmod.Wrapf(erc20types.ErrEVMCall,
			"receiver has %d unrecoverable tokens after callback", receiverTokenBalance)
	}
```
