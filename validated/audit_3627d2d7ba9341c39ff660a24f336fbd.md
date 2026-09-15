### Title
Gasless bundle execution can be DoSed by frontrunning one leg of the bundle, discarding an already-valid user swap - (File: work/worker.go)

### Summary
The `heal()` report describes a class of bug where a batch/array of independently-valid items is processed as one all-or-nothing unit, so an attacker can intentionally make just one item fail its status check to revert the entire batch and DoS the honest participants whose items were otherwise valid. The Kaia client has a structurally identical pattern in the `kaiax/gasless` block-building flow: a gasless swap is packaged into an atomic bundle `[LendTxGenerator, GaslessApproveTx, GaslessSwapTx]` [1](#0-0)  and executed as a single unit whose success requires every member transaction to succeed.

### Finding Description
`ExtractTxBundles` builds a bundle for each gasless user that groups the (proposer-funded) lend transaction with the user's approve/swap transactions [2](#0-1) . During block building, `commitBundleTransaction` executes each transaction of the bundle in `ApplyTransaction`, and if **any** transaction in the bundle fails or its receipt status is not `ReceiptStatusSuccessful`, the whole bundle — including transactions that already executed successfully — is discarded via `restoreEnv()`, and the code explicitly documents the exact risk being described in the report:

> "Bundled tx will be rejected with any receipt.Status other than success. There may be cases where a revert occurs within the EVM, which could result in an attack on a tx sender in an already executed bundle." [3](#0-2) 

The `GaslessSwapTx` executes a swap against a whitelisted router/pool with a `minAmountOut`/`amountRepay` check enforced on-chain (`checkBalanceForSwap` mirrors these checks at the pool level: `minAmountOut >= amountRepay`, and required `amountIn` computed from the router's live exchange rate) [4](#0-3) . Any unprivileged sender can submit an ordinary transaction that changes the router/pool state between the time the gasless bundle is formed and the time it is executed on-chain (e.g., trading against the same pool in an earlier position within the same block), causing the `GaslessSwapTx`'s slippage/repay condition to fail on execution. Because this transaction is the last element of the bundle, its failure causes the entire bundle — including the `LendTxGenerator` and `GaslessApproveTx` that had already succeeded — to be reverted and discarded, exactly the batch-DoS pattern in the report ("frontrunning by intentionally invalidating one element of a batch causes the whole batch, including otherwise-valid elements, to fail").

### Impact Explanation
A malicious, unprivileged transaction sender can grief gasless users by cheaply manipulating shared swap-router state to make their `GaslessSwapTx` revert on execution, causing:
- The victim's already-valid `GaslessApproveTx` execution (state changes) to be rolled back and the bundle to be dropped from the block.
- Repeated failure/exclusion of the victim's gasless transaction across blocks, effectively denying them the KIP-247 gasless service, at a cost only proportional to a normal swap/trade transaction for the attacker.

This matches the Medium-severity impact class of the original finding: availability/DoS of a batched operation via frontrunning one member's status check, without direct fund theft.

### Likelihood Explanation
Likelihood is moderate-to-high: any address can submit a transaction that alters the state a `GaslessSwapTx` depends on (same swap router/pool), and the mempool/bundle formation logic in `ExtractTxBundles`/`IsExecutable` performs its checks against pending state before block assembly, but the on-chain execution order and pool state at commit time is what actually determines success, so a well-timed unrelated trade can flip the swap's outcome. No special privileges (proposer, validator, or protocol-level access) are required — only the ability to submit an ordinary transaction ahead of the target user's gasless bundle.

### Recommendation
- Consider re-validating/re-simulating the `GaslessSwapTx` against current state immediately before including it in the bundle, and skip only the failing gasless bundle rather than discarding all previously-applied state if any recovery is feasible.
- Where possible, avoid all-or-nothing semantics for bundles whose legs are logically independent (e.g., don't retroactively invalidate the `LendTxGenerator`/`ApproveTx` effects solely because the final swap slipped), or add tighter, more attack-resistant slippage bounds so that ordinary same-block trades cannot trivially flip the swap's success/failure outcome.

### Proof of Concept
1. A gasless user submits `GaslessApproveTx` (nonce n) and `GaslessSwapTx` (nonce n+1) with `minAmountOut` computed against the current router rate.
2. The `kaiax/gasless` module validates the pair as executable via `IsExecutable`/`VerifyExecutable` and bundles them with a prepended `LendTxGenerator` [5](#0-4) .
3. An attacker submits an ordinary swap transaction against the same router/pool ordered ahead of the victim's bundle in the same block, shifting the exchange rate so the victim's `GaslessSwapTx.AmountRepay`/`minAmountOut` check fails on execution.
4. In `commitBundleTransaction`, the swap's `receipt.Status != types.ReceiptStatusSuccessful` (or it reverts), triggering `restoreEnv()`, which rolls back the entire bundle — including the already-executed `LendTxGenerator` and `GaslessApproveTx` — and marks the bundle's transactions unexecutable [6](#0-5) .
5. The victim's gasless transaction pair fails to be included, and the attacker can repeat this cheaply each block to keep DoSing the victim, mirroring the "heal front-run by intentionally failing one item's status check" pattern from the source report.

### Citations

**File:** kaiax/gasless/README.md (L29-36)
```markdown
### Block building rules

Upon detection of GaslessTxs, the following logics are executed:

- Per sender, if exists, GaslessApproveTx is relocated before GaslessSwapTx.
- LendTxGenerator is prepended before GaslessApproveTx.
- A new bundle is generated which contain either `[LendTxGenerator, GaslessApproveTx, GaslessSwapTx]` or `[LendTxGenerator, GaslessSwapTx]`
- If the bundle has conflict with any previous bundles, it is excluded from the returned bundle list.
```

**File:** kaiax/gasless/impl/builder.go (L28-66)
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
```

**File:** work/worker.go (L905-936)
```go
	var totalTxSize uint64 = 0
	for _, txOrGen := range bundle.BundleTxs {
		tx, err := txOrGen.GetTx(env.state.GetNonce(nodeAddr))
		if err != nil {
			logger.Error("TxGenerator error", "error", err)
			markAllTxUnexecutable()
			restoreEnv()
			return kerrors.ErrTxGeneration, nil, nil
		}

		env.state.SetTxContext(tx.Hash(), common.Hash{}, env.tcount)
		receipt, _, err := bc.ApplyTransaction(env.config, &nodeAddr, env.state, env.header, tx, &env.header.GasUsed, vmConfig)
		// Bundled tx will be rejected with any receipt.Status other than success.
		// There may be cases where a revert occurs within the EVM, which could result in an attack on a tx sender in an already executed bundle.
		if err != nil || receipt.Status != types.ReceiptStatusSuccessful {
			if err != vm.ErrInsufficientBalance && err != vm.ErrTotalTimeLimitReached {
				markAllTxUnexecutable()
			}
			receiptStatus := ""
			if receipt != nil {
				receiptStatus = strconv.FormatUint(uint64(receipt.Status), 10)
			}
			logger.Warn("ApplyTransaction error, restoring env",
				"blockNum", env.header.Number.String(), "txHash", tx.Hash().String(),
				"error", err, "receiptStatus", receiptStatus,
			)
			restoreEnv()
			if err == nil {
				err = kerrors.ErrRevertedBundleByVmErr
			}
			return err, tx, nil
		}
```

**File:** kaiax/gasless/impl/tx_pool.go (L102-142)
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
```
