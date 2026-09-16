### Title
Gasless swap deadline is validated only at tx-pool admission, not at promotion/bundling time, allowing execution of expired swaps that fail to repay lent gas - ([File: kaiax/gasless/impl/getter.go])

### Summary
The gasless module (`kaiax/gasless`) validates a `GaslessSwapTx`'s `deadline` field only when the transaction is first admitted to the transaction pool, via `checkBalanceForSwap`. This check is never repeated when the transaction is later promoted from `queue` to `pending`, nor when it is actually selected for block building. As a result, a swap transaction whose `deadline` has since expired can still be bundled with a `LendTxGenerator` transaction and included in a block by the proposer.

### Finding Description
`GaslessModule.checkBalanceForSwap` explicitly enforces `tx.deadline >= currentTimestamp` at admission time: [1](#0-0) 

However, this function is only wired to `GetCheckBalance()`, which is invoked when a transaction is first added to the pool: [2](#0-1) 

The re-validation path used later for promotion decisions (`IsReady` → `isReady` → `isApproveTxReady`/`isSwapTxReady`) and for block-building bundling (`ExtractTxBundles`) both call `IsExecutable`/`VerifyExecutable`, which never checks `deadline` at all: [3](#0-2) [4](#0-3) [5](#0-4) 

A `GaslessSwapTx` can legitimately sit in `txpool.queue` for an extended period (its promotion depends on a preceding `GaslessApproveTx` becoming ready, on `QueueTimeout`, or on repeated `PreReset`/`PostReset` cycles) while the chain's timestamp advances past the transaction's declared `deadline`. Because `VerifyExecutable` omits the deadline check, the module will still consider the swap "executable," bundle it with a `LendTxGenerator` transaction (which unconditionally transfers lent gas value to the sender), and hand this bundle to the block builder: [6](#0-5) 

### Impact Explanation
The `LendTxGenerator` transaction sends the lent gas amount to the sender unconditionally as the first transaction of the bundle, expecting the following `GaslessSwapTx` to repay that amount from the swap proceeds. If the swap's `deadline` has expired by execution time and the underlying `GaslessSwapRouter` contract enforces `deadline >= block.timestamp` on-chain (mirroring the same field/semantics validated off-chain), the swap call reverts and the repayment never occurs, while the lend transfer has already succeeded. This produces an unrecovered loss of lent gas funds for the block proposer/fee-delegation counterparty — a concrete value-movement/fee-delegation-abuse impact that is explicitly in scope.

### Likelihood Explanation
Likelihood is moderate: it requires a `GaslessSwapTx` (optionally paired with a `GaslessApproveTx`) to remain queued/pending long enough for its `deadline` to elapse before promotion/bundling, which is plausible under network congestion, sender-nonce ordering delays (waiting for a preceding approve tx), or simply a short deadline chosen relative to actual inclusion time. No attacker sophistication is required beyond submitting an otherwise-valid gasless tx pair with a short deadline and letting normal pool timing elapse; only the correction that the admission-time check exists but is not repeated is needed to trigger the divergence.

### Recommendation
Re-validate `swapArgs.Deadline` against `g.Chain.CurrentBlock().Time()` (or the timestamp of the block being built) inside `VerifyExecutable`, so that both `IsReady` (promotion) and `ExtractTxBundles` (block building) consistently reject stale gasless swaps, matching the check already performed in `checkBalanceForSwap` at admission time.

### Proof of Concept
1. Sender submits `GaslessApproveTx` (nonce N) and `GaslessSwapTx` (nonce N+1) with `deadline = currentBlockTime + 5`.
2. Both pass `checkBalanceForSwap`/`checkBalanceForApprove` at admission and enter `txpool.queue`/`pending` per normal promotion rules in `tx_pool.go`.
3. Due to promotion timing (e.g., waiting on other txs, `PreReset`/`PostReset` cycles, or `QueueTimeout`/`PendingTimeout` windows), more than 5 seconds elapse before the block proposer calls `ExtractTxBundles`.
4. `ExtractTxBundles` calls `g.IsExecutable(approveTx, swapTx)` → `VerifyExecutable`, which does not check `Deadline`, so the bundle `[LendTxGenerator, ApproveTx, SwapTx]` is still produced and included in the block.
5. On execution, the `LendTxGenerator` transaction sends lent gas value to the sender; the `SwapTx` call to `swapForGas` reverts on-chain due to the expired deadline (assuming the router contract enforces it), leaving the lent amount unrepaid — a direct loss for the block proposer.

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

**File:** kaiax/gasless/impl/tx_pool.go (L269-290)
```go
// isSwapTxReady assumes that the caller checked `g.IsSwapTx(swapTx)`
func (g *GaslessModule) isSwapTxReady(swapTx, prevTx *types.Transaction) bool {
	addr, err := types.Sender(g.signer, swapTx)
	if err != nil {
		return false
	}
	nonce := g.getCurrentStateNonce(addr)

	var approveTx *types.Transaction
	if swapTx.Nonce() == nonce {
		approveTx = nil
	} else if swapTx.Nonce() == nonce+1 {
		if prevTx == nil || !g.IsApproveTx(prevTx) {
			return false
		}
		approveTx = prevTx
	} else {
		return false
	}

	return g.IsExecutable(approveTx, swapTx)
}
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

**File:** kaiax/gasless/impl/builder.go (L28-72)
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

			b := builder.NewBundle(
				bundleTxs,
				targetTxHash,
				false,
			)

			targetTxHash = tx.Hash()

			isConflict := false
			for _, prev := range append(prevBundles, bundles...) {
				if prev.IsConflict(b) {
					isConflict = true
					break
				}
			}
			if isConflict {
				// Gasless transactions will just fail even if they aren't bundled.
				continue
			}
			bundles = append(bundles, b)
		} else {
			targetTxHash = tx.Hash()
		}
	}
	return bundles
}
```
