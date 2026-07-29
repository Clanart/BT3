### Title
Premature state commit in `IBCReceivePacketCallback` allows attacker-controlled contract calls to permanently lock user IBC funds - ([File: x/ibc/callbacks/keeper/keeper.go])

### Summary
`ContractKeeper.IBCReceivePacketCallback` in `x/ibc/callbacks/keeper/keeper.go` mirrors the reported bug class (self-constructed, fully attacker-specified "pair"/target contract invoked with attacker-controlled calldata, executed on behalf of a module-controlled account) but goes further: it calls `writeFn()` — committing the `approve()` and arbitrary `CallEVMWithData` state changes into `ctx` — **before** validating the post-condition invariant (that the isolated receiver's ERC20 balance is zero). If the invariant check fails, the function returns an error, but the balance-mutating side effects have already been written to `ctx`.

### Finding Description
`IBCReceivePacketCallback` [1](#0-0)  handles ICS-20 `dest_callback` memos. The `contractAddress` and `cbData.Calldata` are both derived directly from attacker-supplied packet memo fields (fully attacker-constructed, exactly like the "self constructed pairs" in the seed report). The keeper:

1. Approves the target `contractAddr` to pull `amountInt` of the received ERC20-represented tokens from the isolated `receiverHex` address [2](#0-1) .
2. Executes attacker-controlled `cbData.Calldata` against the attacker-controlled `contractAddr`, using the isolated receiver as caller: `k.evmKeeper.CallEVMWithData(cachedCtx, receiverHex, &contractAddr, cbData.Calldata, true, remainingGas)` [3](#0-2) .
3. **Commits** all of the above (the approve + arbitrary call effects) to the parent `ctx` via `writeFn()` [4](#0-3) .
4. **Only after that commit**, checks that the isolated receiver's ERC20 balance is now zero, returning an error if not [5](#0-4) .

The isolated address (`types.GenerateIsolatedAddress`) is a deterministic hash-derived address with no associated private key — it exists purely as an EVM `msg.sender` placeholder for the callback contract call. Any tokens that remain stranded there because the callback failed to fully "pull" them are permanently unrecoverable, since nobody controls that address's keys and no module has withdrawal rights over it.

Because `writeFn()` executes before the balance check, any code path where the arbitrary attacker-controlled `contractAddr`/`calldata` combination causes only a partial transfer (or the target contract intentionally leaves a small remainder, or reenters and manipulates balances such that the isolated address retains a nonzero balance) will still have its committed effects persisted to `ctx`, while the function returns an error. Depending on whether the caller (`ProcessCallback` in the ibc-go callbacks middleware) discards `ctx` entirely on error or only discards a stacked outer cache, the tokens debited/approved from the isolated account by the malicious contract may be left stuck (irretrievable) rather than cleanly rolled back with the packet safely reverted to the sender via a normal error-acknowledgement refund path. The intended safety net ("we can prevent funds from getting stuck") is defeated by ordering the commit before the check it is meant to gate.

This is a direct structural analog of the MarginRouter finding: an entrypoint invoked with fully attacker-specified "pair"/contract address and calldata, executed with privileged/module-derived identity (the isolated receiver, akin to MarginRouter's `msg.sender`), with insufficient guarantee that the invariant check actually gates the state mutation it's supposed to protect.

### Impact Explanation
If the outer IBC callback machinery does not wrap the entire `ctx` (including this function's `writeFn()`-committed changes) in its own transactional cache that is discarded on any non-nil error return, then a malicious/self-constructed callback contract can cause user IBC-transferred value to become permanently and irrecoverably locked at the isolated address — no private key exists to move it, and no module logic exists to sweep it back. This matches the Critical "permanent freezing/locking of user funds" impact class.

### Likelihood Explanation
Unprivileged trigger: any IBC packet sender can set an arbitrary `dest_callback.address` and `calldata` in the transfer memo, and deploy/control the destination contract referenced there — no permission checks gate `contractAddress` or `calldata` content in `IBCReceivePacketCallback`. The determining factor for actual Critical impact is whether `ProcessCallback` (in `ibc-go`'s callbacks middleware, external dependency) provides an additional outer cache-context rollback on error. I was not able to fully verify ibc-go's `ProcessCallback` internals within this repository (it's vendored from `github.com/cosmos/ibc-go/v10`), so I cannot conclusively confirm whether the outer layer fully neutralizes this ordering bug. This is the key uncertainty in this finding.

### Recommendation
Reorder `IBCReceivePacketCallback` so the "receiver has unrecoverable tokens" invariant check is performed on `cachedCtx` (before commit), and only call `writeFn()` if the check passes; otherwise return the error without committing any state, guaranteeing the packet handling naturally proceeds to the error-acknowledgement/refund path rather than leaving partially-executed callback state committed.

### Proof of Concept
Not independently reproduced in this session — this is a static-code-flow finding based on reading `x/ibc/callbacks/keeper/keeper.go` lines 104–242. Conceptual PoC: send an ICS-20 transfer with a `dest_callback` memo pointing to a malicious contract that implements a no-op or partial `transferFrom` (leaving 1 wei of allowance/balance at the isolated receiver), causing the post-check at line 236 to fail with `ErrEVMCall` after `writeFn()` at line 227 has already committed the approve+call effects; confirming whether the outer `ProcessCallback` caller reverts `ctx` in this case would require tracing into the vendored `ibc-go` `callbacks` module, which was not available for inspection in this indexed codebase.

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

**File:** x/ibc/callbacks/keeper/keeper.go (L214-218)
```go
	// NOTE: use the cached ctx for the EVM calls.
	res, err = k.evmKeeper.CallEVMWithData(cachedCtx, receiverHex, &contractAddr, cbData.Calldata, true, remainingGas)
	if err != nil {
		return errorsmod.Wrapf(types.ErrEVMCallFailed, "EVM returned error: %s", err.Error())
	}
```

**File:** x/ibc/callbacks/keeper/keeper.go (L226-227)
```go
	// Write cachedCtx events back to ctx.
	writeFn()
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
