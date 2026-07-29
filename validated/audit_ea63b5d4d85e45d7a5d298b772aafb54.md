### Title
Sender-controlled IBC source-callback can permanently block refund/timeout unescrow, freezing escrowed funds - ([File: x/ibc/callbacks/keeper/keeper.go])

### Summary
The `ibccallbacks` middleware wraps the ICS-20 transfer stack (`callbacks -> erc20 -> transfer`) and executes a sender-specified EVM contract callback (`IBCOnAcknowledgementPacketCallback` / `IBCOnTimeoutPacketCallback`) as part of the very same `OnAcknowledgementPacket`/`OnTimeoutPacket` call that performs the token refund/unescrow. Because a Cosmos SDK message is processed atomically, if the callback contract execution errors, the whole `OnAcknowledgementPacket`/`OnTimeoutPacket` call fails and any refund logic already run by the inner `transfer`/`erc20` layers in that same call is rolled back together with it. A packet sender fully controls the callback contract address they embed in the transfer `memo`, so a sender can point the callback at a contract (or non-contract/EOA/self-destructed contract) that deterministically reverts or runs out of gas on every invocation, permanently preventing the refund/timeout path from ever completing successfully — the escrowed coins become permanently stuck.

### Finding Description
The transfer stack is composed as (from top to bottom): `ibccallbacks.NewIBCMiddleware -> erc20.NewIBCMiddleware -> transfer.NewIBCModule` [1](#0-0) .

On an error acknowledgement or timeout, the underlying `transfer`/`erc20` layer performs the refund/unescrow of the coin to the sender as part of `OnAcknowledgementPacket`/`OnTimeoutPacket` [2](#0-1) [3](#0-2) .

The outer `ibccallbacks` middleware then (via ibc-go's callbacks framework) invokes `ContractKeeper.IBCOnAcknowledgementPacketCallback` / `IBCOnTimeoutPacketCallback` in the same processing flow. Both functions return a hard `error` (`ErrCallbackFailed`, `ErrOutOfGas`, etc.) whenever:
- the callback address has no code [4](#0-3) ,
- the EVM call to `onPacketAcknowledgement`/`onPacketTimeout` reverts [5](#0-4) ,
- the callback runs out of gas [6](#0-5) .

This matches the tested behavior: a failing callback (e.g., calling a non-existent/empty contract address) causes the entire `OnAcknowledgementPacket` call to return an error up to the caller, seen as `"ABCI code: 4"` in integration tests [7](#0-6) .

Crucially, only the packet **sender** can set the `src_callback` address in the memo (per module documentation) [8](#0-7) , and the repository's own tests never exercise the combination of `ackType == "error"` (i.e., a refund is actually required) together with a callback that fails (non-contract address, out-of-gas, or reverting logic) — the only tested failing-callback cases use `ackType: "success"` (no refund needed) [7](#0-6) , while the only tested error-ack-with-callback case uses a callback contract that behaves correctly [9](#0-8) .

Since the callback address, its bytecode, and its behavior are fully attacker/sender-controlled, and the failure mode is deterministic (a contract that unconditionally reverts, or has no code, or intentionally burns gas will fail identically on every relayer retry of `MsgAcknowledgement`/`MsgTimeout`), the refund can never be committed. This mirrors the bug class in the external report (`L1MessageService`/`L2MessageService`): a failed downstream call permanently blocks the release of escrowed value, with no built-in recovery/retry-independent refund path.

### Impact Explanation
This falls under "Critical permanent freezing, locking, theft, or unauthorized extraction of user funds ... or escrowed assets" — escrowed IBC transfer amounts (native coins or native-ERC20-backed coins) can become permanently unrecoverable because the atomic coupling of the refund/unescrow operation with an untrusted, sender-controlled external call means a single always-reverting or no-code callback contract can indefinitely block the refund logic on every retry of the acknowledgement/timeout message. Although the sender is the one who nominates the malicious/broken contract, the escrowed value being frozen is real user (or user-manipulated) value that cannot be recovered through any protocol-level path once the callback is guaranteed to keep failing — there is no way to disassociate the callback failure from the refund settlement in the current design.

### Likelihood Explanation
Triggering this requires only an unprivileged user action: sending a normal `MsgTransfer` (or IBC v2 payload) with a `memo.src_callback.address` pointing at:
- an address with no bytecode (EOA / self-destructed contract), or
- a contract that always reverts / consumes all provided gas.

No relayer or validator collusion is needed — the failure is deterministic and reproducible on every relay attempt of the acknowledgement or timeout. The scenario is directly supported by the existing memo-parsing and callback-execution code paths, and the repository's tests confirm that a failing callback aborts the entire ack/timeout handler with an error, which is the core mechanism enabling permanent lock.

### Recommendation
Decouple the mandatory refund/unescrow accounting from the optional, sender-controlled callback execution: perform the refund logic (transfer/erc20 layer) first and commit it unconditionally, then invoke the source callback in a fully isolated sub-context (e.g., `CacheContext` that is only ever used to emit best-effort events/logs) whose failure is caught, logged, and does not propagate an error that aborts the already-completed refund. This is consistent with the general IBC callbacks design goal (per the module's own README, callbacks should not risk stranding funds that would "otherwise be stuck") — the current implementation needs to guarantee that a broken/malicious callback contract can never block completion of the underlying refund that ibc-go's callbacks act on top of.

### Proof of Concept
1. Sender deploys (or selects) a contract `Bad` whose `onPacketAcknowledgement`/`onPacketTimeout` implementation always `revert()`s (or simply uses an address with no code).
2. Sender sends `MsgTransfer` with `memo`:
```json
{"src_callback": {"address": "<Bad or EOA address>", "gas_limit": "1000000"}}
```
3. The packet fails on the destination (error acknowledgement) or times out.
4. Relayer submits `MsgAcknowledgement`/`MsgTimeout`. The ibc-go core deletes the packet commitment and invokes the transfer stack: `erc20`/`transfer` execute the refund of escrowed funds to sender, then `ibccallbacks.IBCOnAcknowledgementPacketCallback` (or `IBCOnTimeoutPacketCallback`) is invoked and fails (`ErrCallbackFailed` due to no bytecode / revert / out-of-gas) — see the same failure mode already demonstrated in `TestOnAcknowledgementPacketWithCallback` "failure - callback to non-existent contract"/"failure - callback to empty address" cases [7](#0-6) .
5. Because the whole `OnAcknowledgementPacket` call returns an error, the Cosmos SDK message processing reverts entirely, undoing the refund and leaving the packet commitment intact (or, for a timeout, leaving escrowed funds locked with the commitment never cleared).
6. Every subsequent relay attempt of the same `MsgAcknowledgement`/`MsgTimeout` will hit the same deterministic callback failure, so the refund can never be completed — the escrowed funds are permanently frozen.

Note: I was unable to directly step through the vendored ibc-go `callbacks` module's `ProcessCallback` source in this repository's index (only usage points inside `x/ibc/callbacks/keeper/keeper.go` were indexed) to confirm whether ibc-go internally wraps callback errors in a panic-recovery that is always non-fatal to the acknowledgement processing. The observable test behavior in this repo (`"ABCI code: 4"` propagating as a hard failure for a failing callback) indicates that, at least for this integration, callback failures are NOT silently swallowed and do propagate as failing results for `OnAcknowledgementPacket`. If further review of the vendored ibc-go module shows that callback failures are always caught and never abort the encompassing message, this finding would need to be downgraded/rejected — this should be verified with full repository access (e.g., a Devin session) before treating this as fully confirmed.

### Citations

**File:** evmd/app.go (L511-522)
```go
	// create IBC module from top to bottom of stack
	var transferStack porttypes.IBCModule

	transferStack = transfer.NewIBCModule(app.TransferKeeper)
	maxCallbackGas := uint64(1_000_000)
	transferStack = erc20.NewIBCMiddleware(app.Erc20Keeper, transferStack)
	app.CallbackKeeper = ibccallbackskeeper.NewKeeper(
		app.AccountKeeper,
		app.EVMKeeper,
		app.Erc20Keeper,
	)
	transferStack = ibccallbacks.NewIBCMiddleware(transferStack, app.IBCKeeper.ChannelKeeper, app.CallbackKeeper, maxCallbackGas)
```

**File:** x/erc20/ibc_middleware.go (L69-94)
```go
// OnAcknowledgementPacket implements the IBCModule interface.
// It refunds the token transferred and then automatically converts the
// Cosmos Coin to their ERC20 token representation.
func (im IBCMiddleware) OnAcknowledgementPacket(
	ctx sdk.Context,
	channelVersion string,
	packet channeltypes.Packet,
	acknowledgement []byte,
	relayer sdk.AccAddress,
) error {
	var ack channeltypes.Acknowledgement
	if err := transfertypes.ModuleCdc.UnmarshalJSON(acknowledgement, &ack); err != nil {
		return errorsmod.Wrapf(errortypes.ErrUnknownRequest, "cannot unmarshal ICS-20 transfer packet acknowledgement: %v", err)
	}

	var data transfertypes.FungibleTokenPacketData
	if err := transfertypes.ModuleCdc.UnmarshalJSON(packet.GetData(), &data); err != nil {
		return errorsmod.Wrapf(errortypes.ErrUnknownRequest, "cannot unmarshal ICS-20 transfer packet data: %s", err.Error())
	}

	if err := im.Module.OnAcknowledgementPacket(ctx, channelVersion, packet, acknowledgement, relayer); err != nil {
		return err
	}

	return im.keeper.OnAcknowledgementPacket(ctx, packet, data, ack)
}
```

**File:** x/erc20/keeper/ibc_callbacks.go (L156-188)
```go
// OnAcknowledgementPacket responds to the success or failure of a packet
// acknowledgement written on the receiving chain. If the acknowledgement was a
// success then nothing occurs. If the acknowledgement failed, then the sender
// is refunded and then the IBC Coins are converted to ERC20.
// If the ERC20 conversion fails for whatever reason, such as an attempt to call
// a self-destructed ERC20 contract or an invalid function, OnAcknowledgementPacket
// still succeeds, but the user receives the corresponding bank token from the
// TokenPair instead. A user may then manually re-attempt the conversion.
func (k Keeper) OnAcknowledgementPacket(
	ctx sdk.Context, _ channeltypes.Packet,
	data transfertypes.FungibleTokenPacketData,
	ack channeltypes.Acknowledgement,
) error {
	switch ack.Response.(type) {
	case *channeltypes.Acknowledgement_Error:
		// convert the token from Cosmos Coin to its ERC20 representation
		return k.ConvertCoinToERC20FromPacket(ctx, data)
	default:
		// the acknowledgement succeeded on the receiving chain so nothing needs to
		// be executed and no error needs to be returned
		return nil
	}
}

// OnTimeoutPacket converts the IBC coin to ERC20 after refunding the sender
// since the original packet sent was never received and has been timed out.
// If the ERC20 conversion fails for whatever reason, such as an attempt to call
// a self-destructed ERC20 contract or an invalid function, OnTimeoutPacket still
// succeeds, but the user receives the corresponding bank token from the TokenPair
// instead. A user may then manually re-attempt the conversion.
func (k Keeper) OnTimeoutPacket(ctx sdk.Context, _ channeltypes.Packet, data transfertypes.FungibleTokenPacketData) error {
	return k.ConvertCoinToERC20FromPacket(ctx, data)
}
```

**File:** x/ibc/callbacks/keeper/keeper.go (L310-317)
```go
	contractAddr := common.HexToAddress(contractAddress)

	// Check if the contract address contains code.
	// This check is required because if there is no code, the call will still pass on the EVM side,
	// but it will ignore the calldata and funds may get stuck.
	if !k.evmKeeper.IsContract(ctx, contractAddr) {
		return errorsmod.Wrapf(types.ErrCallbackFailed, "provided contract address is not a contract: %s", contractAddr)
	}
```

**File:** x/ibc/callbacks/keeper/keeper.go (L324-336)
```go
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
```

**File:** x/ibc/callbacks/keeper/keeper.go (L424-434)
```go
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
```

**File:** evmd/tests/ibc/ibc_middleware_test.go (L917-947)
```go
		// FAILURE CASES - Invalid Contract
		{
			name:     "failure - callback to non-existent contract",
			malleate: nil,
			memo: func() string {
				return fmt.Sprintf(`{
					"src_callback": {
						"address": "0x1234567890123456789012345678901234567890",
						"gas_limit": "%d"
					}
				}`, 1_000_000)
			},
			ackType:        "success",
			onSendRequired: true,
			expError:       "ABCI code: 4",
		},
		{
			name:     "failure - callback to empty address",
			malleate: nil,
			memo: func() string {
				return fmt.Sprintf(`{
					"src_callback": {
						"address": "0x0000000000000000000000000000000000000000",
						"gas_limit": "%d"
					}
				}`, 1_000_000)
			},
			ackType:        "success",
			onSendRequired: true,
			expError:       "ABCI code: 4",
		},
```

**File:** evmd/tests/ibc/ibc_middleware_test.go (L1058-1072)
```go
		{
			name:     "success - callback with error ack and refund verification",
			malleate: nil,
			memo: func() string {
				return fmt.Sprintf(`{
					"src_callback": {
						"address": "%s",
						"gas_limit": "%d"
					}
				}`, contractAddr, 2_000_000)
			},
			ackType:        "error",
			onSendRequired: true,
			expError:       "",
		},
```

**File:** x/ibc/callbacks/README.md (L127-132)
```markdown
### Design

The sender of an IBC transfer packet may specify a contract to be called when the packet lifecycle completes.
This contract **must** implement the expected entrypoints for `onAcknowledgePacket` and `onTimeoutPacket`.

Crucially, **only the IBC packet sender can set the callback**.
```
