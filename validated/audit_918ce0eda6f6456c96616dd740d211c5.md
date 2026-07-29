Confirmed: `GenerateIsolatedAddress` derives a module-based hash address with no associated private key [1](#0-0) , meaning any tokens left at that address after the callback are permanently uncontrollable by any signer.

### Title
Permanent Loss of IBC Callback Tokens Due to Post-Write Balance Check in `IBCReceivePacketCallback` - (File: x/ibc/callbacks/keeper/keeper.go)

### Summary
`IBCReceivePacketCallback` approves and forwards IBC-received ERC20-represented tokens from a deterministic, keyless "isolated address" to a user-specified contract, then commits the cached execution context via `writeFn()` **before** verifying that the isolated address's token balance is fully drained. If the target contract does not consume the full approved allowance (benign partial-transfer logic, a bug, or a malicious/careless contract), the leftover balance is permanently stuck at an address that has no private key and no withdraw path.

### Finding Description
In `IBCReceivePacketCallback`, the flow is:
1. `approve(contractAddr, amount)` is called from the isolated receiver address, and `onPacketReceive`-style calldata is executed against the target contract, both inside `cachedCtx`.
2. `writeFn()` commits all of these state changes onto the real `ctx`.
3. Only afterward does the code check `receiverTokenBalance := k.erc20Keeper.BalanceOf(ctx, ..., receiverHex)` and return an error if it's non-zero. [2](#0-1) 

Because `writeFn()` runs before the balance check, the check cannot undo anything — it is purely diagnostic. If the target contract's calldata does not `transferFrom` the full approved amount (e.g. it only pulls part of the funds, or ignores the call entirely due to a logic branch), the remainder permanently remains on `receiverHex`, the isolated address computed by `GenerateIsolatedAddress` [1](#0-0) . This address is derived via `address.Module(...)` — a pure hash with no corresponding ECDSA key — so no one can ever sign a transaction from it to move the stuck tokens. There is no keeper-level admin/withdraw function elsewhere in `x/ibc/callbacks` or `x/erc20` that can sweep balances out of an arbitrary EVM/isolated address; the module only ever mints/approves/transfers through user or contract-initiated flows.

This is directly analogous to the reported bug class: privileged/administrative logic changes leave old value (here, the leftover ERC20 balance at an abandoned/isolated holder) with no recovery mechanism, because the withdraw/sweep capability doesn't exist for this specific holder pattern.

### Impact Explanation
Any unprivileged user who initiates an ICS-20 transfer with a destination callback to a contract that doesn't fully drain the approved allowance causes permanent, irrecoverable loss of the leftover token value at the isolated address. Since the isolated address is keyless by construction, this is not a temporary lock — it is permanent freezing/loss of user funds, matching the Critical impact bucket for "permanent freezing, locking, theft, or unauthorized extraction of user funds ... token-pair-backed balances."

### Likelihood Explanation
Trigger requires only an ordinary, permissionless IBC transfer with callback data pointing at a contract that (by bug, partial-amount business logic, or malice) does not pull the entire approved amount via `transferFrom`. No privileged access, validator collusion, or governance action is needed — the sender fully controls `contractAddress`, `data.Sender` (hence the isolated address), and the calldata payload.

### Recommendation
Move the `writeFn()` call to occur only after the post-execution balance check passes (mirroring the pattern already used correctly in `IBCOnAcknowledgementPacketCallback` and `IBCOnTimeoutPacketCallback`, where `writeFn()` is the last statement). This ensures that if the callback leaves residual balance at the isolated address, the entire `cachedCtx` state (including the token movement and the calldata call) is discarded and the packet processing can fail cleanly without stranding funds. Additionally, consider adding a governance- or user-triggered sweep/refund mechanism for the isolated address in case partial fund consumption is a legitimate, expected outcome of certain contract integrations.

### Proof of Concept
1. Attacker/sender initiates an ICS-20 transfer with a destination callback specifying `contractAddress = C` and `calldata = D`, where `C` is a contract (deployed by the same attacker or any third party) whose function implementing `D` only calls `transferFrom(isolatedAddr, C, partialAmount)` for less than the full approved `amount`, or does nothing at all with the approved allowance (a no-op function selector still succeeds since the contract has code, passing the `IsContract` check at line 162).
2. `IBCReceivePacketCallback` runs: `approve(C, amount)` executes in `cachedCtx`, then `CallEVMWithData` invokes `D` on `C`, pulling only `partialAmount` (or nothing).
3. `writeFn()` (line 227) commits both the approval and the partial-transfer execution to the real `ctx` — this state is now permanent regardless of what happens next.
4. The subsequent balance check (lines 234-238) finds `receiverTokenBalance != 0` and returns an error, but this error has no effect on already-committed state.
5. `amount - partialAmount` tokens remain forever at `receiverHex` (the isolated address from `GenerateIsolatedAddress`), which has no private key, no admin sweep, and no code — the funds are permanently unrecoverable. [3](#0-2)

### Citations

**File:** x/ibc/callbacks/types/keys.go (L13-17)
```go
// GenerateIsolatedAddress generates an isolated address for the given channel ID and sender address.
// This provides a safe address to call the receiver contract address with custom calldata
func GenerateIsolatedAddress(channelID string, sender string) sdk.AccAddress {
	return sdk.AccAddress(address.Module(ModuleName, []byte(channelID), []byte(sender))[:20])
}
```

**File:** x/ibc/callbacks/keeper/keeper.go (L104-241)
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

	// Check if the token pair exists and get the ERC20 contract address
	// for the native ERC20 or the precompile.
	// This call fails if the token does not exist or is not registered.
	token := transfertypes.Token{
		Denom:  data.Token.Denom,
		Amount: data.Token.Amount,
	}
	coin := ibc.GetReceivedCoin(packet.(channeltypes.Packet), token)

	tokenPairID := k.erc20Keeper.GetTokenPairID(ctx, coin.Denom)
	tokenPair, found := k.erc20Keeper.GetTokenPair(ctx, tokenPairID)
	if !found {
		return errorsmod.Wrapf(types.ErrTokenPairNotFound, "token pair for denom %s not found", data.Token.Denom.IBCDenom())
	}
	amountInt, ok := math.NewIntFromString(data.Token.Amount)
	if !ok {
		return errorsmod.Wrapf(types.ErrNumberOverflow, "amount overflow")
	}

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

	return nil
```
