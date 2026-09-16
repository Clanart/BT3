### Title
Gasless swap deadline and slippage checks are not re-validated at promotion time, allowing stale `SwapForGas` transactions to be included after price/time conditions expire - (File: kaiax/gasless/impl/tx_pool.go)

### Summary
The `kaiax/gasless` module implements Kaia's KIP-247 gasless transaction flow, where an unprivileged sender submits a `GaslessSwapTx` (calling `swapForGas(token, amountIn, minAmountOut, amountRepay, deadline)`) that a block proposer executes on the sender's behalf, repaying the lent gas from the swap output. The economic safety checks that mirror the Maia/Talos report's `slippage`/`deadline` concerns — i.e. `minAmountOut >= amountRepay`, `amountIn >= router.GetAmountIn(minAmountOut)`, and `deadline >= currentTimestamp` — are implemented in `checkBalanceForSwap` [1](#0-0) [2](#0-1) . However, the pool's promotion/readiness logic (`isReady`, `isApproveTxReady`, `isSwapTxReady`), which decides whether a queued tx becomes "ready" to be bundled into a block, only calls `IsExecutable`/`VerifyExecutable` [3](#0-2) , and `VerifyExecutable` only checks structural pattern validity (sender/token/nonce/repay-amount matching) [4](#0-3)  — it does not re-invoke `checkBalanceForSwap`'s deadline or slippage/price checks.

### Finding Description
`checkBalanceForSwap` enforces the deadline and slippage-related invariants described in the reported bug class: [5](#0-4) [2](#0-1) 

This function is exposed to the tx pool only via `GetCheckBalance`, which is used as a validation hook: [6](#0-5) 

Separately, the promotion decision for moving a queued `GaslessSwapTx`/`GaslessApproveTx` pair into the "ready"/pending state that the block builder consumes is driven entirely by `isReady` → `isApproveTxReady`/`isSwapTxReady`, which call only `IsExecutable`: [3](#0-2) 

`IsExecutable`/`VerifyExecutable` validate only the "pattern" conditions (matching sender, token, approve amount, nonce sequencing, and correct `amountRepay` computation) — it never re-checks `swapArgs.Deadline` against the current block time, nor re-checks `minAmountOut` viability against the router's live exchange rate: [7](#0-6) 

Because a `GaslessSwapTx` can sit in the queue (e.g., waiting for its preceding `GaslessApproveTx` to become the sender's current nonce, or waiting for a nonce gap to close) for an arbitrary amount of time before `isReady` promotes it, and because promotion does not re-run the deadline/minAmountOut checks that originally gated its admission, a swap whose `deadline` has since elapsed, or whose `minAmountOut` no longer reflects a safe slippage bound relative to the live pool price, can still be judged "ready" and bundled by `GetLendTxGenerator`/`ExtractTxBundles` for inclusion in a block, deferring the actual revert-or-not decision to on-chain execution of `swapForGas` at whatever price exists at inclusion time.

### Impact Explanation
If the on-chain `GaslessSwapRouter.swapForGas` contract itself does not independently re-verify `deadline` and `minAmountOut` against the live AMM price at execution (the contract bytecode is opaque to source-level review in this repo; only the generated Go bindings are indexed), a stale swap could be executed at a time and price the sender never intended, permitting a proposer or a colluding MEV actor to strategically delay promotion of a submitted `SwapForGas` transaction until an unfavorable price window, then let it execute — replicating exactly the "stale slippage/deadline enables sandwich/adverse execution" bug class from the external report, but in Kaia's gasless/auction-adjacent transaction-bundling path rather than a DeFi vault. This could result in gasless users receiving worse swap outputs than intended, and their gas-repayment (`amountRepay`) obligation still being deducted, i.e., concrete value loss to an unprivileged sender.

### Likelihood Explanation
Likelihood is moderate and conditioned on unverified on-chain contract behavior: the Solidity source of `GaslessSwapRouter` was not available in the indexed codebase (only ABI/bytecode bindings), so it is uncertain whether `swapForGas` itself enforces `deadline`/slippage on-chain independent of the off-chain pool checks. If it does, this pool-level gap is only a minor staleness window; if it does not (mirroring the off-chain-only enforcement pattern seen in `checkBalanceForSwap`), the promotion-path gap is directly exploitable by any block proposer or observer who can influence promotion timing (e.g., via nonce-gap timing, resubmission ordering, or simply natural queue delay across blocks).

### Recommendation
Re-invoke `checkBalanceForSwap` (or at minimum its deadline and price-safety checks) inside `isSwapTxReady`/`isApproveTxReady` immediately before promotion, and/or re-validate these conditions again in `GetLendTxGenerator`/`ExtractTxBundles` right before a bundle is built for the current block, so that a transaction whose deadline has expired or whose slippage bound is no longer safe against the current on-chain price is dropped rather than promoted. Additionally, confirm and, if necessary, add independent enforcement of `deadline` and `minAmountOut` inside the `GaslessSwapRouter.swapForGas` contract itself so no single-layer omission (pool or contract) can allow stale execution.

### Proof of Concept
Not independently reproduced against a live node in this pass; based on static analysis:
1. Attacker/observer sends `ApproveTx` (nonce N) and `SwapForGas` (nonce N+1) with `deadline = now + Δ`, `minAmountOut` computed against the current pool price. Both pass `checkBalanceForSwap`/`checkBalanceForApprove` at admission and enter the pool queue.
2. Suppose the `ApproveTx` remains un-promoted for longer than `Δ` seconds (e.g., due to gas price competition, queue congestion, or a colluding proposer intentionally delaying it) — `isApproveTxReady`/`isSwapTxReady` only check `IsExecutable` (nonce/token/repay pattern), not `swapArgs.Deadline` [8](#0-7) .
3. Once the sender's on-chain nonce catches up (approve executes), `isSwapTxReady` promotes the swap tx as "ready" even though `block.timestamp > swapArgs.Deadline` and/or the pool price has since moved such that `minAmountOut` no longer reflects a safe bound — no re-check occurs in this code path.
4. The swap bundle is generated by `GetLendTxGenerator`/`ExtractTxBundles` and executed on-chain at the current (stale, and potentially adverse) price, exposing the gasless sender to sandwich/adverse-price risk identical to the reported bug class, contingent on the actual `swapForGas` contract not independently rejecting expired deadlines — which could not be confirmed from the indexed sources.

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

**File:** kaiax/gasless/impl/tx_pool.go (L102-120)
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
```

**File:** kaiax/gasless/impl/tx_pool.go (L175-179)
```go
	// tx.deadline >= currentTimestamp
	deadline := swapArgs.Deadline
	if deadline.Cmp(g.Chain.CurrentBlock().Time()) < 0 {
		return fmt.Errorf("insufficient deadline: deadline=%s, want=%s", deadline.String(), g.Chain.CurrentBlock().Time().String())
	}
```

**File:** kaiax/gasless/impl/tx_pool.go (L232-290)
```go
// Check promotion condition.
func (g *GaslessModule) isReady(txs map[uint64]*types.Transaction, i uint64, ready types.Transactions) bool {
	tx := txs[i]

	if g.IsApproveTx(tx) && i < uint64(math.MaxUint64) {
		return g.isApproveTxReady(tx, txs[i+1])
	}

	if g.IsSwapTx(tx) {
		var prevTx *types.Transaction
		if len(ready) > 0 {
			prevTx = ready[len(ready)-1]
		}
		return g.isSwapTxReady(tx, prevTx)
	}

	return false
}

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
