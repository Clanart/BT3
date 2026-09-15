### Title
Stale gasless‑tx readiness checks allow a lend transaction to execute while the paired swap reverts, causing unrecoverable proposer fund loss - ([File: kaiax/gasless/impl/tx_pool.go], [File: kaiax/gasless/impl/builder.go])

### Summary
Similar to the GMX ADL bug where a candidate's eligibility is validated against a stale, pre-execution snapshot and never re-checked at the moment of execution, Kaia's gasless module validates `GaslessApproveTx`/`GaslessSwapTx` readiness (balance, allowance, AMM output amount) once against the parent block's state, but the actual `LendTx` (unconditional KAIA transfer from the block proposer) and the paired `SwapTx` (which repays the proposer) are built and executed as two independent transactions later in the same block, without any final re-validation immediately before inclusion.

### Finding Description
The gasless flow works as follows:
1. `checkBalanceForSwap`/`checkBalanceForApprove` validate balance, allowance, and expected AMM output (`GetAmountIn`) using the current head state at pool-admission/promotion time. [1](#0-0) 
2. `isSwapTxReady`/`isApproveTxReady`, used for tx-pool promotion, only re-verify structural conditions (`IsExecutable` → `VerifyExecutable`) such as nonce sequencing and repay-amount arithmetic - they do not re-check on-chain balance/allowance/AMM rate freshness. [2](#0-1) 
3. At block-building time, `ExtractTxBundles` re-runs only `IsExecutable` (the same structural check) and then unconditionally prepends a `LendTxGenerator` that sends real KAIA value to the user, followed by the `ApproveTx`/`SwapTx`. [3](#0-2) 
4. The `LendTx` and `SwapTx` are ordinary, independently-executed transactions in the assembled block - there is no atomic "bundle succeeds or all reverts together" guarantee in the builder/incorporation logic; `incorporate`/`IncorporateBundleTx` merely places the bundle's transactions contiguously in the block. [4](#0-3) 
5. `repayAmount`/`lendAmount` are computed purely from gas price and fixed constants, and the swap's actual repayment to the proposer only happens if `swapForGas` succeeds on-chain; the balance/allowance/AMM-rate checks that gated pool admission use the state as of the block *before* the one being built, and do not account for effects of other transactions executed earlier in the very same block (e.g., another swap on the same token pool moving the AMM rate, or the sender's balance/allowance changing due to another transaction from that sender included earlier in the block). [5](#0-4) 

Because the check-time state (used to gate promotion/inclusion) can diverge from the actual execution-time state within the same block being assembled, `SwapTx` can revert on execution (e.g., "insufficient minAmountOut", "insufficient amountIn", or "insufficient balance", as demonstrated in the gasless test suite) after the `LendTx` has already unconditionally transferred KAIA to the user. [6](#0-5) 

This is the direct analog of the GMX finding: a global/stale eligibility check (bid pool / tx pool readiness check) is trusted at execution time instead of being re-validated for the specific state at actual execution, and — unlike `AdlUtils.sol`'s guard against negative position sizes — there is no on-chain guard preventing the `LendTx` from firing when the paired `SwapTx` is doomed to fail.

### Impact Explanation
If the `SwapTx` reverts after the `LendTx` has already transferred KAIA to the user, the block proposer permanently loses the lent amount because there is no mechanism to reclaim it (the repayment logic only executes inside a successful `swapForGas` call). This is a direct, concrete value loss for the block proposer (fee-delegation/gasless counterparty), matching the "fee or fee-delegation abuse" and "unauthorized value movement" categories required by the validation rubric.

### Likelihood Explanation
This can be triggered by any user submitting a `GaslessSwapTx` that is valid at pool-admission time but becomes invalid by the time it executes within the same block — for example, another transaction from the same sender (or a third-party swap affecting the shared AMM rate via `GetAmountIn`) executing earlier in the same block, or the user themselves front-running their own gasless approval with a separate token transfer. Because `ExtractTxBundles` and the final ordering only re-check the structural condition (`IsExecutable`), not balance/allowance/AMM freshness, this is reachable via an ordinary transaction submission and does not require a malicious validator or privileged access.

### Recommendation
Re-validate `checkBalanceForSwap`/`checkBalanceForApprove` (and not just `VerifyExecutable`'s structural checks) immediately before finalizing the bundle in `ExtractTxBundles`, using the exact state the block will be built on. Alternatively, make the lend-and-swap bundle atomic (skip/drop the `LendTx` if the paired `SwapTx` would fail given current state), similar to requiring a final on-chain eligibility check as recommended in the GMX finding.

### Proof of Concept
1. User A submits `ApproveTx` + `SwapTx(token, amountIn, minAmountOut, amountRepay)` that pass `checkBalanceForApprove`/`checkBalanceForSwap` against the parent block state, entering the pending pool as ready. [1](#0-0) 
2. Before the block containing A's bundle is sealed, another transaction (e.g., a large swap on the same `token`/router pair, or a `transfer` moving away A's token balance) is included earlier in the same block, changing the AMM output or A's balance/allowance.
3. `ExtractTxBundles` still includes A's bundle (`LendTx`, `ApproveTx`, `SwapTx`) because it only re-runs `IsExecutable` (nonce/repay-amount arithmetic), not the balance/AMM-rate check. [7](#0-6) 
4. On execution, `LendTx` succeeds (unconditional value transfer to A), but `SwapTx` reverts with "insufficient minAmountOut"/"insufficient amountIn"/"insufficient balance" as shown in the existing test assertions. [6](#0-5) 
5. The proposer has permanently lost the lent KAIA amount since the repayment logic never executed.

### Citations

**File:** kaiax/gasless/impl/tx_pool.go (L107-142)
```go
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
```

**File:** kaiax/gasless/impl/tx_pool.go (L251-290)
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

**File:** work/builder/builder.go (L69-116)
```go
// IncorporateBundleTx incorporates bundle transactions into the transaction list.
// Caller must ensure that there is no conflict between bundles.
func IncorporateBundleTx(txs []*types.Transaction, bundles []*Bundle) ([]*TxOrGen, error) {
	ret := make([]*TxOrGen, len(txs))
	for i, tx := range txs {
		ret[i] = NewTxOrGenFromTx(tx)
	}

	for _, bundle := range bundles {
		var err error
		ret, err = incorporate(ret, bundle)
		if err != nil {
			return nil, err
		}
	}
	return ret, nil
}

// incorporate assumes that `txs` does not contain any bundle transactions.
func incorporate(txs []*TxOrGen, bundle *Bundle) ([]*TxOrGen, error) {
	ret := make([]*TxOrGen, 0, len(txs)+len(bundle.BundleTxs))
	targetFound := false

	// 1. place bundle at the beginning
	if bundle.TargetTxHash == (common.Hash{}) {
		ret = append(ret, bundle.BundleTxs...)
		targetFound = true
	}

	// 2. place bundle after TargetTxHash
	for _, txOrGen := range txs {
		// if tx-in-bundle, the tx will be appended when target is found.
		if bundle.Has(txOrGen) {
			continue
		}
		ret = append(ret, txOrGen)
		if txOrGen.Id == bundle.TargetTxHash {
			targetFound = true
			ret = append(ret, bundle.BundleTxs...)
		}
	}

	if !targetFound {
		return nil, ErrFailedToIncorporateBundle
	}

	return ret, nil
}
```

**File:** kaiax/gasless/impl/getter.go (L346-367)
```go
func lendAmount(approveTxOrNil, swapTx *types.Transaction) *big.Int {
	r := new(big.Int)

	// R2 = ApproveTx.Fee() if exists
	if approveTxOrNil != nil {
		r.Add(r, approveTxOrNil.Fee())
	}

	// R3 = SwapTx.Fee()
	r.Add(r, swapTx.Fee())

	// LendAmount = R2 + R3
	return r
}

func repayAmount(approveTxOrNil, swapTx *types.Transaction) *big.Int {
	// R1 = LendTx.Fee() = SwapTx.GasPrice() * TxGas
	r1 := new(big.Int).Mul(swapTx.GasPrice(), new(big.Int).SetUint64(params.TxGas))

	// RepayAmount = R1 + R2 + R3
	return new(big.Int).Add(r1, lendAmount(approveTxOrNil, swapTx))
}
```

**File:** tests/gasless_test.go (L245-258)
```go
	//// Reject obviously reverting SwapTx.

	// reject swapTx when minAmountOut < amountRepay
	_, err = sendSwapTx(t, gsrContract, accounts[0], testTokenAddr, swapAmmount, common.Big0, amountRepaySwap, deadline)
	assert.ErrorContains(t, err, "insufficient minAmountOut")

	// reject swapTx when amountIn < router.GetAmountIn(minAmountOut)
	_, err = sendSwapTx(t, gsrContract, accounts[0], testTokenAddr, common.Big0, minAmountOut, amountRepaySwap, deadline)
	assert.ErrorContains(t, err, "insufficient amountIn")

	// reject swapTx when balance < amountIn
	// the test acc first received `transferToken` but used up some. So it has less than `transferToken`.
	_, err = sendSwapTx(t, gsrContract, accounts[0], testTokenAddr, transferToken, minAmountOut, amountRepaySwap, deadline)
	assert.ErrorContains(t, err, "insufficient balance")
```
