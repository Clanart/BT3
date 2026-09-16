### Title
Gasless swap deadline is enforced only at tx-pool admission time, not re-checked during bundle building/block assembly, allowing execution of expired `swapForGas` requests - (File: kaiax/gasless/impl/tx_pool.go, kaiax/gasless/impl/builder.go, kaiax/gasless/impl/getter.go)

### Summary
This is the closest reachable analog to the source bug class: a user-specified time-bound restriction (`deadline` for `swapForGas`, analogous to `lockingPeriod` for dCDS withdraw) is validated at one point in the transaction's lifecycle but is not enforced at the point where it actually matters — the moment the transaction is finally bundled and executed as part of a block.

### Finding Description
The KIP-247 gasless swap flow lets a user submit a `swapForGas(token, amountIn, minAmountOut, amountRepay, deadline)` transaction. The `deadline` field is intended to bound how long the sender's stated exchange-rate assumptions/quote remain valid [1](#0-0) .

The only place `deadline` is checked against the current time is `checkBalanceForSwap`, which is wired to the tx-pool's `GetCheckBalance` hook invoked when a transaction is first admitted to the pool: [2](#0-1) [3](#0-2) 

However, the logic that actually determines whether a swap transaction is "executable" and gets bundled with a proposer-funded `LendTxGenerator` transaction for inclusion in a block, `VerifyExecutable`/`IsExecutable`, does **not** check the deadline at all — it only validates sender match, token match, approve amount, nonce sequencing, and repay amount: [4](#0-3) 

This function is what `ExtractTxBundles` (the `TxBundlingModule` interface used by the block builder/worker) relies on to decide whether to bundle a swap transaction with a lend transaction for actual inclusion in a block: [5](#0-4) 

Because a transaction can remain queued/pending in the pool for a period of time before being included in a block (up to the pool's timeout thresholds, e.g. `PendingTimeout`/`QueueTimeout` of 10 seconds, and network propagation/re-org delays), the deadline check performed once at admission time does not guarantee the deadline still holds when the transaction is later selected into a bundle and executed. There is no deadline re-validation in the block-building path (`ExtractTxBundles`/`IsExecutable`) or execution module path before the swap is bundled with a lend transaction and gas is fronted by the block proposer.

### Impact Explanation
If `deadline` is not re-verified at bundling/execution time, a swap transaction whose price-protection window has expired can still be selected into a bundle, funded by the proposer's `LendTxGenerator`, and executed against `GaslessSwapRouter.swapForGas` with stale `minAmountOut`/`amountRepay` parameters computed at submission time. Depending on how strictly the on-chain router itself enforces the deadline (not verifiable from the indexed contract bytecode/ABI alone — the Solidity source for `GaslessSwapRouter` was not available in the index), this can result in: gas-lending funds being advanced against unfavorable/stale swap conditions, repayment amount mismatches, or reliance purely on the off-chain mempool check as the sole enforcement of a monetary safety parameter feeding proposer-funded value flows. This is a fee-delegation/gasless-settlement class issue touching real value movement (the proposer's lent KAIA and the user's swapped tokens).

### Likelihood Explanation
Likelihood is limited by two mitigating factors that reduce confidence this fully satisfies the "unauthorized value movement" bar: (1) the actual on-chain `GaslessSwapRouter.sol` deadline enforcement could not be confirmed from the indexed bytecode/ABI, so the on-chain contract may independently revert on an expired deadline, making the missing off-chain re-check merely redundant rather than exploitable; and (2) pool timeout windows are short (10 seconds), narrowing the exploitation window. Given this uncertainty, this should be treated as a **potential** gap in defense-in-depth rather than a confirmed critical value-movement bug.

### Recommendation
Re-validate `swapArgs.Deadline` against the current/latest block timestamp inside `VerifyExecutable` (or immediately before `ExtractTxBundles` includes the bundle), in addition to the existing pool-admission check in `checkBalanceForSwap`, so that a swap whose deadline has lapsed between admission and block assembly is dropped from bundling rather than being combined with a proposer-funded lend transaction.

### Proof of Concept
Not independently reproducible from the indexed code alone because the on-chain `GaslessSwapRouter.sol` deadline-enforcement logic could not be located in the index (only ABI/bytecode bindings were available). The gap is demonstrated structurally: `checkBalanceForSwap` performs the deadline check [6](#0-5)  but `VerifyExecutable`, the function that governs bundling for actual block inclusion, has no equivalent check [7](#0-6) . Confirming exploitability requires the contract source of `GaslessSwapRouter.sol`, which is a limitation of the current index; starting a Devin session with full repository access would allow verifying whether the on-chain function itself re-checks `deadline` on execution.

### Citations

**File:** kaiax/gasless/impl/getter.go (L59-67)
```go
type SwapArgs struct {
	Sender       common.Address // tx.from
	Router       common.Address // tx.to
	Token        common.Address
	AmountIn     *big.Int
	MinAmountOut *big.Int
	AmountRepay  *big.Int
	Deadline     *big.Int
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

**File:** kaiax/gasless/impl/tx_pool.go (L175-182)
```go
	// tx.deadline >= currentTimestamp
	deadline := swapArgs.Deadline
	if deadline.Cmp(g.Chain.CurrentBlock().Time()) < 0 {
		return fmt.Errorf("insufficient deadline: deadline=%s, want=%s", deadline.String(), g.Chain.CurrentBlock().Time().String())
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
