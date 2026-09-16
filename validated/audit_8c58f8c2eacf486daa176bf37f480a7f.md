### Title
GaslessSwapTx deadline is only checked at tx-pool admission, not at block-inclusion/execution time, allowing stale swaps to execute after their user-specified deadline - ([File: kaiax/gasless/impl/getter.go])

### Summary
The KIP-247 gasless-swap flow validates `SwapArgs.Deadline` against the current chain time only inside `checkBalanceForSwap`, which runs when the transaction is first admitted into the tx pool. The function that actually gates whether a `GaslessApproveTx`/`GaslessSwapTx` pair is allowed to be bundled and committed into a block — `VerifyExecutable`/`IsExecutable` — never re-checks the deadline. This mirrors the reported `UniV3SwapInput` bug class: a deadline parameter exists in the protocol but is not enforced where it actually matters (at execution/inclusion time), exposing the user to price movement between submission and inclusion.

### Finding Description
`checkBalanceForSwap` performs the deadline check `tx.deadline >= currentTimestamp` using the current block's time at the moment the transaction is being validated for tx-pool admission: [1](#0-0) 

This function is wired as `GetCheckBalance()`, invoked by the tx pool when a gasless tx is being added: [2](#0-1) 

However, the actual gate used to decide whether a swap is "ready"/executable for bundling into a block is `IsExecutable`/`VerifyExecutable`, which validates sender consistency, token consistency, approval amount, nonce ordering, and repay amount (Ax, AP1, SP1–SP4) — but contains no check on `swapArgs.Deadline` at all: [3](#0-2) 

This function is called both during pool promotion (`isSwapTxReady`/`isApproveTxReady`) and during block building (`ExtractTxBundles`): [4](#0-3) [5](#0-4) 

The initial admission-time check in `checkBalanceForSwap` is a one-time gate at insertion. A gasless swap tx can remain in the pool (subject to `QueueTimeout`/`PendingTimeout` of 10s, or longer via requeuing/`PostReset`) and get promoted/bundled and finally committed in a later block without the deadline ever being re-validated against the block it actually lands in: [6](#0-5) 

The RPC-facing validator `debug_isGaslessTx` (`IsGaslessTx`/`VerifyExecutable`) that external callers/proposers may use to pre-check bundle validity has the same gap — it calls `VerifyExecutable` directly, without deadline enforcement: [7](#0-6) 

### Impact Explanation
Because the on-chain-equivalent execution gate (`VerifyExecutable`) does not enforce the deadline, a gasless swap transaction admitted while the deadline was still valid can be delayed (due to pool timeouts, low priority, congestion, or proposer scheduling) and ultimately be included and executed in a much later block after its intended deadline has passed. At that point the AMM price used by `GaslessSwapRouter.swapForGas` (queried on-chain at execution) may have diverged significantly from what the user expected, resulting in the user receiving less favorable output while still being forced to repay the lending amount (`amountRepay`) to the block proposer. This is directly analogous to the reported issue: the deadline safeguard exists in name but is not enforced where the swap is actually executed, exposing users to price-slippage/MEV risk on a value-transferring meta-transaction.

### Likelihood Explanation
This is reachable by any ordinary, unprivileged user submitting a standard KIP-247 gasless swap transaction (a public-RPC-submitted transaction) — no special privileges are required. It requires no attacker action beyond normal network conditions causing delay (congestion, mempool backlog, a validator choosing not to promote it immediately, or `PostReset`/timeout-driven re-queueing), all of which are common on any live chain. The bug is a straightforward missing-check omission rather than a complex multi-step exploit, making it plausible.

### Recommendation
Add an explicit deadline check inside `VerifyExecutable` (and thus `IsExecutable`) in `kaiax/gasless/impl/getter.go`, comparing `swapArgs.Deadline` against the timestamp of the block currently being built (not just the pool-admission-time block), so that stale gasless swaps are rejected from bundling/inclusion rather than only rejected at initial tx-pool admission. Additionally, consider re-validating the deadline in `isSwapTxReady`/`ExtractTxBundles` immediately before bundling, since the header timestamp for the next block may differ from `CurrentBlock().Time()` used at admission time.

### Proof of Concept
1. User submits a `GaslessSwapTx` with `deadline = currentBlockTime + N` seconds. `checkBalanceForSwap` accepts it into the pool because `deadline >= CurrentBlock().Time()` at that instant (`kaiax/gasless/impl/tx_pool.go:175-179`).
2. Due to network congestion, low gas price, or pool eviction/re-queue timing (`QueueTimeout`/`PendingTimeout` = 10s, `kaiax/gasless/impl/tx_pool.go:32-36`), the transaction is not promoted/bundled immediately and instead sits until a later block, e.g., `currentBlockTime + N + 100`.
3. When later promoted, `isSwapTxReady` calls `IsExecutable`/`VerifyExecutable` (`kaiax/gasless/impl/getter.go:203-266`), which checks sender/token/amount/nonce consistency but never re-checks `swapArgs.Deadline` against the new block time.
4. `ExtractTxBundles` (`kaiax/gasless/impl/builder.go:28-72`) bundles the stale swap tx (plus its `LendTxGenerator` and optional approve tx) for inclusion in the block being built, and the worker commits it (`work/worker.go` `ApplyTransactions`), executing `GaslessSwapRouter.swapForGas` at the then-current AMM price — well past the user's original deadline — potentially yielding the user a worse output than intended while still repaying the proposer’s lent gas amount.

### Citations

**File:** kaiax/gasless/impl/tx_pool.go (L32-36)
```go
var (
	QueueTimeout   = 10 * time.Second // bundle tx is removed from txpool.queue after QueueTimeout
	PendingTimeout = 10 * time.Second // bundle tx is removed from txpool.pending after PendingTimeout
	KnownTxTimeout = 30 * time.Second // bundle tx is removed from knownTxs after KnownTxTimeout
)
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

**File:** kaiax/gasless/impl/tx_pool.go (L175-179)
```go
	// tx.deadline >= currentTimestamp
	deadline := swapArgs.Deadline
	if deadline.Cmp(g.Chain.CurrentBlock().Time()) < 0 {
		return fmt.Errorf("insufficient deadline: deadline=%s, want=%s", deadline.String(), g.Chain.CurrentBlock().Time().String())
	}
```

**File:** kaiax/gasless/impl/tx_pool.go (L252-290)
```go
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

**File:** kaiax/gasless/impl/getter.go (L203-266)
```go
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

**File:** kaiax/gasless/impl/api.go (L97-123)
```go
	// Check if the transactions form a valid gasless transaction
	// Case 1: A single swap transaction
	if len(txs) == 1 {
		swapTx := txs[0]
		if !s.b.IsSwapTx(swapTx) {
			return ToResponse(errors.New("transaction is not a swap transaction"))
		}

		return ToResponse(s.b.VerifyExecutable(nil, swapTx))
	}

	// Case 2: An approve transaction followed by a swap transaction
	if len(txs) == 2 {
		approveTx := txs[0]
		swapTx := txs[1]

		if !s.b.IsApproveTx(approveTx) {
			return ToResponse(errors.New("first transaction is not an approve transaction"))
		}

		if !s.b.IsSwapTx(swapTx) {
			return ToResponse(errors.New("second transaction is not a swap transaction"))
		}

		err := s.b.VerifyExecutable(approveTx, swapTx)
		return ToResponse(err)
	}
```
