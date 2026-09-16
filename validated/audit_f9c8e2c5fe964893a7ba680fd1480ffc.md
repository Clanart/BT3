This confirms the key finding: `ExtractTxBundles` (block-building path, `kaiax/gasless/impl/builder.go:28-72`) decides whether to bundle a `GaslessSwapTx` into a block using only `g.IsExecutable(approveTxs[addr], tx)`, which calls `VerifyExecutable` in `kaiax/gasless/impl/getter.go:214-266`. That function checks sender match, token match, approval amount, nonce sequencing, and repay amount — but never checks `swapArgs.Deadline` against the current block time. The deadline check ("tx.deadline >= currentTimestamp") only exists in `checkBalanceForSwap` at `kaiax/gasless/impl/tx_pool.go:107-179` (lines 175-179), a separate code path used for the tx-pool "check balance" hook, not for the bundling/execution-readiness decision.

### Title
Gasless swap transactions can be bundled into a block after their deadline has expired due to missing deadline check in `VerifyExecutable` - (File: `kaiax/gasless/impl/getter.go`)

### Summary
`GaslessModule.VerifyExecutable`, which backs `IsExecutable` and is used by the block-building bundler (`ExtractTxBundles`) and the `IsReady`/tx-pool promotion path, does not check `SwapArgs.Deadline` against the current block time, even though the deadline check exists elsewhere in the codebase (`checkBalanceForSwap`) and is documented as a required condition ("tx.deadline >= currentTimestamp").

### Finding Description
`kaiax/gasless/impl/getter.go` documents the required gasless-swap conditions in the comment above `IsExecutable`:
```
// Ax. IsApproveTx conditions (if ApproveTx != nil)
// Sx. IsSwapTx conditions
// AP1. ApproveTx.from == SwapTx.from
// SP1. ApproveTx.to == SwapTx.token
// SP2. ApproveTx.amount >= SwapTx.amountIn
// SP3. ApproveTx.nonce+1 == SwapTx.nonce and Gasless transactions are head for nonce
// SP4. SwapTx.amountRepay = RepayAmount(ApproveTx, SwapTx)
```
Notably absent from both the comment list and the implementation of `VerifyExecutable` (`getter.go:214-266`) is any deadline check, even though `SwapArgs` carries a `Deadline` field (used in tests, e.g. `getter_test.go`).

By contrast, `checkBalanceForSwap` in `kaiax/gasless/impl/tx_pool.go:107-179` explicitly documents and enforces:
```go
// tx.deadline >= currentTimestamp
func (g *GaslessModule) checkBalanceForSwap(swapArgs *SwapArgs, swapNonce uint64) error {
	...
	deadline := swapArgs.Deadline
	if deadline.Cmp(g.Chain.CurrentBlock().Time()) < 0 {
		return fmt.Errorf("insufficient deadline: ...")
	}
	return nil
}
```
This function is only wired to the tx-pool's `GetCheckBalance()` hook (`tx_pool.go:62-72`), which is invoked when the sender balance/allowance is checked for a transaction — not necessarily re-invoked at every block-building attempt.

The actual block-building path, `AuctionModule`... (analogous for gasless) `GaslessModule.ExtractTxBundles` in `kaiax/gasless/impl/builder.go:40`, decides inclusion purely via:
```go
} else if g.IsSwapTx(tx) && g.IsExecutable(approveTxs[addr], tx) {
```
`IsExecutable` → `VerifyExecutable` performs none of the deadline validation. Thus a swap transaction that sat in the pending pool (e.g., waiting for its paired approve transaction, or simply not yet mined) past its declared `Deadline` can still be selected and bundled by a block proposer, exactly mirroring the reported pattern: a documented time-window restriction ("swap must occur before deadline") that is not actually enforced at the code path that performs the state-changing action.

### Impact Explanation
If the deadline check is bypassed at bundling/execution-readiness time, a `GaslessSwapTx` can be executed by a block proposer after its declared deadline. Since the swap executes against a router contract using `AmountRepay`/`MinAmountOut` computed at submission time, executing it later (after the deadline the user intended as a staleness/slippage bound) can result in the swap being settled under price conditions the user no longer consented to, while still consuming the block proposer's lent gas (`GetLendTxGenerator`) and the sender's repay obligation. This constitutes settlement of a stale/expired user intent — a fee-delegation/gasless settlement integrity issue reachable by any user submitting a gasless transaction and any proposer selecting it for a block.

### Likelihood Explanation
Likelihood is moderate: it requires a `GaslessSwapTx` (optionally paired with an approve tx) to remain in the pending pool past its `Deadline` without being purged, and for a proposer to still select it via `ExtractTxBundles`/`IsReady`. Whether `checkBalanceForSwap`/`GetCheckBalance` is re-run immediately before every bundling attempt (which would incidentally catch this) is not verifiable from the explored code — this is the key uncertainty. If `GetCheckBalance` is only used as a pool-admission gate and not re-evaluated at every block-building pass, an expired swap can slip through purely via `IsExecutable`/`VerifyExecutable`, which has no deadline logic at all.

### Recommendation
Add an explicit deadline check inside `VerifyExecutable` (or `IsExecutable`) in `kaiax/gasless/impl/getter.go`, comparing `swapArgs.Deadline` to the current chain time, mirroring the check already present in `checkBalanceForSwap`. This ensures the deadline is enforced uniformly at the actual point of block-building/execution decision, not only at a possibly-stale pool-admission check.

### Proof of Concept
1. Submit a valid `GaslessApproveTx` + `GaslessSwapTx` pair with `SwapArgs.Deadline` set to a near-future timestamp.
2. Ensure the pair is accepted into the tx pool (passes `checkBalanceForSwap` at submission time, since deadline was still valid).
3. Let block production stall or delay (e.g., due to network congestion) until the current block time exceeds `Deadline`.
4. When a block is finally built, `ExtractTxBundles` (`kaiax/gasless/impl/builder.go:40`) calls `g.IsExecutable(approveTx, swapTx)` → `VerifyExecutable` (`kaiax/gasless/impl/getter.go:214-266`), which performs no deadline check and returns `nil` (executable) purely based on AP1/SP1/SP2/SP3/SP4 conditions.
5. The stale swap transaction is bundled and executed on-chain past its intended deadline. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

### Citations

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

**File:** kaiax/gasless/impl/getter.go (L195-266)
```go
// IsGaslessPattern checks following conditions:
// Ax. IsApproveTx conditions (if ApproveTx != nil)
// Sx. IsSwapTx conditions
// AP1. ApproveTx.from == SwapTx.from
// SP1. ApproveTx.to == SwapTx.token
// SP2. ApproveTx.amount >= SwapTx.amountIn
// SP3. ApproveTx.nonce+1 == SwapTx.nonce and Gasless transactions are head for nonce
// SP4. SwapTx.amountRepay = RepayAmount(ApproveTx, SwapTx)
func (g *GaslessModule) IsExecutable(approveTxOrNil, swapTx *types.Transaction) bool {
	err := g.VerifyExecutable(approveTxOrNil, swapTx)
	if err != nil {
		return false
	}
	return true
}

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
