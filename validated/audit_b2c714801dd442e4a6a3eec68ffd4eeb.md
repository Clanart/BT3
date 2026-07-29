## Title
Committed pre-check ERC20 approval in IBC receive-packet EVM callback can be exploited to drain isolated-address funds even when the callback errors and the packet is failed - (File: `x/ibc/callbacks/keeper/keeper.go`)

### Summary
`ContractKeeper.IBCReceivePacketCallback` sets an ERC20 `approve()` for an attacker-influenced destination contract address, calls that contract, and **writes the cached context to the real store (`writeFn()`) before** it validates that the callback actually consumed the tokens. Because the state is committed prior to the final safety check, an error returned after `writeFn()` does not roll back the approval or the EVM call side effects, while the higher-level IBC ack/packet-processing may still treat the whole packet as failed.

### Finding Description
`IBCReceivePacketCallback` [1](#0-0)  parses the packet callback data, whose destination contract address (`contractAddress`) and calldata are attacker/sender-controlled fields taken from the packet memo (per the module's own README, the `dest_callback.address`/`calldata` come directly from the ICS-20 packet metadata) [2](#0-1) .

The keeper then:
1. Grants the destination contract an ERC20 `approve()` for the received amount from the isolated receiver account: [3](#0-2) 
2. Calls the destination contract with attacker-supplied calldata: [4](#0-3) 
3. **Commits the cached context to the real state via `writeFn()`**: [5](#0-4) 
4. Only *after* that commit, checks whether the receiver's ERC20 balance is fully drained (i.e., that the callback contract actually used the approval/transferFrom to take the funds), returning an error if it did not: [6](#0-5) 

Because `writeFn()` executes **before** step 4's validation, any error returned in step 4 (or any later processing that treats the whole packet as failed) cannot undo the already-persisted `approve()` and contract-call side effects. This breaks the intended invariant documented in the code comment itself — "This check is here to prevent funds from getting stuck in the isolated address, since they would become irretrievable" — because the check runs *after* the point of no return, not before it.

This is the direct analog of the External Report's `AnyswapFacet` bug class: a protocol-controlled account (there, the diamond contract; here, the deterministic isolated per-channel/sender address) is made to `approve()` a caller-influenced address for the full asset amount, and that state is durably committed regardless of whether the intended atomic "approve + spend" invariant holds.

### Impact Explanation
If the callback contract does not fully consume the approval within the same call (e.g., it reverts partway after taking only part of the tokens, or intentionally leaves an allowance and does nothing), the function returns an error. Depending on how the outer IBC callbacks/ack pipeline treats this error (source-chain ErrorAcknowledgement, timeout retry, or packet-level revert), the destination chain may still hold:
- Minted/unescrowed ERC20 tokens sitting in the isolated address, and
- A live, uncapped-in-time ERC20 allowance for the attacker-controlled `contractAddr` to later `transferFrom` those tokens.

If the source chain, upon seeing the destination side reported failure, refunds the original escrowed value to the sender, the attacker's contract can subsequently call `transferFrom` using the still-valid allowance to also drain the isolated address's ERC20 balance on the destination chain — resulting in double-crediting/duplication of spendable value (refund on source chain + drain on destination chain), which matches the "unauthorized minting/duplication of spendable user value across IBC escrows" impact class.

Even absent a double-refund path, the committed-but-unspent approval leaves user value in the isolated address permanently exposed to unauthorized extraction by the callback contract at any future time chosen by the attacker, contradicting the explicit "prevent funds from getting stuck ... irretrievable" design goal, and is inconsistent with the atomic all-or-nothing execution the module's own README promises ("If the EVM call returns an error, return ErrAck").

### Likelihood Explanation
Reachable by any unprivileged IBC packet sender: the `dest_callback.address` and `calldata` in the packet memo are fully attacker-controlled, and an attacker can deploy a destination contract that deliberately does not call `transferFrom` for the full amount (or reverts after taking part), which is exactly the failure branch that triggers the post-`writeFn()` error path. No relayer or validator privilege is required — only crafting an ICS-20 transfer with a malicious memo.

### Recommendation
Move the `receiverTokenBalance` verification (and any other invariant checks on the outcome of the `approve`+call sequence) **before** `writeFn()` is invoked, so that if the invariant is violated, the entire cached context (including the `approve()` and the EVM call) is discarded rather than committed. Additionally, consider explicitly revoking (`approve(contractAddr, 0)`) any residual allowance before returning an error, as defense in depth, and audit how the outer IBC callbacks/ack path (ibc-go `ProcessCallback`) treats an error returned at this point to ensure it cannot also trigger a source-chain refund/duplicate credit for tokens whose approval/mint already landed on the destination chain.

### Proof of Concept
1. Attacker deploys `MaliciousCallback` on the destination chain, implementing an entrypoint that does nothing (or only calls `transferFrom(isolatedAddr, attacker, partialAmount)` for less than the full approved amount) and does not revert.
2. Attacker sends an ICS-20 transfer with memo `dest_callback.address = MaliciousCallback`, `dest_callback.calldata = <noop-call>`.
3. On the destination chain, `IBCReceivePacketCallback` runs:
   - `approve(MaliciousCallback, amountInt)` is executed on behalf of `isolatedAddr` and included in `cachedCtx`.
   - `CallEVMWithData` invokes `MaliciousCallback`, which does nothing.
   - `writeFn()` commits the `cachedCtx`, persisting the `approve` (allowance is now live in state) regardless of what happens next.
   - The post-check finds `receiverTokenBalance != 0`, returns `ErrEVMCall`.
4. Because state was already committed via `writeFn()`, the `MaliciousCallback` contract retains a valid ERC20 allowance for `isolatedAddr`'s tokens. If the packet-level failure causes a refund to be issued on the source chain (this final piece is unverified within this session and would need on-chain/integration testing of the surrounding ibc-go callbacks middleware to confirm), the attacker calls `transferFrom(isolatedAddr, attacker, amountInt)` at any later time to drain the destination-side tokens as well, i.e. two irreversible outcomes are obtained for value that should only exist once.

**Uncertainty/limitation:** I was not able to fully trace, within the available index, whether the outer ibc-go v10 callbacks middleware (`ProcessCallback`) itself wraps `OnRecvPacket` in a further cache-context that reverts the *entire* packet processing (including any earlier token mint into `isolatedAddr`) on a callback error, which would determine whether the "duplicate credit / refund on both chains" scenario is fully reachable versus only the "funds permanently exposed via unspent approval" scenario. Both scenarios independently satisfy the Critical impact bar (irreversible accounting corruption / unauthorized extraction of escrowed IBC value), but confirming the exact refund-duplication mechanics would require running the full ibc-go v10 callbacks middleware code, which is a dependency outside this repository's indexed content. A Devin session with full filesystem/build access would be needed to trace `github.com/cosmos/ibc-go/v10/modules/apps/callbacks` to confirm this precisely.

### Citations

**File:** x/ibc/callbacks/keeper/keeper.go (L104-119)
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

**File:** x/ibc/callbacks/README.md (L43-51)
```markdown
For use with EVM `recvPacket callbacks, the message fields above can be derived from the following:

- `Sender`: IBC packet senders cannot be explicitly trusted, as they can be deceitful. Chains cannot
risk the sender being confused with a particular local user or module address. To prevent this, the
`sender` is replaced with an account that represents the sender prefixed by the channel and a VM module
prefix. This is done by setting the sender to `address.Module(ModuleName, channelId, sender)`, where the
`channelId` is the channel id on the destination chain.
- `Contract`: This field should be directly obtained from the ICS-20 packet metadata
- `Data`: This field should be directly obtained from the ICS-20 packet metadata.
```
