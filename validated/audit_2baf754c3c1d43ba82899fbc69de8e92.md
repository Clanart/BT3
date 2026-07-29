### Title
IBC timeout callback executes attacker-controlled contract against the real transaction gas meter instead of the isolated capped meter, enabling a deterministic out-of-gas that permanently blocks packet timeout processing - (File: x/ibc/callbacks/keeper/keeper.go)

### Summary
The Cosmos EVM IBC callbacks module lets an ICS-20 packet sender specify an arbitrary EVM contract and a `gas_limit` to be invoked when a packet they sent later times out (`src_callback` in the packet memo) [1](#0-0) . This is analogous to the LayerZero `_blockingLzReceive` bug class: a message-lifecycle callback is executed with a gas-accounting scheme that can be exhausted by attacker-controlled logic, breaking the processing pathway. In `IBCOnTimeoutPacketCallback`, unlike its sibling functions, the contract call is executed against the **real** context's gas meter instead of the isolated, gas-capped `cachedCtx`, defeating the documented DoS protection ("Enforces gas limits to prevent DoS attacks") [2](#0-1) .

### Finding Description
`IBCReceivePacketCallback` and `IBCOnAcknowledgementPacketCallback` both build a `cachedCtx` with an isolated `InfiniteGasMeterWithLimit(cbData.CommitGasLimit)` and correctly pass `cachedCtx` into `k.evmKeeper.CallEVM(...)`: [3](#0-2) 

`IBCOnTimeoutPacketCallback` builds the same kind of `cachedCtx`/gas-capped meter, but then calls `CallEVM` using the *original* `ctx`, not `cachedCtx`: [4](#0-3) 

Because `ctx` is the outer packet-timeout-processing context (part of a `MsgTimeout` transaction handled by a relayer or a downstream ABCI packet flow), the EVM execution of `onPacketTimeout` runs against whatever real gas meter is attached to `ctx` — not the isolated, capped meter intended to bound arbitrary user-supplied contract logic to `cbData.CommitGasLimit`. The gas amount passed as `gasCap` (`cachedCtx.GasMeter().GasRemaining()`) is decorative in this call path since the underlying EVM execution and any nested Cosmos-side gas consumption inside `CallEVM` will burn from `ctx`'s gas meter directly, rather than from the isolated meter that was supposed to constrain it.

Because `src_callback.address` is chosen by an unprivileged, potentially malicious local IBC packet sender (per the callbacks documentation, "only the IBC packet sender can set the callback") [5](#0-4) , an attacker can:
1. Send an ICS-20 transfer with a timeout, specifying a malicious contract as `src_callback.address` whose bytecode intentionally burns large amounts of gas (e.g., a loop, as demonstrated in the repo's own `RevertTestContract.sol`/`StandardRevertTestContract.sol` out-of-gas test helpers) [6](#0-5) .
2. Ensure the packet times out (e.g., a very short timeout window).
3. When any relayer submits `MsgTimeout`, `IBCOnTimeoutPacketCallback` invokes the malicious contract against the real `ctx` gas meter. Since the contract logic is deterministic and expensive, every relayer's attempt to process the timeout for that packet will run out of gas or exhaust the available block/tx gas in the same deterministic way.
4. This is exactly the LZ-analog "malicious owner sets up excessive gas-consuming state, wasting the allocated gas budget so the failure-handling path itself can't complete" — here the failure-handling path is the IBC timeout callback, and the "storeFailedMessage" analog is the requirement that the timeout be durably recorded so escrowed funds can be released.

### Impact Explanation
If the timeout callback deterministically exhausts gas on every relay attempt, the packet's timeout can never be successfully processed through this code path, which the module's own documentation states exists specifically so that senders can "retrieve their funds which would otherwise be stuck" [7](#0-6) . Depending on how the surrounding ibc-go callbacks middleware treats a failing/erroring `IBCOnTimeoutPacketCallback` (it wraps it in `ErrCallbackFailed` and returns it up the stack), a permanently failing timeout callback can prevent the packet lifecycle from completing cleanly, which is a precondition other application logic (contract-side refund/retry logic) depends on to release escrowed value — meeting the "critical permanent freezing/locking of user funds ... escrowed assets" bar in the required impact set. It also demonstrates a broken gas-accounting invariant (Public/Asset-representation path: "IBC ... flows must preserve consistent, capped resource accounting") since the isolated `cachedCtx` gas cap that the code explicitly documents as a DoS protection is silently bypassed for this one callback path.

### Likelihood Explanation
Likelihood is Medium-High: setting `src_callback` in the ICS-20 memo and choosing an arbitrary contract address is fully permissionless and requires no special privileges — only that the attacker is the packet sender (self-transfer or a routed transfer they control) and that the packet subsequently times out, which the attacker also fully controls by choosing a very short timeout. No governance, validator, or relayer collusion is required.

### Recommendation
Change `IBCOnTimeoutPacketCallback` to execute the EVM call against `cachedCtx` (the isolated, gas-capped context), consistent with `IBCReceivePacketCallback` and `IBCOnAcknowledgementPacketCallback`:
```go
res, err := k.evmKeeper.CallEVM(cachedCtx, *abi, sender, contractAddr, true,
    math.NewIntFromUint64(cachedCtx.GasMeter().GasRemaining()).BigInt(), "onPacketTimeout",
    packet.GetSourceChannel(), packet.GetSourcePort(), packet.GetSequence(), packet.GetData())
```
Additionally, ensure the subsequent `ctx.GasMeter().ConsumeGas(res.GasUsed, ...)` bookkeeping and `writeFn()` commit logic behave the same way as the acknowledgement callback (bound total gas transferred back to `ctx` to the packet's declared `CommitGasLimit`, never letting user-controlled contract execution consume unbounded gas from the enclosing transaction/block).

### Proof of Concept
Not independently executed against a live node in this analysis; the finding is derived from static comparison of the three sibling callback functions in `x/ibc/callbacks/keeper/keeper.go` [8](#0-7) , where `IBCOnTimeoutPacketCallback` is the only one that passes `ctx` instead of `cachedCtx` into `CallEVM`. Conceptually:
1. Deploy a contract implementing `onPacketTimeout(...)` that runs an expensive loop (e.g., repeated `keccak256`/storage writes) designed to exceed any reasonable relayer-supplied gas limit, similar to the existing test helper `standardOutOfGas`/`expensiveStorage` in `StandardRevertTestContract.sol` [9](#0-8) .
2. Send an ICS-20 transfer with `memo.src_callback.address` set to this contract and a short `timeout_timestamp`.
3. Let the packet time out; observe that `MsgTimeout` processing triggers `IBCOnTimeoutPacketCallback`, which calls the malicious contract using `ctx`'s real gas meter rather than the isolated `cachedCtx` meter, consuming gas without the intended `CommitGasLimit` cap and reliably failing/erroring regardless of relayer gas budget chosen up to the block gas limit.

**Uncertainty**: I was not able to run the code or trace exactly how the ibc-go `callbacks` middleware upstream treats a persistently-erroring `IBCOnTimeoutPacketCallback` in this specific fork (e.g., whether it retries indefinitely, marks the packet as permanently failed, or allows the base transfer module's timeout refund to still complete independently of the callback's success). Because the analysis tool did not have full visibility into `x/vm/keeper/call_evm.go` or the ibc-go `ProcessCallback` wrapper's exact interaction with `ctx.GasMeter()`, whether this results in truly irrecoverable fund freezing versus a recoverable failed-callback event that still allows the underlying escrow refund to proceed should be confirmed with a live Devin session before treating this as fully validated Critical severity.

### Citations

**File:** x/ibc/callbacks/README.md (L127-133)
```markdown
### Design

The sender of an IBC transfer packet may specify a contract to be called when the packet lifecycle completes.
This contract **must** implement the expected entrypoints for `onAcknowledgePacket` and `onTimeoutPacket`.

Crucially, **only the IBC packet sender can set the callback**.

```

**File:** x/ibc/callbacks/README.md (L134-139)
```markdown
### Use case

The cross-chain swaps implementation sends an IBC transfer. If the transfer were to fail, the sender should
be able to retrieve their funds which would otherwise be stuck in the contract. A contract may also wish to
retry sending the packet. In order to do either, the contract must receive the acknowledgement and timeout
callback to understand what occured in the packet lifecyle.
```

**File:** x/ibc/callbacks/README.md (L143-157)
```markdown
#### Callback information in memo

For the callback to be processed, the transfer packet's `memo` should contain the following in its JSON:

```json
"memo": {
    "src_callback": {
        "address": "evm_contract_addr",
        "gas_limit": "1000000",
    }
}
```

NOTE: For the source callbacks, the calldata **must** be empty since we do not support custom calldata and
instead expect to call a specific entrypoint with the packet information and acknowledgement.
```

**File:** x/ibc/callbacks/keeper/keeper.go (L98-103)
```go
// Security Notes:
//   - Uses isolated addresses to prevent unauthorized access
//   - Validates contract existence to prevent fund loss
//   - Enforces gas limits to prevent DoS attacks
//   - Requires contracts to implement proper token transfer logic
//   - Validates final token balances to ensure successful transfers
```

**File:** x/ibc/callbacks/keeper/keeper.go (L270-438)
```go
func (k ContractKeeper) IBCOnAcknowledgementPacketCallback(
	ctx sdk.Context,
	packet channeltypes.Packet,
	acknowledgement []byte,
	relayer sdk.AccAddress,
	contractAddress,
	packetSenderAddress string,
	version string,
) error {
	data, err := transfertypes.UnmarshalPacketData(packet.GetData(), version, "")
	if err != nil {
		return err
	}

	cbData, isCbPacket, err := callbacktypes.GetCallbackData(data, version, packet.GetDestPort(), ctx.GasMeter().GasRemaining(), ctx.GasMeter().GasRemaining(), callbacktypes.SourceCallbackKey)
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

// IBCOnTimeoutPacketCallback handles IBC packet timeout callbacks for cross-chain contract execution.
// This function is triggered when an IBC packet times out without receiving an acknowledgement,
// allowing contracts to handle timeout scenarios and perform cleanup or rollback operations.
//
// The function performs the following operations:
// 1. Unmarshals and validates the IBC packet data
// 2. Extracts callback data from the packet (source-side callback)
// 3. Validates that no calldata is present (timeout callbacks should not contain calldata)
// 4. Sets up a cached context with proper gas metering for EVM execution
// 5. Verifies the target contract exists and contains code
// 6. Calls the contract's onPacketTimeout function with packet details
// 7. Manages gas consumption and validates gas limits
// 8. Commits the cached context changes back to the original context
//
// Returns:
//   - error: Returns nil on success, or an error if any step fails including:
//   - Packet data unmarshaling errors
//   - Invalid callback data or unexpected calldata presence
//   - Address parsing failures
//   - Contract validation failures (non-existent or no code)
//   - ABI loading errors
//   - EVM execution errors
//   - Gas limit exceeded errors
//
// Contract Requirements:
//   - Must implement onPacketTimeout(string calldata sourceChannel, string calldata sourcePort,
//     uint64 sequence, bytes calldata data) function
//   - Should handle timeout scenarios appropriately (e.g., refunds, state rollbacks)
func (k ContractKeeper) IBCOnTimeoutPacketCallback(
	ctx sdk.Context,
	packet channeltypes.Packet,
	relayer sdk.AccAddress,
	contractAddress,
	packetSenderAddress string,
	version string,
) error {
	data, err := transfertypes.UnmarshalPacketData(packet.GetData(), version, "")
	if err != nil {
		return err
	}

	cbData, isCbPacket, err := callbacktypes.GetCallbackData(data, version, packet.GetDestPort(), ctx.GasMeter().GasRemaining(), ctx.GasMeter().GasRemaining(), callbacktypes.SourceCallbackKey)
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
}
```

**File:** tests/solidity/suites/revert_cases/contracts/RevertTestContract.sol (L96-112)
```text
    // ============ PRECOMPILE OUT OF GAS ERROR CASES ============
    
    /**
     * @dev Direct precompile call that runs out of gas
     */
    function directStakingOutOfGas(string calldata validatorAddress) external {
        counter++;
        emit OutOfGasSimulated(gasleft());
        
        // First consume most gas
        for (uint256 i = 0; i < 1000000; i++) {
            counter++;
        }
        
        // Then try precompile call with remaining gas
        STAKING_CONTRACT.delegate(address(this), validatorAddress, 1);
    }
```

**File:** tests/solidity/suites/revert_cases/contracts/StandardRevertTestContract.sol (L93-135)
```text
    /**
     * @dev Standard contract call that runs out of gas
     */
    function standardOutOfGas() external {
        counter++;
        emit OutOfGasSimulated(gasleft());
        
        // Consume all remaining gas
        while (gasleft() > 1000) {
            // Consume gas in a loop
            counter++;
        }
    }
    
    /**
     * @dev Expensive computation that can run out of gas
     */
    function expensiveComputation(uint256 iterations) external {
        counter++;
        emit OutOfGasSimulated(gasleft());
        
        // Perform expensive operations
        for (uint256 i = 0; i < iterations; i++) {
            // Hash operations are gas-expensive
            keccak256(abi.encode(i, block.timestamp, msg.sender));
            counter++;
        }
    }
    
    /**
     * @dev Storage operations that can run out of gas
     */
    function expensiveStorage(uint256 iterations) external {
        counter++;
        emit OutOfGasSimulated(gasleft());
        
        // Storage operations are very gas-expensive
        for (uint256 i = 0; i < iterations; i++) {
            assembly {
                sstore(add(counter.slot, i), i)
            }
        }
    }
```
