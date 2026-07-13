### Title
Wrong Overflow Bound in `CheckEthGasConsume` Allows Block Gas Limit Bypass via Batch Ethereum Transactions - (File: `ante/eth.go`)

### Summary
`CheckEthGasConsume` uses `math.MaxInt64` (signed 64-bit max, `9223372036854775807`) as the upper bound in a `uint64` overflow guard. When any single message in a batch carries a `gasLimit > math.MaxInt64`, the subtraction `math.MaxInt64 - gasLimit` underflows as unsigned arithmetic, making the guard permanently false. The subsequent `gasWanted += gasLimit` then silently wraps to a small value, causing the block-gas-limit check to pass for a transaction whose true aggregate gas far exceeds the block limit.

### Finding Description
In `ante/eth.go`, `CheckEthGasConsume` accumulates per-message gas limits into `gasWanted uint64` and guards against overflow before each addition:

```go
gasLimit := msgEthTx.GetGas()          // uint64
if gasWanted > math.MaxInt64-gasLimit { // ← wrong bound
    return ctx, fmt.Errorf("gasWanted(%d) + gasLimit(%d) overflow", gasWanted, gasLimit)
}
gasWanted += gasLimit
if gasWanted > blockGasLimit {          // ← bypassed when gasWanted wraps
    return ctx, errorsmod.Wrapf(errortypes.ErrOutOfGas, ...)
}
```

`math.MaxInt64` is `9223372036854775807`; the correct bound for a `uint64` addition overflow check is `math.MaxUint64` (`18446744073709551615`). When `gasLimit > math.MaxInt64`, the expression `math.MaxInt64 - gasLimit` underflows in unsigned arithmetic, producing a very large `uint64` value. The condition `gasWanted > <large value>` is always false, so the guard is silently skipped.

**Concrete exploit path (two-message batch):**

| Step | Value |
|---|---|
| `blockGasLimit` | 30,000,000 |
| Message 1 `gasLimit` | 30,000,000 (passes normally) |
| `gasWanted` after msg 1 | 30,000,000 |
| Message 2 `gasLimit` | `math.MaxUint64 − 30,000,000 + 1 = 18446744073679551616` |
| Overflow guard: `30,000,000 > math.MaxInt64 − 18446744073679551616` | `math.MaxInt64 − 18446744073679551616` underflows → `≈9223372036884775807`; condition is **false** → guard skipped |
| `gasWanted += 18446744073679551616` | `30,000,000 + 18446744073679551616 = 18446744073709551616` → wraps to **0** |
| Block-gas-limit check: `0 > 30,000,000` | **false** → passes |

The batch transaction is admitted by the ante handler. During execution, `AddTransientGasUsed` detects the overflow and returns an error, so the EVM messages fail and state is reverted — but the ante-handler fee deduction is already committed.

### Impact Explanation
When `baseFee = 0` and `minGasPrice = 0` (e.g., chains with fee market disabled, or before `EnableHeight`), `VerifyFee` returns empty coins for a zero-price transaction regardless of gas limit. The attacker pays **zero fees** while forcing a batch transaction through the ante handler that should have been rejected for exceeding the block gas limit. The transaction lands in the block as a failed entry, consuming block space. An attacker can repeat this indefinitely to fill every block with zero-cost failed transactions, halting meaningful throughput — a chain-level DoS. This matches the allowed High impact: *"ante handler … bug that permits invalid transactions to commit."*

### Likelihood Explanation
The attack requires a batch `MsgEthereumTx` (supported by the Cosmos SDK wrapper), a gas limit field set to any value above `math.MaxInt64` (a valid `uint64`), and a chain configuration where effective gas price can be zero. Chains that have not yet activated EIP-1559 (`NoBaseFee = true`) or that set `MinGasPrice = 0` are directly vulnerable. No privileged access, governance action, or key compromise is needed — any unprivileged account can craft and broadcast the transaction.

### Recommendation
Replace `math.MaxInt64` with `math.MaxUint64` in the overflow guard so the check correctly covers the full `uint64` range:

```go
// Before (wrong — uses signed max as bound for unsigned arithmetic)
if gasWanted > math.MaxInt64-gasLimit {

// After (correct — guards against uint64 wrap-around)
if gasWanted > math.MaxUint64-gasLimit {
```

This mirrors the fix pattern from the FullMath report: ensure the arithmetic bound matches the actual type's range.

### Proof of Concept

1. Deploy or connect to an Ethermint chain with `NoBaseFee = true` (or `baseFee = 0`) and `MinGasPrice = 0`.
2. Craft a Cosmos transaction containing **two** `MsgEthereumTx` messages from the same sender:
   - **Msg 1**: any valid Ethereum tx with `gasLimit = blockGasLimit` (e.g., 30,000,000).
   - **Msg 2**: an Ethereum tx with `gasLimit = math.MaxUint64 − blockGasLimit + 1`, `gasPrice = 0`, `value = 0`, `data = nil`.
3. Broadcast the transaction.
4. Observe: the ante handler in `CheckEthGasConsume` passes both messages (overflow guard is bypassed for Msg 2; `gasWanted` wraps to 0; block-gas-limit check passes). The transaction is included in the block. Msg 2 fails during EVM execution (overflow detected in `AddTransientGasUsed`), but the ante-handler fee deduction of 0 is already committed.
5. Repeat to fill every block with zero-cost failed transactions.

**Root cause lines:** [1](#0-0) 

**Correct uint64 overflow helper for reference:** [2](#0-1) 

**`AddTransientGasUsed` overflow detection (catches the issue only after ante handler admission):** [3](#0-2)

### Citations

**File:** ante/eth.go (L151-163)
```go
		gasLimit := msgEthTx.GetGas()
		if gasWanted > math.MaxInt64-gasLimit {
			return ctx, fmt.Errorf("gasWanted(%d) + gasLimit(%d) overflow", gasWanted, gasLimit)
		}
		gasWanted += gasLimit
		if gasWanted > blockGasLimit {
			return ctx, errorsmod.Wrapf(
				errortypes.ErrOutOfGas,
				"tx gas (%d) exceeds block gas limit (%d)",
				gasWanted,
				blockGasLimit,
			)
		}
```

**File:** types/gasmeter.go (L63-71)
```go
// addUint64Overflow performs the addition operation on two uint64 integers and
// returns a boolean on whether or not the result overflows.
func addUint64Overflow(a, b uint64) (uint64, bool) {
	if math.MaxUint64-a < b {
		return 0, true
	}

	return a + b, false
}
```

**File:** x/evm/keeper/keeper.go (L339-346)
```go
func (k Keeper) AddTransientGasUsed(ctx sdk.Context, gasUsed uint64) (uint64, error) {
	result := k.GetTransientGasUsed(ctx) + gasUsed
	if result < gasUsed {
		return 0, errorsmod.Wrap(types.ErrGasOverflow, "transient gas used")
	}
	k.SetTransientGasUsed(ctx, result)
	return result, nil
}
```
