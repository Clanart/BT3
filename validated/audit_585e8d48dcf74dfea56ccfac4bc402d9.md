## Finding

### Title
IBC callback approval flow permanently freezes ERC20 funds held by isolated addresses when the token pair wraps a USDT-style approval-race-protected token - (File: `x/ibc/callbacks/keeper/keeper.go`)

### Summary
`IBCReceivePacketCallback` sets an ERC20 allowance for the target contract by calling `approve(contractAddr, amountInt)` directly, without first zeroing the allowance, exactly the same pattern flagged in the SuperVault report for `leverageSwap`/`emptyVaultOperation`. Because `erc20` token pairs in this codebase can be backed by arbitrary externally-deployed ERC20 contracts (`OWNER_EXTERNAL` / `IsNativeERC20`), a token that implements USDT-style "no approve from non-zero to non-zero" protection will permanently break this flow for the deterministic, key-less isolated address that owns the funds.

### Finding Description
The callback handler resolves the ERC20 contract for the received token via `tokenPair.GetERC20Contract()` and unconditionally calls `approve` with the standard minter/burner ABI: [1](#0-0) 

`TokenPair` is not restricted to module-owned/minted tokens — it can represent a pre-existing external ERC20 contract: [2](#0-1) 

The allowance owner is the deterministically-generated isolated address (`GenerateIsolatedAddress(destChannel, sender)`), and the receiver check requires the packet's receiver to equal this isolated address: [3](#0-2) 

This isolated address is a hash-derived construct with no known private key — the only way its ERC20 balance can ever move is through this callback's `approve` + subsequent `CallEVMWithData` to the target contract: [4](#0-3) 

If the underlying token is a USDT-style token that reverts `approve()` when the current allowance is already non-zero, and the previous packet's callback contract execution did not fully consume the granted allowance (e.g., contract logic pulls less than the full approved amount, or performs a partial `transferFrom`), then the next call to `approve` for that same `(isolatedAddr, contractAddr)` pair will always revert. Since this is the only code path capable of authorizing spend of the isolated address's ERC20 balance, the balance becomes permanently unspendable — an unauthorized, irreversible freeze of user funds, matching the exact bug class from the source report (no possible remediation transaction exists, same as `leverageSwap`).

### Impact Explanation
This satisfies the Critical "permanent freezing... of user funds... token-pair-backed balances" impact: an ordinary IBC transfer with a destination callback, using a registered native/external ERC20 token pair, can leave real user value stranded at a receiver address that has no other spending mechanism. Unlike the `emptyVaultOperation` case in the original report, there is no "empty the balance and rerun" workaround here because the isolated address cannot sign transactions to interact with the token directly — the approve call inside the callback keeper is the sole authorization mechanism.

### Likelihood Explanation
Likelihood depends on: (1) a token pair being registered for an externally-owned ERC20 with approval-race protection (governance-controlled registration, but such tokens legitimately exist, e.g. USDT-style contracts), and (2) any prior callback invocation leaving a non-zero residual allowance (achievable by a callback-contract author, or the malicious/careless combination of contract + amount across two packets from the same sender/channel, both of which are unprivileged, ordinary user-triggerable IBC transfer flows). This is a plausible, unprivileged trigger path once such a token pair exists, though it requires the specific token behavior to be present.

### Recommendation
Before calling `approve` in `IBCReceivePacketCallback`, first drive the current allowance to zero (via `approve(contractAddr, 0)`) and only then approve the exact required amount, mirroring the recommended SuperVault mitigation. Alternatively, use `increaseAllowance`/`decreaseAllowance` semantics if the target contract supports them, or verify/reset allowance defensively so repeated callback executions never depend on a strictly monotonic non-zero-to-non-zero approve.

### Proof of Concept
1. Register a token pair with `ContractOwner = OWNER_EXTERNAL` for an ERC20 contract implementing USDT's approval-race protection (revert if `allowance(owner, spender) > 0 && amount > 0`).
2. Send an IBC transfer with a destination callback to a contract `C` that only partially consumes the granted allowance (e.g., calls `transferFrom` for less than the approved amount, or reverts after `approve` succeeds but is committed via `writeFn`).
3. `IBCReceivePacketCallback` runs, `approve(C, amount1)` succeeds, isolated address now has non-zero residual allowance to `C`.
4. Send a second IBC transfer to the same channel/sender (same isolated address) with a callback again targeting contract `C`.
5. `CallEVM(..., "approve", C, amount2)` reverts because the underlying token rejects approve-from-nonzero-to-nonzero; `IBCReceivePacketCallback` returns `ErrAllowanceFailed`.
6. All future callback executions targeting `(isolatedAddr, C)` fail identically; the ERC20 balance held by `isolatedAddr` for that token becomes permanently unspendable, since no other code path can move funds out of the key-less isolated address. [5](#0-4)

### Citations

**File:** x/ibc/callbacks/keeper/keeper.go (L145-156)
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

**File:** x/ibc/callbacks/keeper/keeper.go (L185-219)
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

```

**File:** x/erc20/types/token_pair.go (L61-69)
```go
// IsNativeCoin returns true if the owner of the ERC20 contract is the
// erc20 module account
func (tp TokenPair) IsNativeCoin() bool {
	return tp.ContractOwner == OWNER_MODULE
}

// IsNativeERC20 returns true if the owner of the ERC20 contract is an EOA.
func (tp TokenPair) IsNativeERC20() bool {
	return tp.ContractOwner == OWNER_EXTERNAL
```
