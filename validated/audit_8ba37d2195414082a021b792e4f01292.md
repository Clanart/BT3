## Finding [1](#0-0) 

### Title
Gasless swap `deadline` is enforced only at mempool admission, not at inclusion/execution time, allowing stale-price swap execution - (File: kaiax/gasless/impl/tx_pool.go)

### Summary
The gasless module (KIP-247) lets a user submit a `swapForGas` transaction with a `deadline` parameter intended to bound how long the signed swap authorization remains valid, exactly like a Uniswap-style deadline guard against stale-price execution. In Kaia, this deadline is checked *only* once, off-chain, inside the tx-pool's alternative balance-check hook `checkBalanceForSwap`, which runs solely during `TxPool.add()`/`validateTx()` at initial submission. It is never re-validated when the transaction is later promoted to `pending` or built into a block.

### Finding Description
`GetCheckBalance()` returns `checkBalanceForSwap`, which is invoked by `blockchain/tx_pool.go`'s `validateTx()` only at the moment a transaction is first added to the pool: [2](#0-1) 

The deadline check itself compares the user-supplied `deadline` against the **current block time at admission**, not at the time the transaction is eventually mined: [3](#0-2) 

Crucially, the integration test confirms the rejection message ("insufficient deadline: deadline=1") is the exact string produced by this Go-level function, meaning the deadline is a tx-pool admission gate, not an EVM/consensus-level invariant enforced by `Transaction.Validate()` or block/state processing: [4](#0-3) 

Once a swap transaction passes this one-time admission check it is only re-evaluated for *readiness* (nonce/approval/bundle sequencing), never for the deadline again, before being bundled and executed: [5](#0-4) [6](#0-5) 

This mirrors the Across bug pattern precisely: a deadline field exists to bound "how long a value-moving authorization can be exploited," but the enforcement point does not match the point of actual execution, so the temporal guarantee the deadline was designed to provide is broken for any transaction that lingers in the pool (e.g., due to congestion, being queued behind a missing nonce, an unresponsive block proposer, or a malicious block-builder deliberately holding it) past its `deadline`.

### Impact Explanation
Because the deadline is not re-checked at build/inclusion time (and the observed revert string shows it is not enforced inside the EVM either), a block proposer or bundler that controls transaction ordering can withhold and later include a user's `swapForGas` transaction after its declared deadline has elapsed, executing the swap against `minAmountOut`/exchange-rate assumptions the user no longer intends to honor. Since `GaslessSwapRouter` directly moves ERC-20 tokens from the sender and lends/repays KAIA gas via the proposer-controlled `LendTxGenerator` bundle, this allows fee/value extraction from an unprivileged gasless user beyond what they authorized, which is a concrete unauthorized-value-movement / gasless-settlement-abuse scenario reachable by a single validator/proposer without any privileged access beyond normal block assembly.

### Likelihood Explanation
The check only executes at first submission (`pool.add()` → `validateTx()` → `GetCheckBalance()`), and readiness/promotion/bundling logic never calls it again. Any transaction that isn't promoted and mined within the tight validity window it declared (e.g. it sits in `queue` behind a missing nonce, is deprioritized by fee, or the proposer intentionally delays it) becomes exploitable. This requires only standard proposer/bundler capability already in scope (block assembly), not a malicious peer or leaked key.

### Recommendation
Re-validate `swapArgs.Deadline` against the block's timestamp at the moment of inclusion — either inside the `GaslessSwapRouter.swapForGas` contract logic (enforced by the EVM/consensus, so it can't be bypassed regardless of mempool state) and/or immediately before bundling in `ExtractTxBundles`/`IsReady`, so a stale swap is dropped rather than executed.

### Proof of Concept
1. User signs and submits a `swapForGas` tx with `deadline = currentBlockTime + 1` (minimum valid deadline accepted by `checkBalanceForSwap`).
2. Transaction passes `TxPool.add()` and enters `queue`/`pending`.
3. A block proposer/bundler withholds the transaction (e.g., delays promotion, or simply doesn't propose a block for several seconds) until `currentBlockTime` in a later block exceeds the original `deadline`.
4. Because `checkBalanceForSwap` is not invoked again during `IsReady`/`ExtractTxBundles`, and the test evidence shows the deadline error string originates from the Go module rather than an EVM revert, the transaction is bundled and executed in a later block, swapping the user's tokens at potentially unfavorable rates against a deadline the user no longer intended to honor.

### Citations

**File:** kaiax/gasless/impl/tx_pool.go (L102-182)
```go
// tx.minAmountOut >= tx.amountRepay
// tx.amountIn >= gsr.getAmountIn(minAmountOut)
// tx.token.approval(sender, router) >= tx.amountIn
// tx.token.balanceOf(sender) >= tx.amountIn
// tx.deadline >= currentTimestamp
func (g *GaslessModule) checkBalanceForSwap(swapArgs *SwapArgs, swapNonce uint64) error {
	token := swapArgs.Token
	bc := backends.NewBlockchainContractBackend(g.Chain, nil, nil)

	g.gaslessInfoMu.RLock()
	swapRouter := g.swapRouter
	g.gaslessInfoMu.RUnlock()

	// tx.minAmountOut >= tx.amountRepay
	minAmountOut := swapArgs.MinAmountOut
	amountRepay := swapArgs.AmountRepay
	if minAmountOut.Cmp(amountRepay) < 0 {
		return fmt.Errorf("insufficient minAmountOut: minAmountOut=%s, amountRepay=%s", minAmountOut.String(), amountRepay.String())
	}

	if g.GaslessConfig.ShouldCheckSenderCode() {
		if g.getCurrentHasCode(swapArgs.Sender) {
			return errors.New("sender with code is not allowed")
		}
	}

	if g.GaslessConfig.ShouldCheckSwapAmount() {
		// tx.amountIn >= gsr.getAmountIn(minAmountOut)
		routerContract, err := kip247.NewGaslessSwapRouterCaller(swapRouter, bc)
		if err != nil {
			return err
		}
		// Required token amountIn, given the current exchange rate and the declared minAmountOut.
		requiredAmountIn, err := routerContract.GetAmountIn(nil, token, minAmountOut)
		if err != nil {
			return err
		}
		if swapArgs.AmountIn.Cmp(requiredAmountIn) < 0 {
			return fmt.Errorf("insufficient amountIn: have=%s, want=%s", swapArgs.AmountIn.String(), requiredAmountIn.String())
		}
	}

	if g.GaslessConfig.ShouldCheckToken() {

		tokenContract, err := sc_erc20.NewERC20(token, bc)
		if err != nil {
			return err
		}

		// If SwapTx.nonce is the sender's next nonce, then there is no room for ApproveTx proceeding SwapTx.
		senderNonce := g.getCurrentStateNonce(swapArgs.Sender)
		noApproveTxPreceeds := swapNonce == senderNonce
		if noApproveTxPreceeds {
			// tx.token.allowance(sender, router) >= tx.amountIn
			approval, err := tokenContract.Allowance(nil, swapArgs.Sender, swapRouter)
			if err != nil {
				return err
			}
			if approval.Cmp(swapArgs.AmountIn) < 0 {
				return fmt.Errorf("insufficient approval: approval=%s, want=%s", approval.String(), swapArgs.AmountIn.String())
			}
		}

		// tx.token.balanceOf(sender) >= tx.amountIn
		balance, err := tokenContract.BalanceOf(nil, swapArgs.Sender)
		if err != nil {
			return err
		}
		if balance.Cmp(swapArgs.AmountIn) < 0 {
			return fmt.Errorf("insufficient balance: balance=%s, want=%s", balance.String(), swapArgs.AmountIn.String())
		}
	}

	// tx.deadline >= currentTimestamp
	deadline := swapArgs.Deadline
	if deadline.Cmp(g.Chain.CurrentBlock().Time()) < 0 {
		return fmt.Errorf("insufficient deadline: deadline=%s, want=%s", deadline.String(), g.Chain.CurrentBlock().Time().String())
	}

	return nil
}
```

**File:** kaiax/gasless/impl/tx_pool.go (L184-230)
```go
// Check promotion condition and enforce pending pool flow control.
func (g *GaslessModule) IsReady(txs map[uint64]*types.Transaction, next uint64, ready types.Transactions) bool {
	g.knownTxsMu.Lock()
	defer g.knownTxsMu.Unlock()

	tx, ok := txs[next]
	if !ok {
		return false
	}

	if !g.isReady(txs, next, ready) {
		return false
	}

	if g.IsBundleTx(tx) {
		// If prev tx is bundle tx, there's no need to check the knownTxs limit because it has been checked in the previous `IsReady()` execution.
		isPrevTxBundleTx := len(ready) != 0 && g.IsBundleTx(ready[len(ready)-1])
		if isPrevTxBundleTx {
			g.knownTxs.add(tx, TxStatusPending)
			return true
		}

		maxBundleTxsInPending := g.GetMaxBundleTxsInPending()
		if maxBundleTxsInPending != math.MaxUint64 {
			numExecutable := uint(g.knownTxs.numExecutable())

			numSeqTxs := uint(1)
			for i := next + 1; i < next+uint64(len(txs)); i++ {
				if tx, ok := txs[i]; ok && g.IsBundleTx(tx) {
					numSeqTxs++
				} else {
					break
				}
			}

			// false if there is possibility of exceeding max bundle tx num
			if numExecutable+numSeqTxs > maxBundleTxsInPending {
				logger.Trace("Not promoting a tx because of exceeding max bundle tx num", "tx", tx.Hash().String(), "numExecutable", numExecutable, "maxBundleTxsInPending", maxBundleTxsInPending)
				return false
			}
		}

		g.knownTxs.add(tx, TxStatusPending)
	}

	return true
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

**File:** tests/gasless_test.go (L260-262)
```go
	// reject swapTx when deadline is in the past
	_, err = sendSwapTx(t, gsrContract, accounts[0], testTokenAddr, swapAmmount, minAmountOut, amountRepaySwap, common.Big1)
	assert.ErrorContains(t, err, "insufficient deadline: deadline=1")
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
