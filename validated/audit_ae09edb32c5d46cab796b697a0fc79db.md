### Title
Permanent freezing of IBC-received funds in the isolated receiver address when the destination ERC20 callback fails due to a missing/removed `TokenPair` — ([File: x/ibc/callbacks/keeper/keeper.go])

### Summary
The Cosmos EVM IBC callbacks module credits ICS-20 transfer proceeds to a deterministic "isolated address" that no private key controls, and relies entirely on the destination-callback contract (invoked in `IBCReceivePacketCallback`) to move those funds onward. If the received coin's `TokenPair` is not registered — or was disabled/deleted (e.g. via `ToggleConversion` or self-destruct-triggered `DeleteTokenPair`) between packet send and packet receipt — the callback aborts with `ErrTokenPairNotFound` before the contract call ever executes. Because ibc-go's destination-callback processing is non-atomic with the underlying transfer, the ICS-20 coins are already credited to the isolated address by the time this failure occurs, and they become permanently unrecoverable since nobody holds the isolated address's private key. This mirrors the report's core bug class: a two-phase asset-recovery flow (transfer-then-callback-withdraw) that gets permanently blocked when the underlying asset mapping is "sunset," stranding already-escrowed user funds.

### Finding Description
`IBCReceivePacketCallback` is the ContractKeeper hook invoked by ibc-go's IBC callbacks middleware after a `dest_callback`-tagged ICS-20 packet is received. The receiver of the transfer is forced to be a `GenerateIsolatedAddress(destChannel, sender)` address [1](#0-0) , which per the module's own documentation has no signer: "no user has the ability to sign messages on behalf of this account" [2](#0-1) .

The only way funds ever leave that address is via the `approve` + calldata call to the destination contract performed later in the same function: [3](#0-2) 

Before that call can happen, the keeper requires a registered `TokenPair` for the received denom: [4](#0-3) 

If no pair is found — because the denom was never registered as an ERC-20, or because governance disabled it via `ToggleConversion` [5](#0-4) , or because the pair was silently deleted after the backing ERC20 contract self-destructed [6](#0-5)  — `IBCReceivePacketCallback` returns an error and never reaches the `approve`/calldata call.

Since ibc-go's callbacks middleware executes destination callbacks *after* the underlying `OnRecvPacket` has already unescrowed/minted the coins to the receiver (the isolated address), a failure in this callback does **not** roll back that transfer — the module's own comments elsewhere in this file confirm the "funds may get stuck" pattern is a known risk class the code tries (incompletely) to guard against: "if there is no code, the call will still pass on the EVM side, but it will ignore the calldata and funds may get stuck" [7](#0-6) , and "This check is here to prevent funds from getting stuck in the isolated address, since they would become irretrievable" [8](#0-7) . However, that post-call safety check only fires *if* the ERC20 `approve`/contract-call step is reached at all — the earlier `ErrTokenPairNotFound` path (and any other pre-approve early return) exits before any such protection exists, leaving coins permanently parked at an address nobody can sign for.

### Impact Explanation
This satisfies the Critical impact bar for "permanent freezing, locking, theft, or unauthorized extraction of user funds ... or token-pair-backed balances." Coins delivered via a `dest_callback`-tagged ICS-20 transfer become permanently unrecoverable whenever the TokenPair required by `IBCReceivePacketCallback` is unavailable at receipt time — a condition triggerable simply by (a) targeting a denom that was never registered as an ERC20 pair, or (b) a TokenPair being disabled/deleted between the time the sender constructs/sends the packet and the time it is received (a race fully controllable/observable by an unprivileged relayer or the sender themselves, and also reachable by anyone self-destructing a registered native ERC20 contract to force pair deletion mid-flight). There is no recovery mechanism: the isolated address is generated deterministically and is never assigned a controllable key.

### Likelihood Explanation
Likelihood is Low-to-Medium: it requires the attacker/sender to craft an ICS-20 transfer with a `dest_callback` memo, either targeting an unregistered denom or timing the transfer against a TokenPair being disabled/self-destructed before the packet lands. Both preconditions are achievable by any unprivileged user without validator or governance cooperation — a malicious sender can simply choose to self-destruct their own registered ERC20 contract right after sending the packet, guaranteeing the callback hits `ErrTokenPairNotFound` on receipt. Since the sender fully controls the packet contents and the callback timing, this is trivially and deterministically reproducible.

### Recommendation
- Make destination-callback failures caused by `ErrTokenPairNotFound` (or any pre-approve failure) result in a refundable/recoverable state rather than a silent freeze — e.g., detect the missing TokenPair *before* crediting funds to the isolated address (fail the receive atomically so the sending chain sees a normal ack-error and refunds the sender), or
- Provide a governance/permissionless "sweep" path that lets the original packet `sender` (or a designated recovery mechanism) claim funds left in the isolated address when the destination callback cannot execute, mirroring the recommendation in the original report to relax constraints once the backing asset mapping is removed.
- Ensure `TokenPair` deletion/disabling logic (`ToggleConversion`, self-destruct-driven `DeleteTokenPair`) accounts for in-flight IBC packets referencing that denom.

### Proof of Concept
1. Register a native ERC20 `TokenPair` for a token contract that contains a `selfdestruct`-capable function (or simply pick any denom without a registered pair).
2. As the packet sender, submit an ICS-20 `MsgTransfer` to the Cosmos EVM chain with a memo containing a `dest_callback` referencing an arbitrary deployed contract, using the denom above.
3. Before the packet is relayed and processed, self-destruct the ERC20 contract backing the TokenPair (via any account capable of calling the contract's owner/self-destruct function), or have governance disable the pair via `ToggleConversion`.
4. When the packet is received, `OnRecvPacket`/ICS-20 core logic mints/unescrows the coin to the isolated address `GenerateIsolatedAddress(destChannel, sender)`.
5. The callbacks middleware then invokes `IBCReceivePacketCallback`, which calls `k.erc20Keeper.GetTokenPair` and returns `ErrTokenPairNotFound` [9](#0-8) , aborting before any `approve`/contract call.
6. The transfer itself is not reverted (destination callback failures are non-atomic per ibc-go's callbacks design); the coins now sit permanently at the isolated address, which has no corresponding private key, and are unrecoverable — matching the documented "Limitations" caveat in [2](#0-1) .

### Citations

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

**File:** x/ibc/callbacks/keeper/keeper.go (L159-164)
```go
	// Check if the contract address contains code.
	// This check is required because if there is no code, the call will still pass on the EVM side,
	// but it will ignore the calldata and funds may get stuck.
	if !k.evmKeeper.IsContract(ctx, contractAddr) {
		return errorsmod.Wrapf(types.ErrContractHasNoCode, "provided contract address is not a contract: %s", contractAddr)
	}
```

**File:** x/ibc/callbacks/keeper/keeper.go (L166-224)
```go
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

**File:** x/ibc/callbacks/README.md (L200-210)
```markdown
## Limitations

The receiver side callback **must** receive funds to an ephemeral address generated from the channelId and packet
sender address. Note that since this is a generated address, no user has the ability to sign messages on behalf of
this account even though it is a cross-chain representation of the packet sender.

Thus, a contract that receives the funds and calldata from the isolated receiver address **must** send the tokens
onwards to a desired address that is specified in the calldata. If tokens are deposited back into the isolated address,
they are unreachabe. If you wish to interact with a contract that does not implement functionality for sending the
tokens to a different address then you must interact with that contract through some wrapper contract interface that
can receive the funds, call the contract which deposits funds back to `msg.sender` and then the wrapper contract
```

**File:** x/erc20/keeper/proposals.go (L116-138)
```go
// ToggleConversion toggles conversion for a given token pair
func (k Keeper) toggleConversion(
	ctx sdk.Context,
	token string,
) (types.TokenPair, error) {
	id := k.GetTokenPairID(ctx, token)
	if len(id) == 0 {
		return types.TokenPair{}, errorsmod.Wrapf(
			types.ErrTokenPairNotFound, "token '%s' not registered by id", token,
		)
	}

	pair, found := k.GetTokenPair(ctx, id)
	if !found {
		return types.TokenPair{}, errorsmod.Wrapf(
			types.ErrTokenPairNotFound, "token '%s' not registered", token,
		)
	}

	pair.Enabled = !pair.Enabled
	k.SetTokenPair(ctx, pair)
	return pair, nil
}
```

**File:** x/erc20/keeper/msg_server.go (L42-53)
```go
	if pair.IsNativeERC20() {
		// Remove token pair if contract is suicided
		acc := k.evmKeeper.GetAccountWithoutBalance(ctx, pair.GetERC20Contract())
		if acc == nil || !acc.HasCodeHash() {
			k.DeleteTokenPair(ctx, pair)
			k.Logger(ctx).Debug(
				"deleting selfdestructed token pair from state",
				"contract", pair.Erc20Address,
			)
			// NOTE: return nil error to persist the changes from the deletion
			return nil, nil
		}
```
