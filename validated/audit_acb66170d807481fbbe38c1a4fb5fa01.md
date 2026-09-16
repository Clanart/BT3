## Title
Gasless swap deadline check uses the last committed block's timestamp instead of the block being built, allowing execution-time divergence for `deadline`-boundary swaps - ([File: kaiax/gasless/impl/tx_pool.go])

### Summary
`GaslessModule.checkBalanceForSwap` — the tx-pool admission check for gasless swap transactions — validates `tx.deadline` against `g.Chain.CurrentBlock().Time()`, i.e. the timestamp of the **last already-inserted** block, not the timestamp of the block the transaction will actually be executed in (`currentBlock+1`). This mirrors the class of bug in the reported issue: two different code paths use two different "current time" references (`>=`/`<` on stale vs. live time) to decide whether a time-bounded operation is still valid, producing a validity mismatch between admission-time and execution-time checks.

### Finding Description
`checkBalanceForSwap` performs:
```go
// tx.deadline >= currentTimestamp
deadline := swapArgs.Deadline
if deadline.Cmp(g.Chain.CurrentBlock().Time()) < 0 {
    return fmt.Errorf("insufficient deadline: ...")
}
``` [1](#0-0) 

This function is invoked as `GetCheckBalance()` during tx-pool admission (`PreAddTx`/`IsModuleTx` pipeline) [2](#0-1)  — i.e. it is evaluated against `Chain.CurrentBlock()`, the head block that has already been committed, well before the transaction is actually included and executed in the *next* block. Block timestamps strictly increase, so the block in which the swap ultimately executes will always have `block.timestamp > CurrentBlock().Time()` at admission time.

This creates the same category of discrepancy as the reported Gitcoin issue: one code path (tx-pool admission) treats the round/deadline as "still valid" using a stale, smaller timestamp reference, while the actual execution context (the produced block, and presumably the on-chain `GaslessSwapRouter.swapForGas` deadline check executed with the real `block.timestamp`) uses a later, larger timestamp. A swap transaction whose `deadline` equals or is only slightly greater than the current head's timestamp can pass the tx-pool's admission check yet be built into a block whose `block.timestamp` has advanced past `deadline`, causing the on-chain `swapForGas` call to revert at execution time even though the pool accepted and promoted it as valid, wasting the lending flow (`LendTxGenerator` prepends a proposer-funded lend transaction ahead of the swap) and gas.

### Impact Explanation
Because the gasless flow requires the block proposer to first fund the sender via a `LendTxGenerator`-prepended transaction before the `GaslessApproveTx`/`GaslessSwapTx` bundle executes [3](#0-2) , an admission-time-valid-but-execution-time-invalid swap causes the proposer's lend transaction to be spent while the paired swap reverts on the stale/boundary deadline, since the sender's balance check is intentionally skipped for gasless senders (`GetCheckBalance()` bypasses normal balance checks) [4](#0-3) . This can result in proposer-funded gas being lent without full repayment/settlement succeeding in the same bundle for transactions sitting exactly at the deadline boundary, which is a fee-delegation/gasless settlement correctness issue.

### Likelihood Explanation
Any unprivileged sender submitting a gasless swap transaction via public RPC with a `deadline` set to (or very close to) the current chain head's timestamp will reliably reach this path — no special privileges, node access, or timing race beyond normal block-interval timestamp advance is required, since Kaia produces blocks continuously and the CurrentBlock() reference used in the check is always at least one block interval behind the eventual execution block.

### Recommendation
Align the tx-pool deadline check with the timestamp of the block the transaction will actually be executed in (e.g., use an estimate of the next block's timestamp, or apply the same margin/strictness that the on-chain `swapForGas` deadline check uses), and use consistent comparison semantics (`>` vs `>=`) between the pool-level admission check and the on-chain execution check so a transaction accepted by the pool cannot be rejected on-chain purely due to timestamp advancement between admission and inclusion.

### Proof of Concept
1. Attacker/user determines the current head block's timestamp `T` via `Chain.CurrentBlock().Time()` (observable via any RPC).
2. Submits `SwapForGas(token, amountIn, minAmountOut, amountRepay, deadline=T)` as a gasless swap tx.
3. `checkBalanceForSwap` computes `deadline.Cmp(CurrentBlock().Time()) < 0` → `T.Cmp(T) == 0`, not `< 0`, so the check passes and the tx is admitted/promoted [1](#0-0) .
4. The block builder assembles the bundle `[LendTxGenerator, GaslessApproveTx, GaslessSwapTx]` into the next block, whose `block.timestamp` is `> T` [5](#0-4) .
5. If the on-chain `GaslessSwapRouter.swapForGas` deadline check is strict (`deadline >= block.timestamp` or similar) with the now-greater timestamp, the swap reverts on-chain despite the lend transaction having already succeeded, illustrating the discrepancy between the pool-level "still valid" determination and the execution-level determination.

Note: I was unable to locate the actual `GaslessSwapRouter.sol` source in the indexed codebase (only the generated Go bindings in `contracts/bindings/kip247/GaslessSwapRouter.go` were found) [6](#0-5) , so the exact on-chain comparison operator for `deadline` could not be directly confirmed from the index; this may be excluded from indexing due to size limits. Confirming the precise on-chain check would require a full repository session (e.g., via a Devin run) rather than the ask-only index.

### Citations

**File:** kaiax/gasless/impl/tx_pool.go (L62-72)
```go
func (g *GaslessModule) GetCheckBalance() func(tx *types.Transaction) error {
	return func(tx *types.Transaction) error {
		if approveArgs, ok := decodeApproveTx(tx, g.signer); ok {
			return g.checkBalanceForApprove(approveArgs)
		}
		if swapArgs, ok := decodeSwapTx(tx, g.signer); ok {
			return g.checkBalanceForSwap(swapArgs, tx.Nonce())
		}
		return errors.New("not a gasless transaction") // should not happen because IsModuleTx is called before GetCheckBalance
	}
}
```

**File:** kaiax/gasless/impl/tx_pool.go (L175-179)
```go
	// tx.deadline >= currentTimestamp
	deadline := swapArgs.Deadline
	if deadline.Cmp(g.Chain.CurrentBlock().Time()) < 0 {
		return fmt.Errorf("insufficient deadline: deadline=%s, want=%s", deadline.String(), g.Chain.CurrentBlock().Time().String())
	}
```

**File:** kaiax/gasless/README.md (L25-27)
```markdown
#### Balance check

Sender balance check is omitted for gasless transactions (see `GetCheckBalance()`).
```

**File:** kaiax/gasless/README.md (L29-36)
```markdown
### Block building rules

Upon detection of GaslessTxs, the following logics are executed:

- Per sender, if exists, GaslessApproveTx is relocated before GaslessSwapTx.
- LendTxGenerator is prepended before GaslessApproveTx.
- A new bundle is generated which contain either `[LendTxGenerator, GaslessApproveTx, GaslessSwapTx]` or `[LendTxGenerator, GaslessSwapTx]`
- If the bundle has conflict with any previous bundles, it is excluded from the returned bundle list.
```

**File:** contracts/bindings/kip247/GaslessSwapRouter.go (L568-573)
```go
// SwapForGas is a paid mutator transaction binding the contract method 0x80426901.
//
// Solidity: function swapForGas(address token, uint256 amountIn, uint256 minAmountOut, uint256 amountRepay, uint256 deadline) returns()
func (_GaslessSwapRouter *GaslessSwapRouterTransactorSession) SwapForGas(token common.Address, amountIn *big.Int, minAmountOut *big.Int, amountRepay *big.Int, deadline *big.Int) (*types.Transaction, error) {
	return _GaslessSwapRouter.Contract.SwapForGas(&_GaslessSwapRouter.TransactOpts, token, amountIn, minAmountOut, amountRepay, deadline)
}
```
