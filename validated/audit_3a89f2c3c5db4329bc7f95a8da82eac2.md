### Title
Gas Refund Over-Issued When `PostTxProcessing` Fails: SSTORE Refunds Not Reversed After `tmpCtx` Discard - (File: x/evm/keeper/state_transition.go)

### Summary

In `ApplyTransaction`, when a registered `PostTxProcessing` hook fails, the EVM state is discarded by not committing `tmpCtx`. However, `res.GasUsed` was already computed inside `ApplyMessageWithConfig` with EVM-level SSTORE refunds applied. `RefundGas` is then called unconditionally using `leftoverGas = msg.GasLimit - res.GasUsed`, over-refunding the sender by the full SSTORE refund amount even though the state changes that earned those refunds were reverted.

### Finding Description

The flow in `ApplyTransaction` is:

1. A cache context `tmpCtx` is created when hooks are registered.
2. `ApplyMessageWithConfig(tmpCtx, msg, cfg, true)` runs the EVM and commits the `StateDB` to `tmpCtx`. Inside, the SSTORE refund counter (`stateDB.GetRefund()`) is consumed to reduce `gasUsed`:

```go
// x/evm/keeper/state_transition.go lines 532-534
temporaryGasUsed := msg.GasLimit - leftoverGas
refund := GasToRefund(stateDB.GetRefund(), temporaryGasUsed, refundQuotient)
leftoverGas += refund
```

`res.GasUsed` is therefore already reduced by the SSTORE refund amount.

3. Back in `ApplyTransaction`, if `PostTxProcessing` fails, `tmpCtx` is **not** committed — the SSTORE writes are reverted:

```go
// x/evm/keeper/state_transition.go lines 229-246
if !res.Failed() {
    if err = k.PostTxProcessing(tmpCtx, msg, receipt); err != nil {
        res.VmError = types.ErrPostTxProcessing.Error()
        res.Logs = nil
        // tmpCtx is NOT committed — EVM state reverted
    } else if commit != nil {
        commit()
        tmpCtxCommitted = true
    }
}
```

4. `RefundGas` is called **unconditionally** with `leftoverGas = msg.GasLimit - res.GasUsed`:

```go
// x/evm/keeper/state_transition.go lines 249-254
leftoverGas := msg.GasLimit - res.GasUsed
if err = k.RefundGas(ctx, msg, leftoverGas, cfg.Params.EvmDenom); err != nil {
    return nil, errorsmod.Wrapf(err, "failed to refund leftover gas to sender %s", msg.From)
}
```

Because `res.GasUsed` was already reduced by the SSTORE refund inside `ApplyMessageWithConfig`, `leftoverGas` is inflated by that refund amount. The sender receives coins from the fee collector for SSTORE operations that were ultimately reverted. The fee collector is permanently drained by the refund amount without any corresponding state change having been committed.

In Ethereum, when a transaction reverts, SSTORE refunds are not applied. Ethermint's post-hook failure is a Cosmos-level revert, but the gas accounting does not mirror this: the SSTORE refund earned during the successful EVM execution is never reversed.

### Impact Explanation

The fee collector (`authtypes.FeeCollectorName`) is over-drained by `SSTORERefund × gasPrice` per affected transaction. Under EIP-3529 (London), the SSTORE refund is capped at `gasUsed / 5`, so the over-refund per transaction is at most 20% of gas used. For a 10 M gas transaction at 100 gwei, this is up to 0.2 ETH per transaction. An attacker who can reliably trigger post-hook failures while generating SSTORE refunds can drain the fee collector incrementally across many transactions. This is a gas refund bug causing valid user funds/fees to be mis-accounted.

### Likelihood Explanation

Any chain deploying Ethermint with a `PostTxProcessing` hook (e.g., an ERC-20 conversion module, a staking rewards hook, or any custom hook) is affected. An unprivileged user can:

1. Deploy a contract that clears storage slots (generating SSTORE refunds via `SSTORE key 0`).
2. Craft a transaction whose emitted logs match the hook's failure condition (e.g., a log topic that causes the hook to return an error).
3. Submit the transaction: EVM execution succeeds and generates SSTORE refunds; the hook fails; `tmpCtx` is discarded; `RefundGas` over-refunds the sender.

No privileged access is required beyond the ability to submit a normal Ethereum transaction.

### Recommendation

After `PostTxProcessing` fails and `tmpCtx` is discarded, `res.GasUsed` should be corrected to add back the SSTORE refund that was applied in `ApplyMessageWithConfig`. One approach: track the SSTORE refund amount in `EVMResult` and, when the post-hook fails, recompute `leftoverGas` without the refund before calling `RefundGas`. Alternatively, move the SSTORE refund application to after the post-hook check so it is only applied when the state is actually committed.

### Proof of Concept

```
1. Register a PostTxProcessing hook that returns an error when it sees a specific log topic.

2. Deploy a contract:
   // Clears slot 1 (earns SSTORE refund) then emits the trigger log
   SSTORE(1, 0)          // clears storage → adds to stateDB.refund
   LOG0(triggerTopic)    // causes hook to fail

3. Fund sender with gasLimit * gasPrice tokens (ante handler deducts upfront).

4. Call ApplyTransaction with gasLimit = 1_000_000, gasPrice = 1 gwei.
   - ApplyMessageWithConfig runs: SSTORE refund ≈ 4800 gas applied,
     res.GasUsed ≈ actualGasUsed - 4800.
   - PostTxProcessing sees triggerTopic, returns error.
   - tmpCtx NOT committed: SSTORE write reverted.
   - RefundGas called with leftoverGas = gasLimit - (actualGasUsed - 4800).
   - Sender receives 4800 * 1 gwei = 4800 gwei extra from fee collector.

5. Repeat across many transactions to drain the fee collector incrementally.
``` [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

### Citations

**File:** x/evm/keeper/state_transition.go (L229-254)
```go
	if !res.Failed() {
		receipt.Status = ethtypes.ReceiptStatusSuccessful
		// Only call hooks if tx executed successfully.
		if err = k.PostTxProcessing(tmpCtx, msg, receipt); err != nil {
			// If hooks return error, revert the whole tx.
			res.VmError = types.ErrPostTxProcessing.Error()
			k.Logger(ctx).Error("tx post processing failed", "error", err)

			// If the tx failed in post processing hooks, we should clear the logs
			res.Logs = nil
		} else if commit != nil {
			// PostTxProcessing is successful, commit the tmpCtx
			commit()
			tmpCtxCommitted = true
			// Since the post-processing can alter the log, we need to update the result
			res.Logs = types.NewLogsFromEth(receipt.Logs)
		}
	}

	// Get the tracer and add OnGasChange hook for gas refund
	leftoverGas := msg.GasLimit - res.GasUsed

	// refund gas in order to match the Ethereum gas consumption instead of the default SDK one.
	if err = k.RefundGas(ctx, msg, leftoverGas, cfg.Params.EvmDenom); err != nil {
		return nil, errorsmod.Wrapf(err, "failed to refund leftover gas to sender %s", msg.From)
	}
```

**File:** x/evm/keeper/state_transition.go (L520-534)
```go
	refundQuotient := params.RefundQuotient

	// After EIP-3529: refunds are capped to gasUsed / 5
	if rules.IsLondon {
		refundQuotient = params.RefundQuotientEIP3529
	}

	// calculate gas refund
	if msg.GasLimit < leftoverGas {
		return nil, errorsmod.Wrap(types.ErrGasOverflow, "apply message")
	}
	// refund gas
	temporaryGasUsed := msg.GasLimit - leftoverGas
	refund := GasToRefund(stateDB.GetRefund(), temporaryGasUsed, refundQuotient)
	leftoverGas += refund
```

**File:** x/evm/keeper/gas.go (L54-88)
```go
// RefundGasWithPrice transfers the leftover gas to sender using the provided gas price.
func (k *Keeper) RefundGasWithPrice(
	ctx sdk.Context,
	msg *core.Message,
	leftoverGas uint64,
	gasPrice *big.Int,
	denom string,
) error {
	if gasPrice == nil {
		gasPrice = new(big.Int)
	}

	// Return EVM tokens for remaining gas, exchanged at the original rate.
	remaining := new(big.Int).Mul(new(big.Int).SetUint64(leftoverGas), gasPrice)

	switch remaining.Sign() {
	case -1:
		// negative refund errors
		return errorsmod.Wrapf(types.ErrInvalidRefund, "refunded amount value cannot be negative %d", remaining.Int64())
	case 1:
		// positive amount refund
		refundedCoins := sdk.Coins{sdk.NewCoin(denom, sdkmath.NewIntFromBigInt(remaining))}

		// refund to sender from the fee collector module account, which is the escrow account in charge of collecting tx fees
		err := k.bankKeeper.SendCoinsFromModuleToAccountVirtual(ctx, authtypes.FeeCollectorName, msg.From.Bytes(), refundedCoins)
		if err != nil {
			err = errorsmod.Wrapf(errortypes.ErrInsufficientFunds, "fee collector account failed to refund fees: %s", err.Error())
			return errorsmod.Wrapf(err, "failed to refund %d leftover gas (%s)", leftoverGas, refundedCoins.String())
		}
	default:
		// no refund, consume gas and update the tx gas meter
	}

	return nil
}
```

**File:** x/evm/keeper/gas.go (L98-108)
```go
// GasToRefund calculates the amount of gas the state machine should refund to the sender. It is
// capped by the refund quotient value.
// Note: do not pass 0 to refundQuotient
func GasToRefund(availableRefund, gasConsumed, refundQuotient uint64) uint64 {
	// Apply refund counter
	refund := gasConsumed / refundQuotient
	if refund > availableRefund {
		return availableRefund
	}
	return refund
}
```
