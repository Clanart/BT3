### Title
Gasless SwapTx deadline is checked only at mempool admission, not at block-building settlement time - ([File: kaiax/gasless/impl/getter.go])

### Summary
The Kaia gasless module validates a swap's `deadline` field only inside `checkBalanceForSwap`, which runs as the tx-pool's `GetCheckBalance` hook at mempool admission time. The function that actually decides whether a pending Approve/Swap pair is executed and settled on-chain — `VerifyExecutable` (called via `IsExecutable`, `isSwapTxReady`, and `ExtractTxBundles`) — never re-checks the deadline. This mirrors the Packistry bug class: a time-bound authorization (expired token / expired deadline) is validated once at intake but not re-validated at the point where the privileged action (fee-delegated lending + token swap settlement) is actually granted.

### Finding Description
`checkBalanceForSwap` explicitly re-derives `tx.deadline` and compares it to the current block timestamp: [1](#0-0) 
This is only invoked from `GetCheckBalance()`, which the core `TxPool.validateTx` calls once, at the moment the swap transaction is submitted to the pool: [2](#0-1) 

However, the actual gate that determines whether the Approve/Swap pair is treated as "ready" for promotion and whether a bundle (including the fee-delegation `LendTx` that fronts gas for the swap) is built for block inclusion is `VerifyExecutable`: [3](#0-2) 
This function checks sender consistency (AP1), token consistency (SP1), approve amount (SP2), nonce sequencing (SP3), and repay amount (SP4) — but contains no comparison of `swapArgs.Deadline` against the current block time.

`VerifyExecutable`/`IsExecutable` is reached from two places that gate real chain effects:
1. `isSwapTxReady` → `isApproveTxReady` → `g.IsExecutable(...)`, used by `IsReady` to decide tx-pool promotion into the pending/executable set: [4](#0-3) 
2. `ExtractTxBundles`, which is the block-builder hook that actually assembles the `LendTx + ApproveTx + SwapTx` bundle to be included in a block: [5](#0-4) 

Because a transaction can sit in the pool (e.g., waiting behind a nonce gap, or simply not yet mined) past the point when `checkBalanceForSwap` was originally evaluated, and because block building re-invokes `IsReady`/`ExtractTxBundles` on every new block without re-running the admission-time balance/deadline check, a swap whose declared `deadline` has since elapsed can still be bundled and executed: the fee-delegation `LendTx` is generated and the underlying `swapForGas` call is made on-chain with an expired deadline.

### Impact Explanation
If the `deadline` is intended to bound the validity window of a swap's declared price parameters (`minAmountOut`, `amountRepay`, `amountIn`), execution past that deadline settles a stale swap: the searcher/relayer (block proposer, via the generated `LendTx`) fronts KAIA gas on behalf of the sender expecting `amountRepay` back per SP4, and the token swap executes against current AMM state with parameters that were only sanity-checked at an earlier point in time. This can allow a swap that should have been rejected (e.g., due to price movement making it no longer favorable, or a user who intended to cancel by letting it expire) to still execute and settle, and it breaks the module's own documented invariant that `tx.deadline >= currentTimestamp`. This is an acceptance-of-an-invalid-transaction-condition into block execution, matching the CWE class in the report (expired-check-then-use).

### Likelihood Explanation
Reaching this requires only a single already-in-pool SwapTx (or Approve+SwapTx pair) submitted by any unprivileged sender, plus the natural passage of time/blocks (no special privileges, no malicious peer/validator behavior needed) — the tx pool retains transactions across many block-building cycles, and `ExtractTxBundles`/`isSwapTxReady` are invoked on ordinary pending-pool contents every block without a fresh deadline check.

### Recommendation
Add an explicit deadline check inside `VerifyExecutable` (getter.go), comparing `swapArgs.Deadline` to the current block's timestamp, mirroring the check already present in `checkBalanceForSwap`, so that the settlement-time gate independently enforces the same time-bound invariant as the admission-time gate rather than relying solely on a one-time mempool check.

### Proof of Concept
1. Submit a `SwapForGas` transaction with `deadline = currentBlockTime + 1` (passes `checkBalanceForSwap` at admission).
2. Do not let it get mined immediately (e.g., it is queued behind other pending state, or block production is delayed past the deadline).
3. Once several blocks pass so that `currentBlockTime > deadline`, the transaction is still present in the pool. On the next block-building cycle, `ExtractTxBundles`/`isSwapTxReady` call `IsExecutable`/`VerifyExecutable`, which does not check the deadline and returns `true` as long as nonce/amount/token conditions still hold.
4. The proposer builds and includes the `LendTx + SwapTx` bundle; `swapForGas` executes on-chain despite the deadline having elapsed — no error is raised for the expired deadline at execution time, only at the earlier admission check that already passed.

### Citations

**File:** kaiax/gasless/impl/tx_pool.go (L175-179)
```go
	// tx.deadline >= currentTimestamp
	deadline := swapArgs.Deadline
	if deadline.Cmp(g.Chain.CurrentBlock().Time()) < 0 {
		return fmt.Errorf("insufficient deadline: deadline=%s, want=%s", deadline.String(), g.Chain.CurrentBlock().Time().String())
	}
```

**File:** kaiax/gasless/impl/tx_pool.go (L251-267)
```go
// isApproveTxReady assumes that the caller checked `g.IsApproveTx(approveTx)`
func (g *GaslessModule) isApproveTxReady(approveTx, nextTx *types.Transaction) bool {
	addr, err := types.Sender(g.signer, approveTx)
	if err != nil {
		return false
	}
	nonce := g.getCurrentStateNonce(addr)

	if approveTx.Nonce() != nonce {
		return false
	}
	if nextTx == nil || !g.IsSwapTx(nextTx) {
		return false
	}

	return g.IsExecutable(approveTx, nextTx)
}
```

**File:** blockchain/tx_pool.go (L918-932)
```go
	// If module recognizes the tx, run an alternative balance check and then skip the default balance check later.
	shouldSkipBalanceCheck := false
	for _, module := range pool.modules {
		if module.IsModuleTx(tx) {
			if checkBalance := module.GetCheckBalance(); checkBalance != nil {
				shouldSkipBalanceCheck = true
				err := checkBalance(tx)
				if err != nil {
					logger.Trace("[tx_pool] invalid funds of module transaction sender", "from", from, "txhash", tx.Hash().Hex())
					return err
				}
			}
			break
		}
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
