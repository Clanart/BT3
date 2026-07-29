Confirmed: `HexAddressFromBech32String` is a straight bech32→bytes conversion of a real, self-controlled account address, unlike `GenerateIsolatedAddress`, which derives an unforgeable module-namespaced address (`address.Module(ModuleName, channelID, sender)`). This asymmetry between the two callback paths is the root cause of the analog vulnerability.

### Title
Ack/Timeout IBC callback notifications use the attacker's own address as `msg.sender`, letting users directly spoof cross-chain notifications without any real IBC packet - ([File: x/ibc/callbacks/keeper/keeper.go])

### Summary
The external report describes a vault that sends "notification" messages to arbitrary receivers with attacker-influenced payloads, letting an attacker make a receiving contract believe a real asset transfer occurred when it did not. The Cosmos EVM analog is the `IBCOnAcknowledgementPacketCallback` / `IBCOnTimeoutPacketCallback` functions in `x/ibc/callbacks/keeper/keeper.go`. Unlike the receive-side callback (which protects itself with an unforgeable, module-derived isolated address), these two source-side callbacks invoke the destination contract with `msg.sender` set to the plain hex conversion of the original packet sender's own bech32 address — an address the "sender" fully controls with their own private key.

### Finding Description
For `onRecvPacket` callbacks, the keeper deliberately uses `types.GenerateIsolatedAddress(channelID, sender)` [1](#0-0)  as the caller identity when invoking the destination contract, and validates the packet's declared receiver against it [2](#0-1) . This isolated address is a `address.Module(...)`-derived value that no externally owned account can ever sign for, so a contract checking `msg.sender` against it cannot be spoofed by a normal transaction.

By contrast, `IBCOnAcknowledgementPacketCallback` and `IBCOnTimeoutPacketCallback` derive the caller identity with a simple bech32→hex conversion of `packetSenderAddress` (the actual, real-world address that initiated the original transfer): [3](#0-2) [4](#0-3) 

This value is then used directly as the `from`/`msg.sender` for the EVM call into the destination contract: [5](#0-4) [6](#0-5) 

Because `sender` is nothing but `common.BytesToAddress(senderAccount.Bytes())` of a real Cosmos/EVM account [7](#0-6) , an attacker who owns that account's private key can submit an ordinary EVM transaction directly to any contract implementing `ICallbacks`, calling `onPacketAcknowledgement(channelId, portId, sequence, data, acknowledgement)` or `onPacketTimeout(channelId, portId, sequence, data)` with entirely fabricated arguments. From inside the contract, `msg.sender` is indistinguishable from a legitimate invocation triggered by the ibc-go callbacks middleware after a real packet lifecycle completed.

The module's own documentation instructs implementers that "only the IBC module can invoke these callback methods" and that this "prevents unauthorized contracts from triggering callback logic" [8](#0-7) , and the interface/README repeats this expectation [9](#0-8) . However, no fixed, unforgeable "IBC module" address is ever used as `msg.sender` for these two callbacks, so this documented invariant is architecturally unenforceable by any contract implementing the interface — exactly analogous to the vault sending a notification whose contents/origin a receiving pool cannot distinguish from a legitimate transfer-backed message.

### Impact Explanation
The README's own use case is a cross-chain swaps/escrow contract that listens for `onPacketAcknowledgement`/`onPacketTimeout` to release or refund escrowed funds depending on whether the outgoing IBC transfer succeeded or failed [10](#0-9) . Any contract built to this specification that trusts `msg.sender` as a proxy for "this came from the IBC module" is spoofable: an attacker can directly call `onPacketAcknowledgement` (or `onPacketTimeout`) with self-chosen `data`/`acknowledgement` payloads without ever sending a real IBC packet or moving any tokens. This allows the attacker to trigger premature release of escrowed contract funds, double-refund a transfer that actually succeeded elsewhere, or otherwise corrupt cross-chain escrow accounting — theft/duplication of escrowed value, matching the Critical "unauthorized extraction of escrowed assets" and "irreversible accounting corruption" impact classes.

### Likelihood Explanation
High. No privileged access is required: the attacker only needs an ordinary EVM account (the same account used to originate the real or a fake transfer memo) and knowledge of a contract's `ICallbacks` implementation. No relayer or IBC packet is needed at all to trigger the vulnerable code path in the victim contract — the attacker calls the victim contract directly, bypassing the `x/ibc/callbacks/keeper` entirely.

### Recommendation
- Route ack/timeout callback invocations through an unforgeable identity, analogous to `GenerateIsolatedAddress` used for `onRecvPacket`, or use a fixed, reserved module address as `msg.sender` for these calls instead of the raw sender address.
- Alternatively, have the callback keeper record packet-callback authorization state (e.g., a per-packet nonce/commitment) in the destination contract via a trusted precompile/system call before the EVM call, so the receiving contract can verify the call correlates with a genuine packet lifecycle event rather than relying on `msg.sender` alone.
- Update documentation/interface guidance so it does not instruct implementers to rely on an access-control check (`msg.sender == IBC module`) that is not actually achievable given the current sender derivation.

### Proof of Concept
1. Deploy (or target) a contract `C` implementing `ICallbacks`, following the documented cross-chain-swap pattern: it escrows funds when sending an IBC transfer with a `src_callback` memo, and releases/refunds escrow inside `onPacketAcknowledgement`/`onPacketTimeout` trusting that these are only invoked by the IBC module for `C`'s own outgoing packets.
2. As attacker, using your own EOA (or the address `C` used previously as `data.Sender` for some earlier transfer), directly submit an EVM transaction calling `C.onPacketTimeout(fakeChannelId, fakePortId, fakeSequence, fakeData)` (or `onPacketAcknowledgement` with a crafted success acknowledgement) — no real IBC packet, relayer, or token transfer is needed.
3. Since `msg.sender` seen by `C` is identical to what the legitimate keeper-driven call would present (`common.BytesToAddress(senderAccount.Bytes())`), `C` cannot distinguish the forged call from a genuine callback and executes its refund/release logic, resulting in unauthorized extraction/duplication of escrowed value.

### Citations

**File:** x/ibc/callbacks/types/keys.go (L13-17)
```go
// GenerateIsolatedAddress generates an isolated address for the given channel ID and sender address.
// This provides a safe address to call the receiver contract address with custom calldata
func GenerateIsolatedAddress(channelID string, sender string) sdk.AccAddress {
	return sdk.AccAddress(address.Module(ModuleName, []byte(channelID), []byte(sender))[:20])
}
```

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

**File:** x/ibc/callbacks/keeper/keeper.go (L266-269)
```go
// Contract Requirements:
//   - Must implement onPacketAcknowledgement(string calldata sourceChannel, string calldata sourcePort,
//     uint64 sequence, bytes calldata data, bytes calldata acknowledgement) function
//   - Should handle both successful and failed acknowledgements appropriately
```

**File:** x/ibc/callbacks/keeper/keeper.go (L305-309)
```go
	sender, err := utils.HexAddressFromBech32String(packetSenderAddress)
	if err != nil {
		return errorsmod.Wrapf(err, "unable to parse packet sender address %s", packetSenderAddress)
	}

```

**File:** x/ibc/callbacks/keeper/keeper.go (L324-330)
```go
	// Call the onPacketAcknowledgement function in the contract
	// NOTE: use the cached ctx for the EVM calls.
	res, err := k.evmKeeper.CallEVM(cachedCtx, *abi, sender, contractAddr, true, math.NewIntFromUint64(cachedCtx.GasMeter().GasRemaining()).BigInt(), "onPacketAcknowledgement",
		packet.GetSourceChannel(), packet.GetSourcePort(), packet.GetSequence(), packet.GetData(), acknowledgement)
	if err != nil {
		return errorsmod.Wrapf(types.ErrCallbackFailed, "EVM returned error: %s", err.Error())
	}
```

**File:** x/ibc/callbacks/keeper/keeper.go (L405-409)
```go
	senderAccount, err := sdk.AccAddressFromBech32(packetSenderAddress)
	if err != nil {
		return errorsmod.Wrapf(err, "unable to parse packet sender address %s", packetSenderAddress)
	}
	sender := common.BytesToAddress(senderAccount.Bytes())
```

**File:** x/ibc/callbacks/keeper/keeper.go (L424-428)
```go
	res, err := k.evmKeeper.CallEVM(ctx, *abi, sender, contractAddr, true, math.NewIntFromUint64(cachedCtx.GasMeter().GasRemaining()).BigInt(), "onPacketTimeout",
		packet.GetSourceChannel(), packet.GetSourcePort(), packet.GetSequence(), packet.GetData())
	if err != nil {
		return errorsmod.Wrapf(types.ErrCallbackFailed, "EVM returned error: %s", err.Error())
	}
```

**File:** utils/utils.go (L60-95)
```go
// HexAddressFromBech32String converts a hex address to a bech32 encoded address.
func HexAddressFromBech32String(addr string) (common.Address, error) {
	decodeFns := []func(string) ([]byte, error){
		func(s string) ([]byte, error) {
			accAddr, err := sdk.AccAddressFromBech32(s)
			if err != nil {
				return nil, err
			}
			return accAddr.Bytes(), nil
		},
		func(s string) ([]byte, error) {
			valAddr, err := sdk.ValAddressFromBech32(s)
			if err != nil {
				return nil, err
			}
			return valAddr.Bytes(), nil
		},
		func(s string) ([]byte, error) {
			consAddr, err := sdk.ConsAddressFromBech32(s)
			if err != nil {
				return nil, err
			}
			return consAddr.Bytes(), nil
		},
	}

	var lastErr error
	for _, fn := range decodeFns {
		bz, err := fn(addr)
		if err == nil {
			return common.BytesToAddress(bz), nil
		}
		lastErr = err
	}
	return common.Address{}, errorsmod.Wrapf(lastErr, "failed to convert bech32 string to address")
}
```

**File:** precompiles/callbacks/README.md (L61-72)
```markdown
**Invocation:**

- Only called by the IBC module
- Only invoked for packets sent by the implementing contract
- Called when packet timeout conditions are met

## Implementation Requirements

### Access Control

Implementing contracts must ensure that only the IBC module can invoke these callback methods.
This prevents unauthorized contracts from triggering callback logic.
```

**File:** x/ibc/callbacks/README.md (L121-139)
```markdown
## Ack and Timeout callbacks

A contract that sends an IBC transfer may need to listen for the outcome of the packet lifecyle.
`Ack`and `Timeout` callbacks allow
contracts to execute custom logic on the basis of how the packet lifecyle completes.

### Design

The sender of an IBC transfer packet may specify a contract to be called when the packet lifecycle completes.
This contract **must** implement the expected entrypoints for `onAcknowledgePacket` and `onTimeoutPacket`.

Crucially, **only the IBC packet sender can set the callback**.

### Use case

The cross-chain swaps implementation sends an IBC transfer. If the transfer were to fail, the sender should
be able to retrieve their funds which would otherwise be stuck in the contract. A contract may also wish to
retry sending the packet. In order to do either, the contract must receive the acknowledgement and timeout
callback to understand what occured in the packet lifecyle.
```
