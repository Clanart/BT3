### Title
Gas Refund Credited to Sender Even When EVM State Is Reverted Due to Post-Hook Failure — (`x/evm/keeper/state_transition.go`)

### Summary

In `ApplyTransaction`, when EVM hooks (`PostTxProcessing`) fail, the EVM state changes are discarded (the `tmpCtx` cache is not committed), but the gas refund is unconditionally sent back to the sender on the **original `ctx`** regardless of whether the EVM execution state was committed. This means a sender whose transaction's EVM state was fully reverted (due to a post-hook failure) still receives a gas refund — a real token credit — from the fee collector module account, without any corresponding committed EVM state change.

### Finding Description

In `ApplyTransaction` (`x/evm/keeper/state_transition.go`), when hooks are enabled, the EVM execution runs inside a `tmpCtx` cache context:

```go
if k.hooks != nil {
    tmpCtx, commit = ctx.CacheContext()
    ...
}
res, err := k.ApplyMessageWithConfig(tmpCtx, msg, cfg, true)
```

If `PostTxProcessing` fails, `commit()` is never called, so all EVM state changes (balance transfers, storage writes, contract deployments) are discarded:

```go
if err = k.PostTxProcessing(tmpCtx, msg, receipt); err != nil {
    res.VmError = types.ErrPostTxProcessing.Error()
    // commit() is NOT called — tmpCtx changes are discarded
}
```

However, the gas refund is then unconditionally applied to the **original `ctx`** (not `tmpCtx`):

```go
leftoverGas := msg.GasLimit - res.GasUsed
if err = k.RefundGas(ctx, msg, leftoverGas, cfg.Params.EvmDenom); err != nil {
    return nil, errorsmod.Wrapf(err, "failed to refund leftover gas to sender %s", msg.From)
}
```

`RefundGas` calls `bankKeeper.SendCoinsFromModuleToAccountVirtual`, which transfers real EVM-denom tokens from the fee collector to the sender's bank account on the **parent context** (`ctx`). This write is not inside any cache context and is therefore permanently committed to state.

The result is:

1. The ante handler deducts `gasLimit * gasPrice` from the sender's balance (on `ctx`).
2. The EVM executes, consuming some gas. The EVM state changes go into `tmpCtx`.
3. `PostTxProcessing` hook fails → `tmpCtx` is discarded. EVM state changes (e.g., value transfers to recipients, storage writes) are **reverted**.
4. `RefundGas` credits `leftoverGas * gasPrice` back to the sender on `ctx` — this **commits permanently**.

The net effect: the sender pays only `gasUsed * gasPrice` in fees (correct), but the EVM state changes that consumed that gas are **not committed**. The sender's balance is partially restored without the corresponding EVM execution effects persisting. This is an accounting inconsistency: the fee collector loses tokens (via refund) without the EVM execution that justified the partial fee being committed to state.

More critically, if the EVM execution included a value transfer (e.g., sending ETH to a recipient), the recipient's balance increase is reverted (in `tmpCtx`), but the sender's fee refund still lands. The sender effectively gets a partial refund for a transaction whose effects were entirely discarded.

### Impact Explanation

This is a **High** severity EVM state transition / fee accounting bug. The gas refund path (`RefundGas` on `ctx`) and the EVM state commit path (`commit()` on `tmpCtx`) are decoupled. When a post-hook failure causes EVM state reversion, the gas refund still executes on the parent context, creating a permanent token credit from the fee collector to the sender for a transaction whose EVM effects were discarded.

This matches the allowed impact: **"EVM state transition, gas refund, fee market, ante handler, mempool, or proposal handling bug that permits invalid transactions to commit or valid user funds/fees to be mis-accounted."**

Any chain deploying Ethermint with EVM hooks (e.g., IBC hooks, staking hooks, or custom post-processing hooks) is affected. An attacker who can reliably trigger hook failures (e.g., by crafting transactions that succeed in EVM execution but cause a hook to revert) can repeatedly extract partial gas refunds while having their EVM state changes reverted, draining the fee collector module account.

### Likelihood Explanation

The condition requires:
1. EVM hooks to be registered (`k.hooks != nil`) — common in production deployments (e.g., Evmos uses IBC transfer hooks).
2. A transaction that succeeds in EVM execution (`!res.Failed()`) but causes `PostTxProcessing` to return an error.

Hook failures can be triggered by crafting transactions that call specific contracts or emit specific events that the hook processes and rejects. This is an unprivileged, attacker-controlled path via a standard `eth_sendRawTransaction` submission.

### Recommendation

Move `RefundGas` inside the committed context path, or apply it only when the EVM state is actually committed. Specifically:

- When hooks are enabled and `PostTxProcessing` fails (so `commit()` is not called), `RefundGas` should either be skipped or applied to `tmpCtx` (which is then discarded), not to `ctx`.
- The simplest fix: call `RefundGas` on `tmpCtx` instead of `ctx`, so the refund is only persisted when `commit()` is called.

### Proof of Concept

1. Deploy a contract on an Ethermint chain with EVM hooks registered.
2. Craft a transaction that:
   - Succeeds in EVM execution (e.g., a simple ETH transfer or contract call).
   - Causes `PostTxProcessing` to return an error (e.g., by triggering a hook condition that the hook rejects).
3. Submit the transaction via `eth_sendRawTransaction`.
4. Observe:
   - The EVM state changes (e.g., recipient balance increase) are **not** committed.
   - The sender's balance is credited with `leftoverGas * gasPrice` from the fee collector (the gas refund), permanently on `ctx`.
   - The fee collector's balance decreases by the refund amount without a corresponding committed EVM execution.

The root cause is at: [1](#0-0) 

Specifically, `RefundGas(ctx, ...)` at line 252 uses the parent `ctx`, while the EVM state commit via `commit()` at line 241 is conditional on `PostTxProcessing` succeeding. When the hook fails (line 232–238), `commit()` is never called but `RefundGas` still executes on `ctx`. [2](#0-1) 

The `RefundGasWithPrice` function sends coins from the fee collector to the sender via `bankKeeper.SendCoinsFromModuleToAccountVirtual`, which writes directly to the bank module's KV store on whatever context is passed — in this case the uncommitted parent `ctx`.

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
