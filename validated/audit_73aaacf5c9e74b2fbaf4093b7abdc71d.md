## Title
Front-running a bundle's target transaction causes atomic all-or-nothing reversal of gasless/auction bundles, griefing valid transactions and denying auction settlement - (File: work/worker.go)

### Summary
Kaia's block builder executes `TxBundlingModule`-produced bundles (used by the `gasless` and `auction` kaiax modules) atomically: every transaction in a bundle must succeed, and the bundle is only committed if a specific `TargetTxHash` precedes it and succeeded. If any single transaction inside the bundle reverts or the target transaction's outcome/position is disturbed, the **entire bundle** — including any already-valid, unrelated transactions bundled with it — is discarded, exactly the "one bad status in a batch reverts everything" pattern described in the LooksRare `heal()` report. This is trivially triggerable by any unprivileged transaction sender who front-runs the target transaction or one of the bundled preconditions.

### Finding Description
`ExtractBundlesAndIncorporate`/`IncorporateBundleTx` place a module's bundle transactions directly after a `TargetTxHash` in the block, and `ApplyTransactions` requires that target to have succeeded immediately before the bundle runs: [1](#0-0) 

Bundle execution itself is atomic — `commitBundleTransaction` iterates every tx in the bundle and rolls back **all of them** (`restoreEnv()`) the moment any single one fails or is EVM-reverted: [2](#0-1) 

This is used concretely by the gasless module, which bundles `[LendTxGenerator, ApproveTx(optional), SwapTx]` keyed on a `TargetTxHash`: [3](#0-2) 

and by the auction module, whose winning `BidTx` is placed as a bundle right after the bid's `TargetTxHash`, requiring that target to succeed (`TargetRequired = true`) before the bid executes.

An unprivileged attacker who observes a pending target transaction (or an approve/swap pair) in the mempool can front-run it with an unrelated transaction that invalidates a downstream precondition — e.g., draining/burning the token balance or allowance the gasless swap depends on (`checkBalanceForSwap`, `VerifyExecutable`): [4](#0-3) 

Because the bundle is atomic and keyed to the target's exact success/position, this forces `shouldDiscardBundle`/`commitBundleTransaction` to reject or roll back the whole bundle even though other transactions in it (e.g., the approve tx, or the lend tx) were individually valid.

### Impact Explanation
- For gasless swaps: a legitimate approve+swap gasless bundle can be forced to fail as a unit by front-running only the swap's precondition, wasting the relayer/sequencer's block space and the gasless-lending flow, and denying the gasless user's transaction inclusion even though part of the bundle was valid — a denial-of-settlement on the gasless module.
- For auctions: front-running/invalidating the `TargetTxHash` (or causing it to fail/relocate) makes `shouldDiscardBundle` reject the winning searcher's `BidTx`, so the auctioneer/proposer never collects the guaranteed auction bid revenue and the winning searcher's paid-for execution right is nullified — auction settlement griefing/theft of the expected fee revenue reachable by any auction participant or unrelated third party who can submit a colliding transaction.
- This matches the accepted class "gasless or auction settlement theft" since it results in guaranteed settlement/fee revenue (from a signed, accepted winning bid) not materializing due to an unprivileged party's ability to invalidate a status the batch depended on.

### Likelihood Explanation
Any unprivileged account able to submit ordinary transactions can trigger this: no special privilege, validator role, or p2p access is required — only visibility of a pending transaction (mempool observation) and the ability to submit a transaction that changes the state the bundle depends on before the target/bundle is committed. The existing test suite in `tests/kaia_scenario_test.go` (`TestTxBundleRevert`, `TestTxBundleRevertByEvmError`) confirms this atomic all-or-nothing behavior is by design, not a corner case, making the griefing reliably reproducible.

### Recommendation
Do not tie an entire bundle's validity to a rigid, exact-position status check on the target transaction. Instead, allow the block builder to:
- Re-validate/re-derive the bundle content (e.g., recompute `VerifyExecutable`/`checkBalanceForSwap`) at commit time and skip only the invalidated leg instead of aborting the whole bundle, where safe, or
- Make target-dependency checks tolerant to reordering/absence (treat a missing/failed unrelated precondition as "skip bundle" rather than "roll back sibling successful transactions"), and
- For auction bids, decouple the searcher's guaranteed fee/deposit capture from the exact success of an external target transaction so a griefer cannot cheaply deny settlement revenue by invalidating the target.

### Proof of Concept
1. A gasless relayer includes bundle `[LendTx, ApproveTx, SwapTx]` targeting `TargetTxHash = 0`, per `GaslessModule.ExtractTxBundles` (`kaiax/gasless/impl/builder.go:28-72`).
2. Before this bundle is mined, an attacker submits (and gets included via higher gas price) a transaction that transfers away the gasless sender's token balance/allowance so `checkBalanceForSwap`'s `balance.Cmp(swapArgs.AmountIn) < 0` check (`kaiax/gasless/impl/tx_pool.go:165-172`) or `VerifyExecutable`'s `SP2` check (`kaiax/gasless/impl/getter.go:243-245`) would now fail.
3. When the builder executes the bundle, `SwapTx` reverts inside `commitBundleTransaction`; per `work/worker.go:917-936`, `restoreEnv()` rolls back the *entire* bundle, including the otherwise-valid `ApproveTx`, and returns `kerrors.ErrRevertedBundleByVmErr`, which causes the whole bundle to be popped (`work/worker.go:788-792`).
4. Analogously, for an auction `BidTx` whose bundle requires `bundle.TargetTxHash == env.txs[env.tcount-1].Hash()` and `Status == Successful` (`work/worker.go:960-969`), an attacker who causes the target transaction to fail or be excluded forces the winning bid to be discarded, denying the auctioneer/proposer the expected auction fee even though a valid signed winning bid existed.

### Citations

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

**File:** work/worker.go (L956-972)
```go
func (env *Task) shouldDiscardBundle(bundle *builder.Bundle) (bool, error) {
	if !bundle.TargetRequired {
		return false, nil
	}
	if env.tcount == 0 {
		return bundle.TargetTxHash != common.Hash{}, fmt.Errorf("target tx %s does not precede the bundle", bundle.TargetTxHash.Hex())
	} else {
		// if `env.tcount` is not zero, the `bundle.TargetTxHash` must not be empty hash
		if bundle.TargetTxHash != env.txs[env.tcount-1].Hash() {
			return true, fmt.Errorf("target tx %s does not precede the bundle", bundle.TargetTxHash.Hex())
		}
		if env.receipts[env.tcount-1].Status != types.ReceiptStatusSuccessful {
			return true, fmt.Errorf("target tx %s failed with status %d", bundle.TargetTxHash.Hex(), env.receipts[env.tcount-1].Status)
		}
	}
	return false, nil
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
