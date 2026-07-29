## Finding

### Title
IBC receive-packet callback calls ERC-20 `approve` without resetting to zero, permanently locking funds in isolated receiver addresses for non-standard ("approve-race-protected") tokens - (File: `x/ibc/callbacks/keeper/keeper.go`)

### Summary
`ContractKeeper.IBCReceivePacketCallback` unconditionally calls the token's `approve` method with a new allowance value before invoking the destination DApp contract, without ever resetting the allowance to zero first. Tokens such as USDT that reject `approve()` calls which change a non-zero allowance directly to another non-zero value will cause every subsequent callback for the same isolated receiver/contract pair to permanently revert, while the transferred coins have already been credited to the (unrecoverable) isolated address by the underlying ICS20 transfer.

### Finding Description
In `IBCReceivePacketCallback` [1](#0-0) , the keeper calls:

```go
res, err := k.evmKeeper.CallEVM(cachedCtx, erc20.ABI, receiverHex, tokenPair.GetERC20Contract(), true, remainingGas, "approve", contractAddr, amountInt.BigInt())
```

This mirrors the exact anti-pattern described in the external report (`TokenUtils.checkApprove`): it directly sets a new non-zero allowance without first setting it to zero, using the standard `IERC20.approve` selector on `tokenPair.GetERC20Contract()` — which can be any user/governance-registered "native" ERC20 contract backing a token pair, not just the module's own minted precompile/contract.

The receiver of the funds is a deterministic "isolated address" derived from `(destChannel, sender)` [2](#0-1) , which has no corresponding private key — it can only be acted upon through this callback's programmatic `approve` + destination-contract `transferFrom` flow. Per the IBC-go ADR-8 callback ordering, the destination-callback execution occurs *after* the underlying ICS20 `OnRecvPacket` has already minted/unlocked the transferred coins to that isolated address on the base (non-cached) context — so the funds are already credited before this callback logic runs.

Critically, the EVM state changes made during a successful callback invocation (including the `approve` allowance set and any partial `transferFrom` pulled by the destination contract) are committed via `writeFn()` at line 227 *before* the final balance-zero check at line 236 is evaluated [3](#0-2) . If the destination contract's logic only partially drains the approved allowance (e.g., contract bug, revert after partial pull, or intentional partial consumption) the final check fails and the function returns an error, but the non-zero residual allowance from the committed `approve` call persists in state.

Any subsequent IBC transfer-with-callback packet from the same `(channel, sender)` to the same destination contract will re-trigger this code path and call `approve` again with a new non-zero value while the stale non-zero allowance still exists. For a token contract that implements Tether-style "approve race protection" (revert when changing a non-zero allowance directly to a different non-zero value), this `approve` call will revert every time going forward, permanently breaking the callback for that isolated address/contract pair. Since the ICS20 receive already credited the isolated address (which is not signable by anyone), and the callback path that could otherwise move the funds out is now permanently broken, the tokens are irrecoverably stuck.

### Impact Explanation
This maps to the Critical "permanent freezing, locking ... of user funds ... token-pair-backed balances" bucket. An ordinary user performing standard ICS20 transfers with callback memos to a DeFi/DApp contract can trigger a permanently unrecoverable lock of their own (and other users') transferred tokens at a keyless isolated address, with no privileged action, malicious relayer, or governance misbehavior required — only a token contract registered as a `TokenPair` that behaves like real-world USDT with respect to `approve`.

### Likelihood Explanation
Likelihood is moderate-to-high in practice because:
- Widely used stablecoins (USDT and similarly implemented tokens) are prime, expected candidates for bridging/registration as token pairs.
- The only trigger needed is a destination contract that doesn't fully consume its approved allowance in a single call (a very common, even accidental, occurrence — e.g., a DApp reverting mid-execution after `approve` was already committed via `writeFn`, or only pulling a partial amount) — this does not require malicious intent from the contract owner, just an imperfect/first callback execution.
- Once the residual allowance exists, every future ICS20 transfer-with-callback from that same sender/channel to that contract is permanently bricked.

### Recommendation
Before calling `approve` with a new value in `IBCReceivePacketCallback`, first reset the allowance to zero (or use a check to only call `approve` when the current on-chain allowance is zero), mirroring the audited fix pattern: query current allowance, `approve(spender, 0)` if non-zero, then `approve(spender, amount)`. Additionally, consider validating that the destination contract fully consumes its allowance (or explicitly revoking any residual allowance) before writing back `cachedCtx` state, so that failed/partial callbacks cannot leave lingering non-zero allowances that break future callback executions.

### Proof of Concept
1. Governance/registration process adds a `TokenPair` for a Tether-like ERC20 contract `T` that implements: `require(allowance[msg.sender][spender] == 0 || amount == 0)` in `approve`.
2. User `A` sends an ICS20 transfer of `T` from chain B to chain A with a callback memo targeting DApp contract `C`. The isolated address `iso(channel, A)` is credited with the transferred amount by the base ICS20 `OnRecvPacket` prior to the callback executing.
3. `IBCReceivePacketCallback` calls `T.approve(C, amount)` (succeeds, allowance now `amount`), then invokes `C`'s callback function which (due to contract logic, e.g. it only pulls a sub-amount via `transferFrom`) leaves a non-zero residual allowance and does not fully drain `iso`'s balance. `writeFn()` commits this state; the final balance check then fails and the function returns an error (this failure does not roll back the ICS20 receipt or the already-committed approve/transferFrom state).
4. User `A` sends a second ICS20 transfer with a callback to the same contract `C` from the same channel. `IBCReceivePacketCallback` calls `T.approve(C, newAmount)` again — this now reverts because `T`'s allowance for `(iso, C)` is still non-zero from step 3, and the function errors out with `ErrAllowanceFailed`.
5. All of user `A`'s funds now sitting in `iso(channel, A)` (from step 4's ICS20 receipt, which already succeeded before the callback ran) cannot ever be pulled out via this callback path again, and `iso` has no private key for manual recovery — the funds are permanently locked. [4](#0-3)

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
