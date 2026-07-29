### Title
IBC ICS20 receive callback permanently locks user funds when the token-paired ERC20's `approve()` returns no value - (File: x/ibc/callbacks/keeper/keeper.go)

### Summary
`IBCReceivePacketCallback` unconditionally ABI-decodes the return value of an `approve()` call made against an arbitrary, governance/permissionlessly-registered ERC20 contract. Unlike the sibling conversion functions in the same codebase, it has no fallback path for ERC20s that don't return a boolean from `approve` (the exact bug class described in the external report). When decoding fails, the function aborts before the tokens can be moved out of a deterministically-generated "isolated address" that has no private key, permanently trapping the received IBC funds.

### Finding Description
`IBCReceivePacketCallback` in [1](#0-0)  performs:

1. `CallEVM(..., "approve", contractAddr, amountInt.BigInt())` against `tokenPair.GetERC20Contract()`, using the ABI of `contracts.ERC20MinterBurnerDecimalsContract` to encode the call and decode the result.
2. Immediately calls `erc20.ABI.UnpackIntoInterface(&approveSuccess, "approve", res.Ret)` with no check on `len(res.Ret)`. [2](#0-1) 

This is the exact same assumption flagged in the external report: that `approve()` always returns an ABI-encoded `bool`. The codebase itself demonstrates the correct defensive pattern elsewhere — `ConvertERC20`/`ConvertCoinNativeERC20` explicitly special-case `len(res.Ret) == 0` and fall back to checking for a `Transfer` event in the logs instead of blindly unpacking: [3](#0-2) 

That guard is missing in the IBC callback path. `tokenPair.GetERC20Contract()` is not guaranteed to be the module's own `ERC20MinterBurnerDecimalsContract` bytecode — "native ERC20" token pairs can point at any already-deployed ERC20 contract registered via governance or, per the integration tests, permissionless registration (`s.network.App.GetErc20Keeper().SetPermissionlessRegistration`), so a non-standard token (USDT-style, missing return data on `approve`) is a realistic on-chain target.

When `UnpackIntoInterface` errors, the function returns immediately (line 207), so `writeFn()` — which commits the cached EVM context — is never called, and the whole callback aborts. The ICS20 token amount, however, was already unescrowed/minted to the "isolated address" (`receiverHex`) by the underlying transfer application *before* this callback runs; that state change is on `ctx`, not the aborted `cachedCtx`, so it is not rolled back.

The isolated address is a value deterministically derived from `packet.GetDestChannel()` and `data.Sender` via `types.GenerateIsolatedAddress`  — it is not a normal externally-owned account with an accessible private key on this chain; the code's own comment confirms the intended invariant: [4](#0-3) 

"...since they would become irretrievable" — i.e., the only sanctioned way to move funds out of the isolated address is this same approve+transferFrom callback flow. If that flow can never succeed (because the underlying token never returns a decodable `approve` result), the funds are permanently and irrecoverably stuck.

### Impact Explanation
This matches the Critical "permanent freezing/locking/unauthorized extraction of user funds" impact category. Any IBC transfer that uses a destination callback and targets a token pair backed by a non-standard-return-value ERC20 will result in the transferred value being irrecoverably locked in an address nobody controls — a total, unrecoverable loss of the transferred funds for that transfer, triggerable repeatedly for every such transfer.

### Likelihood Explanation
The trigger requires only an ordinary, unprivileged action: sending an ICS20 transfer with a destination callback for a token pair whose underlying ERC20 contract does not return a bool from `approve` (a known, common real-world ERC20 pattern, e.g., USDT-style tokens). Since native ERC20 token pairs can reference pre-existing, arbitrary bytecode (including via permissionless registration in some configurations), an attacker does not need any privileged role to set this condition up, and a victim does not need to do anything unusual — simply using IBC callbacks with such a token pair is sufficient to lose funds.

### Recommendation
Mirror the defensive pattern already used in `x/erc20/keeper/msg_server.go`: after the `approve` `CallEVM`, check `len(res.Ret) == 0` and, in that case, treat the call as successful only if a corresponding `Approval` event is present in the logs (or otherwise validate success without requiring ABI-decodable return data), instead of unconditionally calling `UnpackIntoInterface`. Additionally, consider providing a permissionless/governance recovery path (e.g., allow directly retrying the callback or sweeping the isolated address) so that funds are not unconditionally unrecoverable if the callback step fails for any reason.

### Proof of Concept
1. Register (via governance, or permissionless registration if enabled) a "native ERC20" token pair whose ERC20 contract implements `approve(address,uint256)` without returning a `bool` (e.g., a USDT-style contract, analogous to the `MockUSDT` PoC in the source report).
2. Send an ICS20 transfer of this token to the chain with IBC destination-callback data pointing to any valid contract address.
3. The transfer app unescrows/mints the token amount to the deterministic isolated address (`receiverHex`).
4. `IBCReceivePacketCallback` calls `CallEVM(..., "approve", ...)` against the token; the on-chain call succeeds but returns no data.
5. `erc20.ABI.UnpackIntoInterface(&approveSuccess, "approve", res.Ret)` fails because `res.Ret` is empty, returning `ErrAllowanceFailed` and aborting before `writeFn()` is invoked.
6. The token balance now sits at the isolated address; since this address has no corresponding private key and the only intended mechanism to move it (approve+transferFrom via this same callback) can never succeed for this token, the funds are permanently locked — matching the balance check comment in the code that explicitly anticipates this as "irretrievable."

Note: I was not able to fully inspect `x/ibc/callbacks/types/keys.go` (`GenerateIsolatedAddress`) or conclusively confirm ibc-go v10 callback-middleware semantics regarding whether a callback error can still influence the packet acknowledgement in every configuration; these would benefit from direct verification in a running node/test environment via a Devin session before finalizing severity.

### Citations

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
