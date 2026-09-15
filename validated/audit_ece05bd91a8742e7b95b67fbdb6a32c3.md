Based on my investigation, the strongest and only substantiated analog is a mempool-only, stale-reference deadline check in the Kaia gasless (KIP-247) module.

### Title
Gasless swap deadline is validated against the stale parent block timestamp instead of the actual inclusion time - (File: kaiax/gasless/impl/tx_pool.go)

### Summary
`GaslessModule.checkBalanceForSwap` enforces `tx.deadline >= currentTimestamp` by comparing the user-supplied `SwapArgs.Deadline` against `g.Chain.CurrentBlock().Time()`, i.e., the timestamp of the *last committed* block, not the timestamp of the block in which the swap transaction will actually be executed. [1](#0-0) 

### Finding Description
The deadline check is documented and implemented as: [2](#0-1) [1](#0-0) 

This check runs during mempool admission/re-validation (`GetCheckBalance`, invoked from `TxPool.validateTx` and `txList.Filter`), using the parent (head) block's timestamp as a proxy for "now." [3](#0-2) [4](#0-3) 

This mirrors the reported bug class exactly: a time-window boundary check ("deadline has/hasn't passed") is evaluated against a *reference timestamp that is one step behind the actual execution context*. Kaia targets ~1 second block intervals (`BlockGenerationInterval`), so a swap with `deadline == parentBlock.Time() + ε` will pass the mempool's stale check even though, by the time it is actually executed in the *next* block, `block.timestamp` will already exceed the deadline. Conversely (and more importantly for exploitation), a transaction can be crafted with a deadline set to satisfy the check against the stale parent time window while genuinely being executed later, because the check is only re-evaluated opportunistically (on `Filter`/`validateTx`), not necessarily right at inclusion time — there is no re-check of deadline validity at the moment the transaction bundle is actually assembled into the block by `ExtractTxBundles`/`ApplyTransactions`. [5](#0-4) [6](#0-5) 

Critically, I could not locate the `GaslessSwapRouter.sol` Solidity source in this repository (only the compiled ABI/bytecode bindings are present), so I cannot confirm whether `swapForGas` itself re-validates `deadline >= block.timestamp` on-chain at execution time. [7](#0-6) 
This is an important gap: if the on-chain contract also enforces the deadline correctly, this is merely a mempool admission inefficiency (transaction reverts, at most a lightweight nuisance—wasted gas is refunded to the sponsor). If the on-chain contract does **not** independently re-check `deadline` at execution (relying on the "off-chain" `checkBalanceForSwap` gate as the sole deadline enforcement, similar to how `ShouldCheckSwapAmount`/`ShouldCheckToken` are pool-level-only guards per the module's config flags), then a swap whose deadline has genuinely expired relative to the block it lands in could still execute, causing the gasless swap to settle using a stale/unfavorable exchange-rate assumption the user no longer consented to, while the block proposer's `LendTxGenerator`-funded gas is still repaid via `AmountRepay`. [8](#0-7) 

### Impact Explanation
If the deadline is enforced only at the pool layer (unconfirmed due to missing Solidity source), this allows execution of a gasless swap after its intended expiry, potentially settling at a stale price against the user's stated deadline intent — an unauthorized value-movement / settlement-integrity issue in the gasless swap flow, reachable by any gasless user submitting an ordinary `SwapForGas` transaction via public RPC. If the router additionally enforces the deadline on-chain (likely, following standard AMM router patterns), the actual practical impact is limited to spurious mempool admission/rejection inconsistencies rather than a fund-safety bug.

### Likelihood Explanation
Likelihood is Low-to-Medium and conditional: it requires (a) the on-chain `swapForGas` to not independently enforce `deadline >= block.timestamp`, which I could not verify from available source, and (b) a ~1-second timing window exploitation similar to the original Telcoin report. Given Kaia's short block times, the window for triggering the boundary condition is narrow but deterministically triggerable by any user controlling their own transaction's `deadline` field.

### Recommendation
- Verify and, if missing, enforce `deadline >= block.timestamp` directly inside `GaslessSwapRouter.swapForGas` at execution time (not only in the off-chain mempool check).
- In `kaiax/gasless/impl/tx_pool.go`, change the mempool-level deadline check to use the *pending* block's expected timestamp (e.g., parent time + `BlockGenerationInterval`) rather than `g.Chain.CurrentBlock().Time()`, to avoid stale-reference false negatives/positives at admission time.
- Add a deadline re-check immediately before/at the point `ExtractTxBundles`/`ApplyTransactions` includes the swap bundle into the block, using the actual header timestamp being built.

### Proof of Concept
1. A gasless user submits `SwapForGas(token, amountIn, minAmountOut, amountRepay, deadline)` with `deadline == parentBlock.Time() + 1` (satisfies `deadline.Cmp(g.Chain.CurrentBlock().Time()) < 0` check at submission, since it is not less than current head time). [1](#0-0) 
2. Due to processing delays (block propagation, mempool re-org, queued promotion), the transaction is only bundled into a block whose actual `header.Time` is now well past the user's intended `deadline`. [5](#0-4) 
3. If `GaslessSwapRouter.swapForGas` does not independently assert `deadline >= block.timestamp` on-chain (unverified — Solidity source not present in this repo), the swap executes and settles despite having conceptually "expired," while the sponsor's lent gas is still repaid via `AmountRepay`, mirroring the original report's "challenge recognized too late" logical time-gap pattern.

### Citations

**File:** kaiax/gasless/impl/tx_pool.go (L106-107)
```go
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

**File:** blockchain/tx_list.go (L406-414)
```go
		// balance check for module transaction
		for _, module := range pool.modules {
			if module.IsModuleTx(tx) {
				if checkBalance := module.GetCheckBalance(); checkBalance != nil {
					return checkBalance(tx) != nil
				}
				break
			}
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

**File:** work/worker.go (L682-722)
```go
		var (
			logs    []*types.Log
			txOrGen = incorporatedTxs[0]
			from    common.Address
		)

		// Verify that tx is included in the bundle
		targetBundle := &builder.Bundle{}
		numShift := 1
		if bundleIdx := builder.FindBundleIdx(bundles, txOrGen); bundleIdx != -1 {
			targetBundle = bundles[bundleIdx]
			numShift = len(targetBundle.BundleTxs)
			// Skip this bundle if target is required but either:
			// 1. The previous transaction hash doesn't match the target hash, or
			// 2. The previous transaction failed (receipt status not successful)
			if discard, err := env.shouldDiscardBundle(targetBundle); discard {
				logger.Warn("Skipping bundle due to invalid target tx", "err", err.Error(), "target tx", targetBundle.TargetTxHash.String(), "bundle tx", txOrGen.Id.String(), "numShift", numShift)
				builder.PopTxs(&incorporatedTxs, numShift, &bundles, env.signer)
				continue
			}
		}

		tx, err := txOrGen.GetTx(env.state.GetNonce(nodeAddr))
		if err != nil {
			logger.Warn("TxGenerator returned a nil tx", "error", err)
			builder.PopTxs(&incorporatedTxs, numShift, &bundles, env.signer)
			continue
		}
		if len(targetBundle.BundleTxs) != 0 {
			// if inclusion of the transaction would put the block size over the
			// maximum we allow, don't add any more txs to the payload.
			if !env.txFitsSizeForBundle(nodeAddr, targetBundle) {
				break
			}
		} else {
			// if inclusion of the transaction would put the block size over the
			// maximum we allow, don't add any more txs to the payload.
			if !env.txFitsSize(tx) {
				break
			}
		}
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

**File:** kaiax/gasless/impl/getter.go (L268-313)
```go
// MakeLendTx creates a transaction with following properties:
// L1. LendTx.type = 0x7802 (TxTypeEthereumDynamicFee)
// L2. LendTx.from = proposer
// L3. LendTx.to = SwapTx.from
// L4. LendTx.value = LendAmount(approveTxOrNil, swapTx)
func (g *GaslessModule) GetLendTxGenerator(approveTxOrNil, swapTx *types.Transaction) *builder.TxOrGen {
	var src []byte
	if approveTxOrNil != nil {
		src = append(src, approveTxOrNil.Hash().Bytes()...)
	}
	src = append(src, swapTx.Hash().Bytes()...)
	bundleHash := crypto.Keccak256Hash(src)

	gen := func(nonce uint64) (*types.Transaction, error) {
		var (
			chainId = g.InitOpts.ChainConfig.ChainID
			signer  = types.LatestSignerForChainID(chainId)
			key     = g.InitOpts.NodeKey
		)

		to, err := types.Sender(signer, swapTx)
		if err != nil {
			return nil, err
		}

		tx, err := types.NewTransactionWithMap(types.TxTypeEthereumDynamicFee, map[types.TxValueKeyType]interface{}{
			types.TxValueKeyNonce:      nonce,
			types.TxValueKeyTo:         &to,
			types.TxValueKeyAmount:     lendAmount(approveTxOrNil, swapTx),
			types.TxValueKeyData:       common.Hex2Bytes("0x"),
			types.TxValueKeyGasLimit:   params.TxGas,
			types.TxValueKeyGasFeeCap:  swapTx.GasFeeCap(),
			types.TxValueKeyGasTipCap:  swapTx.GasTipCap(),
			types.TxValueKeyAccessList: types.AccessList{},
			types.TxValueKeyChainID:    chainId,
		})
		if err != nil {
			return nil, err
		}

		err = tx.Sign(signer, key)
		return tx, err
	}

	return builder.NewTxOrGenFromGen(gen, bundleHash)
}
```
