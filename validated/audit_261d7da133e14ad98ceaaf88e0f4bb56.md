## Analysis

The Connext Executor bug ("tokens increaseAllowance then call recipient; leftover tokens get stuck") has a direct analog in this repository's IBC destination callback flow.

### Title
Premature `writeFn()` commit before the "no leftover tokens" check permits calldata-attacker-controlled dust/partial drains to be permanently and irrecoverably stuck in an unrecoverable isolated address - (File: `x/ibc/callbacks/keeper/keeper.go`)

### Summary
`ContractKeeper.IBCReceivePacketCallback` mirrors the Executor.sol pattern: it grants an ERC20 `approve()` to an attacker/sender-controlled contract address over the full received amount held by an `isolatedAddr`, then invokes attacker-controlled `calldata` on that contract via `CallEVMWithData`. Exactly like Executor.sol, the code attempts to guard against "leftover" balances by checking, after the call, that `receiverTokenBalance == 0` [1](#0-0) . However, `writeFn()` — which commits the cached approve+call execution into the real `ctx` — is invoked *before* this check [2](#0-1) . This means any leftover balance is already permanently committed to state by the time the "unrecoverable tokens" error is even computed. Returning an error at that point cannot undo the commit already performed by `writeFn()`.

### Finding Description
The flow is:
1. `cachedCtx, writeFn := ctx.CacheContext()` creates a scratch context for gas-metering reasons [3](#0-2) .
2. The keeper `approve()`s the full received amount from `isolatedAddr` to the attacker/sender-supplied `contractAddr` [4](#0-3) .
3. The keeper then calls arbitrary, sender-controlled `calldata` on that same contract via `CallEVMWithData` [5](#0-4) .
4. `writeFn()` is called immediately, unconditionally committing steps 2–3 into `ctx` [6](#0-5) .
5. Only *after* the commit does the code check whether the isolated address still holds a nonzero balance, and if so, return an error [1](#0-0) .

Both `contractAddress` (the callback target) and `calldata` originate from attacker-controllable, unauthenticated ICS-20 packet memo fields set by the IBC packet *sender* on the counterparty chain [7](#0-6) . The isolated address itself is a keyless, deterministically derived address that no real user can ever sign for [8](#0-7) ; the module's own README documents that funds returned to the isolated address are otherwise unreachable [9](#0-8) .

If the destination contract's calldata transfers out only part of the approved amount (whether due to a bug, dust rounding, or a maliciously crafted contract that intentionally leaves a remainder), the partial transfer is already committed by `writeFn()` before the balance check runs. The subsequent error return cannot claw back that already-committed state mutation — the comment claiming this check "is here to prevent funds from getting stuck…since they would become irretrievable" does not hold given this ordering, because the commit already happened.

### Impact Explanation
Any dust or partial amount left in the isolated address after the callback executes becomes permanently, irrecoverably frozen: the isolated address has no private key, there is no recovery/reclaim path in this module (unlike the Connext report's suggested mitigations of a recovery address or owner-triggered retrieval), and the packet has already been (or will be) acknowledged as received. This matches the required Critical impact of "permanent freezing, locking, theft, or unauthorized extraction of user funds…or token-pair-backed balances," and it is triggerable by an unprivileged IBC packet sender/relayer supplying a crafted `dest_callback` memo, without any privileged access.

### Likelihood Explanation
The `contractAddress` and `calldata` used in the sensitive `approve` + arbitrary call sequence are both attacker-supplied via ordinary ICS-20 transfer memo fields, requiring no special permissions — any account able to originate an IBC transfer on a counterparty chain (or any relayer able to influence the memo, subject to the noted PFM sender-spoofing caveat also documented in this repo [10](#0-9) ) can trigger this path. The only requirement to produce a stuck-fund outcome is that the destination contract logic (which the attacker also controls, since they choose `contractAddress`) transfers out less than the full approved amount.

### Recommendation
Reorder the logic so that `writeFn()` is only called after confirming `receiverTokenBalance == 0`; if the balance check fails, discard the cached context instead of committing it. Additionally, consider adopting one of the Connext-recommended mitigations adapted to this design: sweep any leftover isolated-address balance back to the original sender/refund path, zero out the granted allowance if the check fails, or provide an explicit sender-triggered recovery mechanism for funds left in isolated addresses.

### Proof of Concept
1. An attacker on the counterparty chain sends an ICS-20 transfer with `memo.dest_callback.address` set to a contract they control and `calldata` that calls `transferFrom(isolatedAddr, attackerAddr, amount - 1)` (leaving 1 unit / or any partial amount) instead of the full `amount`.
2. `IBCReceivePacketCallback` executes `approve(contractAddr, amount)` then the malicious `calldata`, moving `amount - 1` out of `isolatedAddr`.
3. `writeFn()` commits this partial transfer into the real state.
4. The subsequent `receiverTokenBalance` check finds `1` remaining and returns an error — but the transfer of `amount - 1` is already committed, and the residual `1` unit (or, in variations, an arbitrarily large residual amount if the calldata is crafted to transfer a smaller fraction) is now permanently stuck in the keyless `isolatedAddr` with no recovery mechanism.

### Citations

**File:** x/ibc/callbacks/keeper/keeper.go (L129-131)
```go
	cachedCtx, writeFn := ctx.CacheContext()
	cachedCtx = evmante.BuildEvmExecutionCtx(cachedCtx).
		WithGasMeter(evmtypes.NewInfiniteGasMeterWithLimit(cbData.CommitGasLimit))
```

**File:** x/ibc/callbacks/keeper/keeper.go (L145-147)
```go
	// Generate secure isolated address from sender.
	isolatedAddr := types.GenerateIsolatedAddress(packet.GetDestChannel(), data.Sender)
	isolatedAddrHex := common.BytesToAddress(isolatedAddr.Bytes())
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

**File:** x/ibc/callbacks/keeper/keeper.go (L214-227)
```go
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

**File:** x/ibc/callbacks/README.md (L55-58)
```markdown
<!-- markdown-link-check-disable-next-line -->
> ***WARNING:***  Due to a [bug](https://twitter.com/SCVSecurity/status/1682329758020022272) in the
> packet forward middleware, we cannot trust the sender from chains that use PFM. Until that is fixed,
> we recommend chains to not trust the sender on contracts executed via IBC callbacks.
```

**File:** x/ibc/callbacks/README.md (L65-82)
```markdown
```json
{
    //... other ibc fields that we don't care about
    "data":{
    	"denom": "denom on counterparty chain (e.g. uatom)",  // will be transformed to the local denom (ibc/...)
        "amount": "1000",
        "sender": "addr on counterparty chain", // will be transformed
        "receiver": "isolated receiver address for sender",
    	"memo": {
           "dest_callback": {
              "address": "evmContractAddress",
              "gas_limit": "1000000",
              "calldata": "{abipacked_contract_calldata}",
            }
        }
    }
}
```
```

**File:** x/ibc/callbacks/README.md (L200-211)
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
can move the funds to a final desired address.
```
