### Title
Unvalidated negative/overflowing ICS20 amount in `IBCReceivePacketCallback` allows attacker to mint a near-unlimited ERC20 allowance for an arbitrary spender - ([File: x/ibc/callbacks/keeper/keeper.go])

### Summary
`ContractKeeper.IBCReceivePacketCallback` parses the attacker-controlled `data.Token.Amount` field from the raw ICS20 packet with `math.NewIntFromString`, which only rejects strings that fail to parse or whose *bit length* exceeds 256 - it does not reject negative values. The resulting `amountInt.BigInt()` (which can be negative) is passed directly into `EVMKeeper.CallEVM(..., "approve", contractAddress, amountInt.BigInt())`. go-ethereum's ABI encoder converts a negative `*big.Int` for a `uint256` parameter via two's-complement wraparound, producing a value near `2^256-1`. This grants the attacker-chosen `contractAddress` an unlimited allowance over the isolated receiver's ERC20 balance. [1](#0-0) [2](#0-1) 

### Finding Description
`IBCReceivePacketCallback` independently re-unmarshals the raw packet bytes (`transfertypes.UnmarshalPacketData`) rather than relying on state already validated by the wrapped ICS20 transfer application: [3](#0-2) 

The function receives the transfer app's `ack` as a parameter but never checks `ack.Success()` before executing its approve/call logic: [4](#0-3) 

The amount is parsed only for parse-failure/overflow, not for sign: [1](#0-0) 

That value is fed straight into the EVM `approve` call: [5](#0-4) 

Because ICS20's own `FungibleTokenPacketData.ValidateBasic` (in the underlying `transfer` module, which runs *before* this callback in the middleware stack) rejects non-positive amounts, a packet with a negative amount will fail the actual token transfer/mint step and produce an error acknowledgement — no coins are escrowed or converted, and the isolated receiver's real ERC20 balance stays at zero. However, the callbacks middleware still invokes `IBCReceivePacketCallback` for destination callbacks regardless of the underlying ack outcome, and this implementation does not gate on `ack.Success()`. It therefore still executes `approve(contractAddress, wrapped_uint256)` and the subsequent `CallEVMWithData`, then commits state via `writeFn()`: [6](#0-5) 

The final safety check only verifies that the isolated receiver's ERC20 *balance* is zero after the callback — it does not check or reset the *allowance* that was just granted: [7](#0-6) 

Since balance is genuinely zero (no real transfer occurred), this check passes vacuously and the function returns success, leaving a durable, unlimited ERC20 allowance on the isolated address's token-pair contract for an attacker-chosen spender, with no compensating cleanup.

The isolated address is deterministic and attacker-influenced (`GenerateIsolatedAddress(destChannel, data.Sender)`, where `data.Sender` is attacker-controlled): [8](#0-7) 

An attacker can subsequently fund that same isolated address with real tokens via any ordinary IBC transfer (no callback needed) using the same sender/channel pair, then call `transferFrom` with the pre-planted unlimited allowance to drain those funds.

### Impact Explanation
This allows an unprivileged attacker to plant an unlimited ERC20 `approve` allowance over an address they can later fund, and drain it via `transferFrom` — unauthorized extraction/theft of ERC20-represented user value, meeting the Critical "theft of ERC20 balances" impact bar.

### Likelihood Explanation
The attacker only needs to originate an ordinary ICS20 transfer with a crafted destination-callback memo and a negative `Amount` string, and control a destination contract with code (trivial, permissionless). No validator, relayer, or admin privilege is required — the packet relay itself is honest infrastructure behavior; the vulnerability is in how the destination chain processes a failed/negative-amount packet's callback.

### Recommendation
- In `IBCReceivePacketCallback`, gate the entire approve/call flow on `ack.Success()` before performing any EVM interaction.
- Explicitly reject non-positive `amountInt` (`amountInt.IsPositive()`), not just parse/overflow failures, before calling `CallEVM("approve", ...)`.
- After the callback, verify/reset any allowance granted to `contractAddress` on the isolated receiver so no residual allowance survives beyond the completed transaction, regardless of ack outcome.

### Proof of Concept
1. Attacker deploys a contract `C` with arbitrary code (to pass `IsContract` check) on the destination push-chain-evm chain.
2. Attacker sends an ICS20 transfer from a source chain with `Amount = "-1"` and memo:
   `{"dest_callback": {"address": "<C>", "gas_limit": 1000000, "calldata": ""}}`, receiver set to the isolated address for `(destChannel, sender)`.
3. On receipt, transfer module's `ValidateBasic` fails on the negative amount → error ack, no coin escrow/mint.
4. Callbacks middleware still invokes `IBCReceivePacketCallback`; `math.NewIntFromString("-1")` succeeds with `ok=true`; `CallEVM(..., "approve", C, big.NewInt(-1))` ABI-encodes `-1` as `2^256-1` and commits an unlimited allowance for `C` over the isolated address's ERC20 token-pair balance.
5. Attacker performs a normal, unrelated ICS20 transfer (no callback) crediting real value to the same isolated address.
6. Attacker calls `C.transferFrom(isolatedAddr, attacker, amount)` using the pre-existing unlimited allowance to steal the funds. [9](#0-8)

### Citations

**File:** x/ibc/callbacks/keeper/keeper.go (L104-110)
```go
func (k ContractKeeper) IBCReceivePacketCallback(
	ctx sdk.Context,
	packet ibcexported.PacketI,
	ack ibcexported.Acknowledgement,
	contractAddress string,
	version string,
) error {
```

**File:** x/ibc/callbacks/keeper/keeper.go (L111-119)
```go
	data, err := transfertypes.UnmarshalPacketData(packet.GetData(), version, "")
	if err != nil {
		return err
	}

	cbData, isCbPacket, err := callbacktypes.GetCallbackData(data, version, packet.GetDestPort(), ctx.GasMeter().GasRemaining(), ctx.GasMeter().GasRemaining(), callbacktypes.DestinationCallbackKey)
	if err != nil {
		return err
	}
```

**File:** x/ibc/callbacks/keeper/keeper.go (L145-147)
```go
	// Generate secure isolated address from sender.
	isolatedAddr := types.GenerateIsolatedAddress(packet.GetDestChannel(), data.Sender)
	isolatedAddrHex := common.BytesToAddress(isolatedAddr.Bytes())
```

**File:** x/ibc/callbacks/keeper/keeper.go (L180-242)
```go
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
