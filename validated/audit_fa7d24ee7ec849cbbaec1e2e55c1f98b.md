### Title
Gasless swap deadline enforced against stale `CurrentBlock().Time()` instead of the transaction's actual execution timestamp - (File: kaiax/gasless/impl/tx_pool.go)

### Summary
`checkBalanceForSwap` in the gasless module validates a swap's `deadline` field by comparing it against `g.Chain.CurrentBlock().Time()`, i.e., the timestamp of the last mined block at the moment the check runs, rather than the timestamp of the block in which the swap transaction will actually be included and executed. This is the same bug class as the reported `UsdTokenSwapKeeper.checkLog` issue: a time-sensitive admission/pricing decision is made using the wrong timestamp reference point (the checker's "now" instead of the transaction's actual effective time), which can desynchronize the validation from the real execution context.

### Finding Description
`checkBalanceForSwap` performs the deadline check as: [1](#0-0) 

This mirrors the logic pattern flagged in the external report: a value that should represent "the time the request will actually be settled/executed" is instead read as "the time the check is being performed," using a *stale* reference (the last sealed block's timestamp) rather than the timestamp of the block the transaction will be placed in. Because Kaia block times advance and mempool checks (`GetCheckBalance`) may run well before the transaction is actually promoted, sequenced, and included in a future block, `CurrentBlock().Time()` can differ meaningfully from the real settlement time, exactly as `block.timestamp` differed from `log.timestamp` in the original report.

This check is the *only* protocol-level enforcement point for the swap deadline; there is no on-chain Solidity check reproducing `deadline` validation independently at execution time in the reachable gasless swap-router contracts bundled in this repo (no `deadline` references found in the `.sol` sources), so the mempool-level comparison is relied upon as the actual gate.

### Impact Explanation
Because the deadline gate uses the previous block's timestamp rather than the timestamp of the block that will actually execute the swap, a bundle can be admitted, sequenced, and executed at a materially later wall-clock time than the check implied, letting the effective swap settle using an execution context (state, exchange rate window at execution) beyond what the user intended when setting `deadline`. Conversely, a transaction that would still be legitimately fresh in the eventual execution block may be prematurely rejected at admission. Either direction breaks the deadline guarantee gasless users rely on, in the same "wrong reference timestamp" spirit as the reported bug, affecting fairness of gasless swap settlement (state divergence in accepted/rejected sets and settlement at unintended prices/timing) — reachable purely by any unprivileged gasless-swap transaction sender.

### Likelihood Explanation
Every gasless swap transaction routes through this exact check via `GetCheckBalance` (`checkBalanceForSwap`) during tx-pool admission, so the flawed timestamp reference is exercised on every gasless swap without any special privilege — any public transaction sender can trigger it. The magnitude of the discrepancy grows with mempool/queueing delay (bounded by constants such as `QueueTimeout`/`PendingTimeout` of 10s each), so the drift is bounded but non-zero and increases under network congestion or when bundles wait behind other bundle txs.

### Recommendation
Validate `deadline` against the timestamp of the block that will actually include/execute the transaction (e.g., the anticipated next-block time, or defer/re-check the deadline at block-assembly/execution time) rather than `g.Chain.CurrentBlock().Time()` taken at admission time, so the enforced time reference matches the transaction's actual settlement time — analogous to using `log.timestamp` (the event's real timestamp) instead of `block.timestamp` (the checker's current time) in the original report.

### Proof of Concept
1. A user submits a gasless swap transaction (`SwapArgs.Deadline`) that is valid relative to the current chain head when it enters the pool.
2. `PreAddTx`/`GetCheckBalance` calls `checkBalanceForSwap`, which compares `deadline` to `g.Chain.CurrentBlock().Time()` (the last sealed block's time), not the time of the block that will eventually include the tx: [2](#0-1) 
3. If the transaction sits in the bundle queue/pending pool for up to `QueueTimeout`/`PendingTimeout` (10s each) before being sequenced (`kaiax/gasless/impl/tx_pool.go:33-35`), the block that ultimately executes the swap can have a timestamp beyond `deadline`, even though the original admission check passed — the deadline guarantee is not actually enforced against the real execution time.

### Citations

**File:** kaiax/gasless/impl/tx_pool.go (L102-107)
```go
// tx.minAmountOut >= tx.amountRepay
// tx.amountIn >= gsr.getAmountIn(minAmountOut)
// tx.token.approval(sender, router) >= tx.amountIn
// tx.token.balanceOf(sender) >= tx.amountIn
// tx.deadline >= currentTimestamp
func (g *GaslessModule) checkBalanceForSwap(swapArgs *SwapArgs, swapNonce uint64) error {
```

**File:** kaiax/gasless/impl/tx_pool.go (L175-179)
```go
	// tx.deadline >= currentTimestamp
	deadline := swapArgs.Deadline
	if deadline.Cmp(g.Chain.CurrentBlock().Time()) < 0 {
		return fmt.Errorf("insufficient deadline: deadline=%s, want=%s", deadline.String(), g.Chain.CurrentBlock().Time().String())
	}
```
