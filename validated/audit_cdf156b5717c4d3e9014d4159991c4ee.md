Based on my investigation, I found a valid analog in the `kaiax/gasless` module.

### Title
GaslessSwapTx deadline is validated only at mempool admission, not re-checked at block-building/promotion time, allowing execution of stale swaps at outdated prices - (File: kaiax/gasless/impl/tx_pool.go)

### Summary
The Teller `submitBid()` bug is that a bid, once submitted, is treated as valid at execution time with no re-validation of a caller-supplied expiration/deadline against the actual execution time, so it can execute long after being submitted at stale market prices. In Kaia's `kaiax/gasless` module, the analogous `GaslessSwapTx.deadline` field is checked only once, at the moment the transaction is first added to the tx pool via `GetCheckBalance()`/`checkBalanceForSwap()`, and this deadline is never re-validated when the transaction is later actually promoted to pending and included/executed in a block.

### Finding Description
`checkBalanceForSwap` performs the deadline check `deadline.Cmp(g.Chain.CurrentBlock().Time()) < 0` [1](#0-0)  This function is invoked through `GetCheckBalance()`, whose interface documentation explicitly states it is "mainly used on checking if module transaction be appended to queue" [2](#0-1)  — i.e., a one-time admission check, not a continuously re-verified condition.

The transaction's actual readiness/promotion path (`IsReady` → `isSwapTxReady`/`isApproveTxReady` → `IsExecutable`/`VerifyExecutable`) never re-checks the deadline: `VerifyExecutable` validates sender consistency, token consistency, approve amount, nonce ordering, and repay amount, but contains no deadline check at all [3](#0-2) . Likewise, block building via `ExtractTxBundles`/`GetLendTxGenerator` in `kaiax/gasless/impl/builder.go` only calls `IsExecutable` (which internally delegates to the same `VerifyExecutable` with no deadline check) before bundling the swap for execution [4](#0-3) .

A `GaslessSwapTx` can only be promoted once nonce-sequencing conditions are satisfied (e.g., waiting for a preceding `GaslessApproveTx` at the correct nonce), and it can remain queued for up to `QueueTimeout`/re-added across pool resets before being dropped by `PreReset` [5](#0-4) . Because the deadline is a user-declared value baked into the RLP-encoded, signed transaction (not the block timestamp at inclusion), and because the on-chain `GaslessSwapRouter.swapForGas(token, amountIn, minAmountOut, amountRepay, deadline)` contract is external to this repository (only bindings, not Solidity source, are present) [6](#0-5) , there is no visible on-chain (state-transition-level) enforcement of `deadline` within the scope of this repository — the only enforcement found is the one-time, off-chain, mempool-admission check in `tx_pool.go`.

### Impact Explanation
If a swap transaction is admitted to the pool while `deadline >= currentBlock.Time()`, but is only promoted and executed in a later block (after waiting on an approve tx, being requeued across a chain reorg/`PostReset`, or otherwise delayed within the pool's timeout windows), it can be executed with stale `minAmountOut`/`amountRepay`/`amountIn` parameters that no longer reflect current market conditions, exactly mirroring the Teller `submitBid` issue: the borrower/swapper set numbers that were fair "at submission time" but are executed later at worse (or exploitable) prices, potentially causing the user to receive less output than expected or overpay in repay amount relative to the gas advanced by the proposer.

### Likelihood Explanation
Medium likelihood: the pool timeout windows (`QueueTimeout`/`PendingTimeout` = 10s) are short under normal conditions [7](#0-6) , limiting typical staleness. However, no mechanism prevents a swap that passed the deadline check pre-reorg from being reintroduced and executed post-reorg without recheck, and there's no invariant tying the deadline check to the actual block in which the tx executes — the check only compares against the chain head at admission time, not at inclusion time.

### Recommendation
Re-validate `swapArgs.Deadline` against the current block's timestamp inside `VerifyExecutable`/`IsExecutable` (called immediately before bundling in `ExtractTxBundles`) so that the check is performed against the block actually being built, not just at pool-admission time. Alternatively/additionally, ensure `GaslessSwapRouter.swapForGas` performs this check on-chain during EVM execution so that state-transition validity does not depend solely on the off-chain mempool module.

### Proof of Concept
1. A gasless swapper submits a `GaslessApproveTx` (nonce N) and `GaslessSwapTx` (nonce N+1) with `deadline = currentBlock.Time() + 5`.
2. `checkBalanceForSwap` passes at admission because `deadline >= currentBlock.Time()` [1](#0-0) .
3. Due to network conditions, chain reorg, or pool churn, the `ApproveTx`/`SwapTx` pair is not promoted immediately; it is requeued via `PostReset`/`PreReset` handling.
4. Several blocks later (beyond the original deadline), the pair becomes nonce-ready and `IsExecutable`/`VerifyExecutable` is called, which performs no deadline check [3](#0-2) , and `ExtractTxBundles` bundles and executes it [4](#0-3) .
5. The swap executes with stale-but-nominally-valid `amountIn`/`minAmountOut`/`amountRepay` values well past the user's intended deadline.

### Citations

**File:** kaiax/gasless/impl/tx_pool.go (L33-35)
```go
	QueueTimeout   = 10 * time.Second // bundle tx is removed from txpool.queue after QueueTimeout
	PendingTimeout = 10 * time.Second // bundle tx is removed from txpool.pending after PendingTimeout
	KnownTxTimeout = 30 * time.Second // bundle tx is removed from knownTxs after KnownTxTimeout
```

**File:** kaiax/gasless/impl/tx_pool.go (L175-179)
```go
	// tx.deadline >= currentTimestamp
	deadline := swapArgs.Deadline
	if deadline.Cmp(g.Chain.CurrentBlock().Time()) < 0 {
		return fmt.Errorf("insufficient deadline: deadline=%s, want=%s", deadline.String(), g.Chain.CurrentBlock().Time().String())
	}
```

**File:** kaiax/gasless/impl/tx_pool.go (L292-315)
```go
// PreReset removes timed out tx from the tx pool and knownTxs.
func (g *GaslessModule) PreReset(oldHead, newHead *types.Header) []common.Hash {
	g.knownTxsMu.Lock()
	defer g.knownTxsMu.Unlock()

	drops := make([]common.Hash, 0)

	for hash, knownTx := range *g.knownTxs {
		// remove pending timed out tx from tx pool
		if knownTx.status == TxStatusPending && knownTx.elapsedPromotedTime() >= PendingTimeout {
			drops = append(drops, hash)
		}
		// remove queue timed out tx from tx pool
		if knownTx.status == TxStatusQueue && knownTx.elapsedAddedTime() >= QueueTimeout {
			drops = append(drops, hash)
		}
		// remove known timed out tx from knownTxs
		if knownTx.elapsedPromotedOrAddedTime() >= KnownTxTimeout {
			g.knownTxs.delete(hash)
		}
	}

	return drops
}
```

**File:** kaiax/interface.go (L158-161)
```go
	// Optional actions to check if sender balance is valid for module transaction.
	// This is mainly used on checking if module transaction be appended to queue.
	// If nil is returned, default check (balance > txFee) is performed. Otherwise, the returned function overrides default check.
	GetCheckBalance() func(tx *types.Transaction) error
```

**File:** kaiax/gasless/impl/getter.go (L211-266)
```go
// VerifyExecutable checks if the given transactions form a valid gasless transaction
// It returns an error explaining why the transaction is not executable if it's not,
// and a boolean indicating whether the transaction is executable
func (g *GaslessModule) VerifyExecutable(approveTxOrNil, swapTx *types.Transaction) error {
	// Sx.
	swapArgs, ok := decodeSwapTx(swapTx, g.signer)
	if !ok {
		return ErrDecodeSwapTx
	}
	if !g.isSwapTx(swapArgs) {
		return ErrSwapTxInvalid
	}

	// Conditions involving ApproveTx
	if approveTxOrNil != nil {
		// Ax.
		approveArgs, ok := decodeApproveTx(approveTxOrNil, g.signer)
		if !ok {
			return ErrDecodeApproveTx
		}
		if !g.isApproveTx(approveArgs) {
			return ErrApproveTxInvalid
		}
		// AP1.
		if approveArgs.Sender != swapArgs.Sender {
			return ErrDifferentSenders
		}
		// SP1.
		if approveArgs.Token != swapArgs.Token {
			return fmt.Errorf("%w: approve token %s, swap token %s", ErrDifferentTokens, approveArgs.Token.Hex(), swapArgs.Token.Hex())
		}
		// SP2.
		if approveArgs.Amount.Cmp(swapArgs.AmountIn) < 0 {
			return fmt.Errorf("%w: approve amount %s, required amount %s", ErrInsufficientApproveAmount, approveArgs.Amount.String(), swapArgs.AmountIn.String())
		}
		// SP3.
		if approveTxOrNil.Nonce()+1 != swapTx.Nonce() {
			return fmt.Errorf("%w: approve nonce %d, swap nonce %d (expected %d)", ErrNonSequentialNonce, approveTxOrNil.Nonce(), swapTx.Nonce(), approveTxOrNil.Nonce()+1)
		}
		if nonce := g.getCurrentStateNonce(approveArgs.Sender); nonce != approveTxOrNil.Nonce() {
			return fmt.Errorf("%w: approve nonce %d, current nonce %d", ErrApproveNonceNotCurrent, approveTxOrNil.Nonce(), nonce)
		}
	} else {
		// SP3.
		if nonce := g.getCurrentStateNonce(swapArgs.Sender); nonce != swapTx.Nonce() {
			return fmt.Errorf("%w: swap nonce %d, current nonce %d", ErrSwapNonceNotCurrent, swapTx.Nonce(), nonce)
		}
	}

	// SP4.
	if swapArgs.AmountRepay.Cmp(repayAmount(approveTxOrNil, swapTx)) != 0 {
		return fmt.Errorf("%w: got %s, expected %s", ErrIncorrectRepayAmount, swapArgs.AmountRepay.String(), repayAmount(approveTxOrNil, swapTx).String())
	}

	return nil
}
```

**File:** kaiax/gasless/impl/builder.go (L28-46)
```go
func (g *GaslessModule) ExtractTxBundles(txs []*types.Transaction, prevBundles []*builder.Bundle) []*builder.Bundle {
	// there are only at most two gasless transactions in pending for a sender
	bundles := []*builder.Bundle{}
	approveTxs := map[common.Address]*types.Transaction{}
	targetTxHash := common.Hash{}
	for _, tx := range txs {
		addr, err := types.Sender(g.signer, tx)
		if err != nil {
			continue
		}
		if g.IsApproveTx(tx) {
			approveTxs[addr] = tx
		} else if g.IsSwapTx(tx) && g.IsExecutable(approveTxs[addr], tx) {
			bundleTxs := builder.NewTxOrGenList(g.GetLendTxGenerator(approveTxs[addr], tx))
			if approveTxs[addr] != nil {
				bundleTxs = append(bundleTxs, builder.NewTxOrGenFromTx(approveTxs[addr]))
			}
			bundleTxs = append(bundleTxs, builder.NewTxOrGenFromTx(tx))

```

**File:** contracts/bindings/kip247/GaslessSwapRouter.go (L554-559)
```go
// SwapForGas is a paid mutator transaction binding the contract method 0x80426901.
//
// Solidity: function swapForGas(address token, uint256 amountIn, uint256 minAmountOut, uint256 amountRepay, uint256 deadline) returns()
func (_GaslessSwapRouter *GaslessSwapRouterTransactor) SwapForGas(opts *bind.TransactOpts, token common.Address, amountIn *big.Int, minAmountOut *big.Int, amountRepay *big.Int, deadline *big.Int) (*types.Transaction, error) {
	return _GaslessSwapRouter.contract.Transact(opts, "swapForGas", token, amountIn, minAmountOut, amountRepay, deadline)
}
```
