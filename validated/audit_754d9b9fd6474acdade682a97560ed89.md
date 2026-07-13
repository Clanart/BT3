### Title
Intrinsic Gas Check Conditionally Skipped in `VerifyFee` During DeliverTx Enables Unrefunded Fee Loss - (File: `x/evm/keeper/utils.go`)

### Summary

`VerifyFee` enforces the `gasLimit < intrinsicGas` guard **only during `CheckTx`**. During `DeliverTx` the guard is silently skipped, the full `gasLimit × effectiveGasPrice` fee is deducted from the sender in the ante handler, and then `ApplyMessageWithConfig` returns a hard error on the same intrinsic-gas check — an error path that never reaches `RefundGas`. The user's fee is permanently lost with no EVM execution having occurred.

### Finding Description

In `VerifyFee` the intrinsic-gas rejection is wrapped in an `isCheckTx` guard:

```go
// intrinsic gas verification during CheckTx
if isCheckTx && gasLimit < intrinsicGas {
    return nil, errorsmod.Wrapf(
        errortypes.ErrOutOfGas,
        "gas limit too low: %d (gas limit) < %d (intrinsic gas)", gasLimit, intrinsicGas,
    )
}
``` [1](#0-0) 

`CheckEthGasConsume` calls `VerifyFee` with `ctx.IsCheckTx()` as the last argument:

```go
fees, err := keeper.VerifyFee(msgEthTx, evmDenom, baseFee, rules, ctx.IsCheckTx())
``` [2](#0-1) 

During `DeliverTx`, `ctx.IsCheckTx()` is `false`, so the guard is never evaluated. `VerifyFee` returns the full `gasLimit × effectiveGasPrice` fee, which is immediately deducted from the sender's balance via `DeductTxCostsFromUserBalance`: [3](#0-2) 

The ante-handler state write is committed before the message handler runs. Inside `ApplyMessageWithConfig`, the same intrinsic-gas check is performed unconditionally:

```go
if leftoverGas < intrinsicGas {
    return nil, errorsmod.Wrap(core.ErrIntrinsicGas, "apply message")
}
``` [4](#0-3) 

This returns a **hard error** (not a `VmError`). `ApplyTransaction` propagates it immediately:

```go
res, err := k.ApplyMessageWithConfig(tmpCtx, msg, cfg, true)
if err != nil {
    return nil, errorsmod.Wrap(err, "failed to apply ethereum core message")
}
``` [5](#0-4) 

Execution never reaches the `RefundGas` call:

```go
if err = k.RefundGas(ctx, msg, leftoverGas, cfg.Params.EvmDenom); err != nil { ...
``` [6](#0-5) 

The full `gasLimit × effectiveGasPrice` fee is permanently retained by the fee-collector module with zero EVM execution performed.

### Impact Explanation

This is a **fee mis-accounting bug**: a user whose transaction satisfies `gasLimit >= intrinsicGas` at `CheckTx` time but violates it at `DeliverTx` time (see Likelihood below) will have their entire upfront fee deducted and never refunded, despite no EVM state transition occurring. This falls squarely within the allowed High impact: *"ante handler, mempool, or proposal handling bug that permits … valid user funds/fees to be mis-accounted."*

The protection mechanism (intrinsic-gas check) exists but is **conditionally applied** — only during mempool admission, not during block execution — directly mirroring the external report's pattern where `availableMarginWithPrice` respected the time-lock only in some code paths.

### Likelihood Explanation

The realistic trigger is a **hard-fork activation** between `CheckTx` and `DeliverTx`. Ethermint fork rules are block-height-gated. A transaction submitted in the mempool one block before a fork (e.g., Shanghai, which raises intrinsic gas for certain calldata patterns) may satisfy `gasLimit >= intrinsicGas` under pre-fork rules at `CheckTx` time, yet violate it under post-fork rules at `DeliverTx` time. The user has no way to detect this race without monitoring the exact activation block. No privileged role is required; any unprivileged user can be affected by submitting a transaction near a fork boundary.

### Recommendation

Remove the `isCheckTx` guard from the intrinsic-gas check in `VerifyFee`. The check should be unconditional:

```go
// intrinsic gas verification (CheckTx and DeliverTx)
if gasLimit < intrinsicGas {
    return nil, errorsmod.Wrapf(
        errortypes.ErrOutOfGas,
        "gas limit too low: %d (gas limit) < %d (intrinsic gas)", gasLimit, intrinsicGas,
    )
}
``` [1](#0-0) 

This ensures that any transaction whose `gasLimit` is below the intrinsic cost is rejected at the ante-handler level in both `CheckTx` and `DeliverTx`, preventing the fee-deduction/no-refund split.

### Proof of Concept

1. Activate a fork (e.g., Shanghai) at block height `N`.
2. At block `N-1`, submit a `LegacyTx` with `gasLimit = intrinsicGas_pre_fork` (satisfies pre-fork rules).
3. The tx passes `CheckTx`: `VerifyFee(isCheckTx=true)` computes `intrinsicGas` under pre-fork rules, `gasLimit >= intrinsicGas` → accepted; fee deducted.
4. The tx is included in block `N` (post-fork). `DeliverTx` runs `VerifyFee(isCheckTx=false)` — the guard is skipped, fee deducted again from the committed ante-handler state.
5. `ApplyMessageWithConfig` computes `intrinsicGas` under post-fork rules (higher); `leftoverGas < intrinsicGas` → returns `ErrIntrinsicGas`.
6. `ApplyTransaction` returns the error; `RefundGas` is never called.
7. The sender's balance is reduced by `gasLimit × effectiveGasPrice` with no EVM execution and no refund.

### Citations

**File:** x/evm/keeper/utils.go (L147-153)
```go
	// intrinsic gas verification during CheckTx
	if isCheckTx && gasLimit < intrinsicGas {
		return nil, errorsmod.Wrapf(
			errortypes.ErrOutOfGas,
			"gas limit too low: %d (gas limit) < %d (intrinsic gas)", gasLimit, intrinsicGas,
		)
	}
```

**File:** ante/eth.go (L170-173)
```go
		fees, err := keeper.VerifyFee(msgEthTx, evmDenom, baseFee, rules, ctx.IsCheckTx())
		if err != nil {
			return ctx, errorsmod.Wrapf(err, "failed to verify the fees")
		}
```

**File:** ante/eth.go (L175-178)
```go
		err = evmKeeper.DeductTxCostsFromUserBalance(ctx, fees, common.BytesToAddress(msgEthTx.From))
		if err != nil {
			return ctx, errorsmod.Wrapf(err, "failed to deduct transaction costs from user balance")
		}
```

**File:** x/evm/keeper/state_transition.go (L194-197)
```go
	res, err := k.ApplyMessageWithConfig(tmpCtx, msg, cfg, true)
	if err != nil {
		return nil, errorsmod.Wrap(err, "failed to apply ethereum core message")
	}
```

**File:** x/evm/keeper/state_transition.go (L252-254)
```go
	if err = k.RefundGas(ctx, msg, leftoverGas, cfg.Params.EvmDenom); err != nil {
		return nil, errorsmod.Wrapf(err, "failed to refund leftover gas to sender %s", msg.From)
	}
```

**File:** x/evm/keeper/state_transition.go (L434-437)
```go
	if leftoverGas < intrinsicGas {
		// eth_estimateGas will check for this exact error
		return nil, errorsmod.Wrap(core.ErrIntrinsicGas, "apply message")
	}
```
