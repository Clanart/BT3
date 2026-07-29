## Finding

The exact same "non-standard ERC20 return value" bug class from the report exists in this codebase's IBC callback flow, and unlike the `x/erc20` conversion functions (which were hardened against it), this code path was not.

### Title
Unhandled non-boolean-returning `approve()` call permanently locks ICS20 destination-callback funds at the isolated receiver address - (File: `x/ibc/callbacks/keeper/keeper.go`)

### Summary
`IBCReceivePacketCallback` calls `approve()` on the registered "native ERC20" contract behind a token pair, then blindly ABI-decodes the return value as a `bool`, without the safety fallback used elsewhere in the codebase for tokens that don't return data on success (e.g. USDT-style tokens).

### Finding Description
In `x/ibc/callbacks/keeper/keeper.go`, when a destination callback is attached to an ICS20 transfer, the module attempts to auto-approve the target callback contract to spend the tokens that were just credited to a deterministic, non-EOA "isolated address": [1](#0-0) 

Note that `erc20.ABI` here is the module's own `ERC20MinterBurnerDecimalsContract` ABI (which declares `approve(address,uint256) returns (bool)`), but it is executed against `tokenPair.GetERC20Contract()` — an arbitrary, governance-registered ERC20 contract that may not conform to that exact ABI (analogous to USDT's `approve(address,uint) public;` with no return value). When such a contract is called, `res.Ret` will be empty, and: [2](#0-1) 
`erc20.ABI.UnpackIntoInterface(&approveSuccess, "approve", res.Ret)` fails on empty return data, causing `IBCReceivePacketCallback` to error out with `ErrAllowanceFailed`, and the subsequent `CallEVMWithData` step (which actually executes the destination callback's calldata) is never reached.

This is the same root cause as the M-13 report: relying on a strict `IERC20` interface/ABI decode that assumes all ERC20 tokens return a `bool` from `approve()`. The codebase's own `x/erc20/keeper/msg_server.go` demonstrates the fix was already known and applied elsewhere — `convertERC20IntoCoinsForNativeToken` and `ConvertCoinNativeERC20` explicitly check `len(res.Ret) == 0` and fall back to validating the event logs instead of unpacking a bool: [3](#0-2) [4](#0-3) 

The `approve()` call inside `IBCReceivePacketCallback` has no such guard.

The receiving address for these funds, `receiverHex`, is a deterministically-generated "isolated address" derived from the channel and sender — not a user-controlled EOA: [5](#0-4) 
Coins for this ICS20 transfer are already credited to this isolated address by the underlying IBC transfer logic before this callback logic runs; the intended mechanism to move those funds onward is exactly this approve+call sequence. If `approve()` reverts because the underlying "native ERC20" token pair contract has USDT-like semantics, the funds credited to the isolated address can never be forwarded through this path, since the isolated address has no private key and no other module-authorized spend path is exercised in this function.

### Impact Explanation
Any ICS20 transfer using a destination callback, denominated in a "native ERC20" token pair whose underlying contract does not return a `bool` from `approve()` (a legitimate, unprivileged, real-world ERC20 pattern — not a malicious or privileged setup), will have its callback-forwarding step permanently fail. Because the isolated receiver address is not a normal EOA and has no alternate withdrawal mechanism exercised in this code path, tokens routed this way become permanently stuck/inaccessible — matching the "permanent freezing, locking... of user funds" Critical impact.

### Likelihood Explanation
Trigger requires only an ordinary, unprivileged ICS20 transfer with a destination callback targeting a token pair backed by a non-standard (no-bool-return) ERC20 contract — a legitimate token characteristic, not an attacker capability that needs to be specially crafted; any user relying on IBC callbacks for such a token pair hits this deterministically on every packet.

### Recommendation
Apply the same fallback pattern already used in `x/erc20/keeper/msg_server.go`: check `len(res.Ret) == 0` after the `approve` `CallEVM` and, in that case, verify success via the `Approval` event log instead of unconditionally calling `erc20.ABI.UnpackIntoInterface(&approveSuccess, "approve", res.Ret)`.

### Proof of Concept
1. Register a "native ERC20" token pair (via `RegisterERC20`) for a contract whose `approve(address,uint256)` does not return a `bool` (mirrors USDT bytecode behavior — no output ABI encoding).
2. Perform an ICS20 transfer of that token to this chain with `memo` specifying a destination callback (`cbData`), targeting some `contractAddress`.
3. On packet receipt, `IBCReceivePacketCallback` runs; `k.evmKeeper.CallEVM(..., "approve", contractAddr, amountInt.BigInt())` returns `res.Ret` of length 0.
4. `erc20.ABI.UnpackIntoInterface(&approveSuccess, "approve", res.Ret)` errors (mismatched output types for empty data), function returns `ErrAllowanceFailed`.
5. The callback-forwarding `CallEVMWithData` step is never executed; the tokens already credited to the isolated address remain there with no path defined in this flow to move them out, since the isolated address is not backed by a private key.

**Note on uncertainty:** I was not able to fully trace how the ibc-callbacks middleware (upstream, outside this repo's indexed portion) treats an error returned from `IBCReceivePacketCallback` — specifically whether the packet's underlying transfer/mint to the isolated address is reverted together with the callback failure, or whether it is committed independently (as is typical for "best-effort" callback designs). This affects whether the locked-fund scenario is exactly as described or whether an ack-based rollback might mitigate it. I'd recommend a Devin session with full repo/dependency access (including `ibc-go`'s callback middleware source) to confirm the exact commit/rollback semantics before treating this as a fully confirmed Critical, though the ABI-decode root cause itself is clearly present and unguarded.

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

**File:** x/erc20/keeper/msg_server.go (L97-111)
```go
	// Check evm call response
	var unpackedRet types.ERC20BoolResponse
	if len(res.Ret) == 0 {
		// if the token does not return a value, check for the transfer event in logs
		if err := validateTransferEventExists(res.Logs, contract); err != nil {
			return nil, err
		}
	} else {
		if err := erc20.UnpackIntoInterface(&unpackedRet, "transfer", res.Ret); err != nil {
			return nil, err
		}
		if !unpackedRet.Value {
			return nil, sdkerrors.Wrap(errortypes.ErrLogic, "failed to execute transfer")
		}
	}
```

**File:** x/erc20/keeper/msg_server.go (L268-282)
```go
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
```
