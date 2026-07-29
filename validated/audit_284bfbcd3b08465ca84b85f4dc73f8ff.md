### Title
`IBCOnTimeoutPacketCallback` executes the EVM callback against the live `ctx` instead of the isolated `cachedCtx`, breaking atomicity between callback execution and gas/failure handling - (File: `x/ibc/callbacks/keeper/keeper.go`)

### Summary
`IBCOnTimeoutPacketCallback` and `IBCOnAcknowledgementPacketCallback` are structurally identical: both build a `cachedCtx` (a `CacheContext()` wrapped with an infinite gas meter capped at `cbData.CommitGasLimit`) specifically so that the EVM callback's execution and its state writes can be applied atomically and only committed via `writeFn()` after all post-call checks succeed. `IBCOnAcknowledgementPacketCallback` follows this pattern correctly, invoking `k.evmKeeper.CallEVM(cachedCtx, ...)`. `IBCOnTimeoutPacketCallback`, however, calls `k.evmKeeper.CallEVM(ctx, ...)` — the live, uncached context — while still deriving the gas limit from `cachedCtx.GasMeter().GasRemaining()` and still invoking `writeFn()` at the end as if the call had gone through `cachedCtx`.

### Finding Description
Compare the two sibling functions in [1](#0-0) :
- Acknowledgement path builds `cachedCtx` and executes `CallEVM(cachedCtx, ...)`, only calling `writeFn()` after `ctx.GasMeter().ConsumeGas` and `IsOutOfGas()` checks pass, so a failing/over-budget callback's state changes are discarded because they live entirely inside the throwaway `cachedCtx`.

The timeout path builds the exact same `cachedCtx` construct but then calls the EVM directly on `ctx`: [2](#0-1) 

Because `CallEVM(ctx, ...)` commits its state effects directly into the outer, non-cached `ctx` (statedb/journal writes, storage, balances, logs), any state mutation performed by the target contract's `onPacketTimeout` implementation is applied immediately and unconditionally — before the subsequent `ctx.GasMeter().ConsumeGas`/`IsOutOfGas()` check is even evaluated. If that check fails, the function returns an error, but the contract-side state changes already written to `ctx` are **not rolled back**, unlike the acknowledgement path where such changes never leave the discarded `cachedCtx`. The final `writeFn()` call becomes a no-op with respect to the EVM call (since the cachedCtx was never used for it), so the comment "8. Commits the cached context changes back to the original context" is now misleading/incorrect for this path.

This breaks the "VM state path" invariant that recursive/one-shot execution and its revert handling must keep balances, storage, and logs consistent when execution is deemed to have failed — the callback contract's state is desynchronized from the reported failure of the callback.

### Impact Explanation
An unprivileged actor who controls the `contractAddress` registered in the ICS-20 `memo.src_callback` (the packet sender fully controls this field per the module's own design intent — `IBCOnTimeoutPacketCallback` is reachable by any account that sends an IBC transfer with a `src_callback` memo pointing at their own contract) can craft an `onPacketTimeout` implementation that:
1. Performs value-affecting state mutations (e.g., internal token bookkeeping, calling other contracts/precompiles to move balances, minting/crediting internal ledgers) early in its execution.
2. Deliberately consumes gas close to/at the `gas_limit` boundary so that the subsequent `ctx.GasMeter().ConsumeGas(res.GasUsed, ...)` check trips `IsOutOfGas()` after the mutation has already been committed to `ctx`.

Because these mutations are written directly to the live context rather than the isolated `cachedCtx`, they persist even though the function returns `ErrCallbackFailed`/out-of-gas — the callback is reported/treated by the surrounding IBC lifecycle as failed, but its side effects have already taken effect. This can be leveraged to duplicate or corrupt spendable value accounted for by the contract's own state (and any contract-to-contract calls it triggers) in a way that is unreachable by legitimate flows that expect atomic all-or-nothing callback execution — a direct violation of the "irreversible accounting corruption of spendable user value" and "broken invariant" bar for Critical impact in this scope.

### Likelihood Explanation
The trigger requires only sending a standard ICS-20 transfer with a `src_callback` memo pointing to an attacker-deployed contract, then causing (or waiting for) that packet to time out — both are unprivileged, ordinary user actions fully supported by the documented feature (`x/ibc/callbacks/README.md`). Precisely engineering gas consumption to land the "commit-then-fail" window is deterministic and fully controllable by the attacker's own contract bytecode, since EVM gas accounting is exact and reproducible.

### Recommendation
Make `IBCOnTimeoutPacketCallback` mirror `IBCOnAcknowledgementPacketCallback` exactly: execute `k.evmKeeper.CallEVM` against `cachedCtx` (not `ctx`), and only call `writeFn()` after the `ConsumeGas`/`IsOutOfGas()` checks succeed, so that any failing or gas-exceeding callback execution is fully discarded rather than partially committed.

### Proof of Concept
1. Attacker sends an ICS-20 transfer from the EVM chain with `memo = {"src_callback": {"address": "<attackerContract>", "gas_limit": "<N>"}}` to a destination that will not relay/ack the packet (or deliberately times it out).
2. `attackerContract.onPacketTimeout(...)` is invoked via `IBCOnTimeoutPacketCallback`; it performs a value-mutating call (e.g., invoking a precompile/ERC20 contract to move tokens it controls, or writing internal ledger state indicating "refund already processed") and then consumes gas up to just under `cbData.CommitGasLimit`.
3. Because `CallEVM` is executed against the raw `ctx` (see [3](#0-2) ), the mutation is committed to chain state immediately.
4. `ctx.GasMeter().ConsumeGas(res.GasUsed, ...)` subsequently reports `IsOutOfGas()`, and the function returns `ErrCallbackFailed`/"out of gas" — the timeout callback is treated as failed by the IBC lifecycle — yet the attacker's state mutation from step 2 remains permanently applied, producing an accounting state inconsistent with the "failed callback" outcome.

Note: I could not fully verify from this index how the outer `ibc-go` `ProcessCallback` wraps/discards `ctx` on an overall error return (the ibc-go vendor source is external and outside this index's coverage), so I cannot confirm whether an *additional* outer-layer cache exists that might further mitigate or compound this specific asymmetry. The concrete, verifiable root cause — the use of `ctx` instead of `cachedCtx` in `IBCOnTimeoutPacketCallback` versus the correct `cachedCtx` usage in the sibling `IBCOnAcknowledgementPacketCallback` — is confirmed directly in this repository's code.

### Citations

**File:** x/ibc/callbacks/keeper/keeper.go (L292-341)
```go
	// `ProcessCallback` in IBC-Go overrides the infinite gas meter with a basic gas meter,
	// so we need to generate a new infinite gas meter to run the EVM executions on.
	// Skipping this causes the EVM gas estimation function to deplete all Cosmos gas.
	// We re-add the actual EVM call gas used to the original context after the call is complete
	// with the gas retrieved from the EVM message result.
	cachedCtx, writeFn := ctx.CacheContext()
	cachedCtx = evmante.BuildEvmExecutionCtx(cachedCtx).
		WithGasMeter(evmtypes.NewInfiniteGasMeterWithLimit(cbData.CommitGasLimit))

	if len(cbData.Calldata) != 0 {
		return errorsmod.Wrap(types.ErrInvalidCalldata, "acknowledgement callback data should not contain calldata")
	}

	sender, err := utils.HexAddressFromBech32String(packetSenderAddress)
	if err != nil {
		return errorsmod.Wrapf(err, "unable to parse packet sender address %s", packetSenderAddress)
	}

	contractAddr := common.HexToAddress(contractAddress)

	// Check if the contract address contains code.
	// This check is required because if there is no code, the call will still pass on the EVM side,
	// but it will ignore the calldata and funds may get stuck.
	if !k.evmKeeper.IsContract(ctx, contractAddr) {
		return errorsmod.Wrapf(types.ErrCallbackFailed, "provided contract address is not a contract: %s", contractAddr)
	}

	abi, err := callbacksabi.LoadABI()
	if err != nil {
		return err
	}

	// Call the onPacketAcknowledgement function in the contract
	// NOTE: use the cached ctx for the EVM calls.
	res, err := k.evmKeeper.CallEVM(cachedCtx, *abi, sender, contractAddr, true, math.NewIntFromUint64(cachedCtx.GasMeter().GasRemaining()).BigInt(), "onPacketAcknowledgement",
		packet.GetSourceChannel(), packet.GetSourcePort(), packet.GetSequence(), packet.GetData(), acknowledgement)
	if err != nil {
		return errorsmod.Wrapf(types.ErrCallbackFailed, "EVM returned error: %s", err.Error())
	}

	// Consume the actual gas used on the original callback context.
	ctx.GasMeter().ConsumeGas(res.GasUsed, "callback onPacketAcknowledgement")
	if ctx.GasMeter().IsOutOfGas() {
		return errorsmod.Wrapf(types.ErrCallbackFailed, "out of gas")
	}

	writeFn()

	return nil
}
```

**File:** x/ibc/callbacks/keeper/keeper.go (L397-437)
```go
	cachedCtx, writeFn := ctx.CacheContext()
	cachedCtx = evmante.BuildEvmExecutionCtx(cachedCtx).
		WithGasMeter(evmtypes.NewInfiniteGasMeterWithLimit(cbData.CommitGasLimit))

	if len(cbData.Calldata) != 0 {
		return errorsmod.Wrap(types.ErrInvalidCalldata, "timeout callback data should not contain calldata")
	}

	senderAccount, err := sdk.AccAddressFromBech32(packetSenderAddress)
	if err != nil {
		return errorsmod.Wrapf(err, "unable to parse packet sender address %s", packetSenderAddress)
	}
	sender := common.BytesToAddress(senderAccount.Bytes())
	contractAddr := common.HexToAddress(contractAddress)

	// Check if the contract address contains code.
	// This check is required because if there is no code, the call will still pass on the EVM side,
	// but it will ignore the calldata and funds may get stuck.
	if !k.evmKeeper.IsContract(ctx, contractAddr) {
		return errorsmod.Wrapf(types.ErrCallbackFailed, "provided contract address is not a contract: %s", contractAddr)
	}

	abi, err := callbacksabi.LoadABI()
	if err != nil {
		return err
	}

	res, err := k.evmKeeper.CallEVM(ctx, *abi, sender, contractAddr, true, math.NewIntFromUint64(cachedCtx.GasMeter().GasRemaining()).BigInt(), "onPacketTimeout",
		packet.GetSourceChannel(), packet.GetSourcePort(), packet.GetSequence(), packet.GetData())
	if err != nil {
		return errorsmod.Wrapf(types.ErrCallbackFailed, "EVM returned error: %s", err.Error())
	}

	// Consume the actual gas used on the original callback context.
	ctx.GasMeter().ConsumeGas(res.GasUsed, "callback onPacketAcknowledgement")
	if ctx.GasMeter().IsOutOfGas() {
		return errorsmod.Wrapf(types.ErrCallbackFailed, "out of gas")
	}

	writeFn()
	return nil
```
