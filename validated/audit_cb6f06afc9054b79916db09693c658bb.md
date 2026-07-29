## Finding [1](#0-0) 

### Title
Fund-lock validation runs after state is already committed in `IBCReceivePacketCallback`, permanently stranding ERC20-represented IBC tokens in the unrecoverable isolated address - (File: `x/ibc/callbacks/keeper/keeper.go`)

### Summary
The external report describes a `BasicSale`/`Vesting` bug where a missing `approve()` call permanently traps `mainToken` because the intended recipient contract can never pull the funds it needs via `transferFrom`. The Cosmos EVM analog is the inverse failure mode in `ContractKeeper.IBCReceivePacketCallback`: the code *does* call `approve()` correctly, but the function commits the cached EVM state (`writeFn()`) **before** validating that the callback contract actually pulled all approved tokens out of the isolated receiver address. If the target contract does not fully drain the allowance, the function returns an error, but by then the approval and the arbitrary contract-call side effects are already persisted to the real context, and per IBC-go's destination-callback design (ADR-8) a callback error does not unwind the underlying `OnRecvPacket` token transfer. The result is functionally identical to the reported class: tokens end up parked at an address with no private key, and the "no way to retrieve them" outcome that the code's own comment explicitly tries (but fails) to prevent.

### Finding Description
`IBCReceivePacketCallback` ( [2](#0-1) ) derives a deterministic `isolatedAddr` for the packet (`types.GenerateIsolatedAddress`) that has no corresponding private key — its only purpose is to hold the incoming IBC tokens momentarily so the destination callback contract can pull them via `transferFrom`.

The function:
1. Calls `approve(contractAddr, amountInt)` from the isolated address on a **cached context** (`cachedCtx`) [3](#0-2) .
2. Executes the user/attacker-controlled `cbData.Calldata` against `contractAddr`, again on `cachedCtx` [4](#0-3) .
3. Calls `writeFn()`, which **commits all of the cached state changes (the approval and every effect of the arbitrary contract call) to the real context** [5](#0-4) .
4. **Only after that commit**, checks whether the isolated address still holds a nonzero token balance, and if so returns `ErrEVMCall` with the comment "receiver has %d unrecoverable tokens after callback" [6](#0-5) .

Because the balance check happens after `writeFn()`, returning an error at this point cannot undo the already-committed approve/callback effects. Additionally, per the standard IBC-go callbacks middleware pattern that this code implements (`callbacktypes.ContractKeeper`, destination-callback), errors returned from a destination callback do not cause the underlying transfer (`OnRecvPacket`) to fail or roll back — the transfer that credited the isolated address already succeeded on the main `ctx` before this callback ever runs. The doc comment on the function itself acknowledges the intended purpose of this check ("This check is here to prevent funds from getting stuck in the isolated address, since they would become irretrievable") but the ordering defeats it.

### Impact Explanation
Any packet sender can set destination callback data pointing at a contract that (by design, bug, or partial revert of nested calls) does not call `transferFrom` for the entire approved amount. Once such a packet is relayed:
- The `OnRecvPacket` transfer has already credited the isolated address with the full token amount on the primary `ctx`.
- The subsequent callback commits the approval and contract-call side effects via `writeFn()`.
- The trailing balance check fires too late — the leftover token balance is stuck forever at `isolatedAddr`, which has no controllable private key, i.e., permanently frozen/unrecoverable user value, matching the "Critical permanent freezing/locking of user funds ... token-pair-backed balances" impact category.

### Likelihood Explanation
This is trivially reachable by an ordinary IBC transfer sender: craft a `MemoDestinationCallback` targeting any contract that under-consumes the granted allowance (e.g., a no-op contract, a contract that only pulls a partial amount, or one whose internal logic reverts a sub-call after partially succeeding). No privileged relayer or validator behavior is required — the sender fully controls `contractAddress` and `cbData.Calldata`. This is not a hypothetical: the developers' own code comment demonstrates awareness of exactly this risk, but the check is placed after the point of no return.

### Recommendation
Perform the receiver-balance validation on `cachedCtx` (the state that has not yet been committed) **before** calling `writeFn()`, and only call `writeFn()` if the validation passes. Alternatively, restructure so that `writeFn()` is deferred until after all invariants (including the leftover-balance check) are confirmed, ensuring that a failed callback never persists partial approve/callback state that would otherwise strand funds at the isolated address.

### Proof of Concept
1. Initiate an ICS-20 transfer with a `DestinationCallback` memo pointing to `contractAddress = C`, where `C` is a contract whose fallback/target function does nothing (or calls `transferFrom(isolatedAddr, C, 1)` for only 1 unit instead of the full `amountInt`).
2. On packet receipt, `OnRecvPacket` credits `isolatedAddr` with the full transferred amount on `ctx`.
3. `IBCReceivePacketCallback` runs: `approve(C, amountInt)` and the call to `C` both execute on `cachedCtx`.
4. `writeFn()` commits both effects to `ctx`.
5. The post-commit balance check finds `isolatedAddr` still holds `amountInt - 1` tokens and returns `ErrEVMCall`.
6. Because destination-callback errors do not roll back the already-successful `OnRecvPacket` transfer, the acknowledgement is still a success and the remaining `amountInt - 1` tokens are permanently stranded at `isolatedAddr`, which has no recoverable private key.

### Citations

**File:** x/ibc/callbacks/keeper/keeper.go (L104-242)
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
}
```
