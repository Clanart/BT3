### Title
IBC callback allowance flow permanently locks IBC-transferred funds when the underlying native ERC20 token pair contract does not return a boolean on `approve` - (File: `x/ibc/callbacks/keeper/keeper.go`)

### Summary
The external report flags raw `IERC20.transfer`/`approve` calls that decode a boolean return value and will revert on non-compliant tokens (e.g. USDT-style contracts with no return value), recommending `SafeERC20`. The Cosmos EVM codebase has the same anti-pattern in `x/ibc/callbacks/keeper.IBCReceivePacketCallback`, but unlike the analogous code in `x/erc20/keeper/msg_server.go` (`ConvertCoinNativeERC20`, `convertERC20IntoCoinsForNativeToken`), which explicitly falls back to checking Transfer-event logs when `res.Ret` is empty, the IBC callback's `approve` handling has no such fallback.

### Finding Description
`IBCReceivePacketCallback` ( [1](#0-0) ) receives IBC-transferred tokens into a deterministically generated "isolated address" (`GenerateIsolatedAddress`, no known private key) and then, purely at the protocol level (module acting as `receiverHex`), tries to `approve` the destination callback contract to pull the tokens out: [2](#0-1) 

```go
erc20 := contracts.ERC20MinterBurnerDecimalsContract
...
res, err := k.evmKeeper.CallEVM(cachedCtx, erc20.ABI, receiverHex, tokenPair.GetERC20Contract(), true, remainingGas, "approve", contractAddr, amountInt.BigInt())
...
var approveSuccess bool
err = erc20.ABI.UnpackIntoInterface(&approveSuccess, "approve", res.Ret)
if err != nil {
    return errorsmod.Wrapf(types.ErrAllowanceFailed, "failed to unpack approve return: %v", err)
}
if !approveSuccess {
    return errorsmod.Wrapf(types.ErrAllowanceFailed, "failed to set allowance")
}
```

`tokenPair.GetERC20Contract()` is the actual underlying ERC20 contract for a "native ERC20" token pair. Token pairs for native ERC20 contracts can be registered permissionlessly through `RegisterERC20` (`x/erc20/keeper/msg_server.go`, `RegisterERC20`, guarded only by `params.PermissionlessRegistration`) — any unprivileged user can register an arbitrary externally-deployed contract, including one that implements ERC20 semantics but (like USDT) returns no boolean from `approve`/`transfer`. The standard ABI (`ERC20MinterBurnerDecimalsContract.ABI`) is used only to *encode* the call selector, so the low-level EVM call to a non-compliant contract still succeeds and mutates state (approval is actually set on-chain), but returns zero bytes. `UnpackIntoInterface` on empty `res.Ret` then fails, and the function unconditionally returns `ErrAllowanceFailed` instead of falling back the way `x/erc20/keeper/msg_server.go` does with `validateTransferEventExists` ( [3](#0-2)  and [4](#0-3) ).

Because the callback aborts, the packet-received tokens (already credited into the isolated address `receiverHex` by the underlying ICS20 transfer before the callback runs) remain in that address. That address is a deterministic, keyless module-generated address (`GenerateIsolatedAddress`), so the only way its balance can ever move is through this exact `IBCReceivePacketCallback` code path acting as `receiverHex`. Since the `approve` step will deterministically fail for every packet destined to the same non-compliant token pair (the contract's behavior is fixed), the funds can never be swept out — they are permanently frozen.

### Impact Explanation
This matches the "Critical permanent freezing/locking of user funds ... token-pair-backed balances" impact class: any IBC transfer of a native-ERC20-backed token that is paired with a non-standard (no-bool-return) ERC20 contract and routed with a destination callback will have its received principal permanently stuck in an address nobody controls the key for, with no protocol-level recovery path once the approve step reverts.

### Likelihood Explanation
Reachable by an unprivileged actor: (1) permissionlessly register a native ERC20 token pair for a non-standard token (if `PermissionlessRegistration` is enabled, which is the common configuration for this module), or simply use/receive transfers for any existing token pair backed by such a contract; (2) send an ICS-20 transfer with a destination callback targeting that token pair. No privileged role or malicious relayer/validator is required — only ordinary IBC transfer with the packet-callbacks middleware, which is a standard supported user flow in this codebase (`x/ibc/callbacks`).

### Recommendation
Mirror the existing `x/erc20` pattern: after the `CallEVM("approve", ...)`, if `len(res.Ret) == 0`, don't immediately error — verify success via an `Approval` event in `res.Logs` (or otherwise tolerate a missing boolean return), only failing when there is concrete evidence the approval was not applied. Alternatively, adopt a `forceApprove`/`safeApprove`-equivalent semantic at the Go call-site so the callback flow does not assume all registered native ERC20 contracts are ABI-compliant with a boolean return value.

### Proof of Concept
1. Deploy a USDT-style ERC20 contract on the EVM side whose `approve(address,uint256)` performs the state change but returns no data (empty return, not `(bool)`).
2. Permissionlessly register this contract as a token pair via `MsgRegisterERC20` (assuming `PermissionlessRegistration` param is enabled), or use any already-registered contract with this behavior.
3. From a counterparty chain, send an ICS-20 transfer of this token to this chain with a `dest_callback` memo targeting a contract address on the EVM chain, with `data.Receiver` set to the isolated address computed by `GenerateIsolatedAddress(destChannel, sender)`.
4. Upon packet receipt, the underlying ICS20 transfer mints/unescrows the tokens into the isolated address account. `IBCReceivePacketCallback` then calls `approve` on the token contract; `res.Ret` is empty, `UnpackIntoInterface` errors, and the function returns `ErrAllowanceFailed`.
5. The callback contract never gets to pull the tokens via `transferFrom`; the tokens remain at the isolated address, which has no corresponding private key and is only ever driven by this callback code path — funds are permanently unrecoverable.

Note: I was not able to run the code or a full integration test in this session to empirically confirm `UnpackIntoInterface`'s exact failure behavior on zero-length `res.Ret`, nor to confirm whether `PermissionlessRegistration` is enabled by default in this chain's genesis parameters — this should be verified in a live/test environment before treating this as fully confirmed.

### Citations

**File:** x/ibc/callbacks/keeper/keeper.go (L104-164)
```go
func (k ContractKeeper) IBCReceivePacketCallback(
	ctx sdk.Context,
	packet ibcexported.PacketI,
	ack ibcexported.Acknowledgement,
	contractAddress string,
	version string,
) error {
	data, err := transfertypes.UnmarshalPacketData(packet.GetData(), version, "")
	if err != nil {
		return err
	}

	cbData, isCbPacket, err := callbacktypes.GetCallbackData(data, version, packet.GetDestPort(), ctx.GasMeter().GasRemaining(), ctx.GasMeter().GasRemaining(), callbacktypes.DestinationCallbackKey)
	if err != nil {
		return err
	}
	if !isCbPacket {
		return nil
	}

	// `ProcessCallback` in IBC-Go overrides the infinite gas meter with a basic gas meter,
	// so we need to generate a new infinite gas meter to run the EVM executions on.
	// Skipping this causes the EVM gas estimation function to deplete all Cosmos gas.
	// We re-add the actual EVM call gas used to the original context after the call is complete
	// with the gas retrieved from the EVM message result.
	cachedCtx, writeFn := ctx.CacheContext()
	cachedCtx = evmante.BuildEvmExecutionCtx(cachedCtx).
		WithGasMeter(evmtypes.NewInfiniteGasMeterWithLimit(cbData.CommitGasLimit))

	// receiver := sdk.MustAccAddressFromBech32(data.Receiver)
	receiver, err := sdk.AccAddressFromBech32(data.Receiver)
	if err != nil {
		return errorsmod.Wrapf(types.ErrInvalidReceiverAddress,
			"acc addr from bech32 conversion failed for receiver address: %s", data.Receiver)
	}
	receiverHex, err := utils.HexAddressFromBech32String(receiver.String())
	if err != nil {
		return errorsmod.Wrapf(types.ErrInvalidReceiverAddress,
			"hex address conversion failed for receiver address: %s", receiver)
	}

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
