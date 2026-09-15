Based on my analysis, `VerifyExecutable`/`IsExecutable` — the function that gates gasless swap execution (analogous to `_validateSignature` in the report) — checks nonce sequencing, token/sender matching, and repay amount, but **does not check the swap's `deadline`** against the current time. That check exists only in `checkBalanceForSwap` [1](#0-0)  which is wired to the tx pool's `GetCheckBalance` hook, invoked at admission time [2](#0-1) . However, `IsGaslessTx` API (used by RPC callers to validate a gasless-tx pair before submission/relay) and `IsExecutable`/`VerifyExecutable` (used by `IsReady` for pool promotion and `ExtractTxBundles` for bundling into a block) call only `VerifyExecutable`, never `checkBalanceForSwap`. [3](#0-2) [4](#0-3) [5](#0-4) 

### Title
Gasless swap bundling/execution gate (`VerifyExecutable`) omits deadline validation, allowing stale swaps to bundle and execute at expired price parameters - (File: kaiax/gasless/impl/getter.go)

### Summary
`VerifyExecutable` in `kaiax/gasless/impl/getter.go` is the authoritative "signature/eligibility" check for whether an approve+swap (or standalone swap) transaction pair is a valid, bundle-able gasless transaction. It validates sender/token/amount/nonce/repay conditions but never validates `SwapArgs.Deadline` against the current block time. The deadline check exists only in a separate function, `checkBalanceForSwap` (`kaiax/gasless/impl/tx_pool.go:175-179`), which is invoked from `GetCheckBalance` on transaction pool admission but is not called by `VerifyExecutable`, `IsExecutable`, `ExtractTxBundles` (block bundling path), or the public RPC method `IsGaslessTx`.

### Finding Description
The gasless swap flow lets an unprivileged sender submit an ERC20 `approve` + `swapForGas` transaction pair (or a standalone swap) that the block proposer bundles via `ExtractTxBundles` and pays gas for out of the swap proceeds. Eligibility for bundling/inclusion is decided by `g.IsExecutable(approveTx, tx)` → `VerifyExecutable`, which checks:
- decode/whitelist checks for swap (Sx)
- decode/whitelist checks for approve (Ax)
- same sender (AP1)
- same token (SP1)
- sufficient approve amount (SP2)
- sequential nonce and current-nonce alignment (SP3)
- correct repay amount (SP4)

There is no check comparing `swapArgs.Deadline` to the current chain time inside `VerifyExecutable`. The deadline is only enforced in `checkBalanceForSwap`, a *different* function used solely by the tx-pool's balance/eligibility pre-check hook (`GetCheckBalance`), which runs once at initial submission time, not at promotion (`isReady`/`isApproveTxReady`/`isSwapTxReady` call `IsExecutable`, not `checkBalanceForSwap`) or at final bundling into a block (`ExtractTxBundles` also calls `IsExecutable`, not `checkBalanceForSwap`).

Because tx-pool promotion and block-bundling happen at a later time than initial submission (transactions can sit in queue/pending for up to `QueueTimeout`/`PendingTimeout` = 10 seconds each, and potentially longer under load or reorgs via `PreReset`/`PostReset`), a swap whose `deadline` has already elapsed by the time it is promoted or bundled can still pass `IsExecutable`/`VerifyExecutable` and be included in a block, since the deadline is never re-checked at that stage.

### Impact Explanation
This mirrors the reported bug class: a time-bound authorization (the swap's `deadline`, analogous to `validUntil`) is checked at one point (initial admission) but not re-validated at the actual point of execution/inclusion decision (`VerifyExecutable`, used for promotion and bundling). An attacker (a malicious block proposer, MEV searcher, or even the gasless-relay operator) could deliberately delay inclusion of a stale gasless swap (whose price parameters — `minAmountOut`, `amountRepay` — were computed against an earlier exchange rate) past its intended deadline and still have it bundled and executed, since the bundling code path (`ExtractTxBundles`) and readiness path (`IsReady`) never re-check the deadline. This can result in the sender getting a worse swap execution price than intended (fund loss), analogous to Alice's swap example in the source report, since KIP-247 GaslessSwapRouter deadline semantics are meant to prevent execution outside the sender-approved timeframe.

### Likelihood Explanation
Medium likelihood: the swap sender does not control transaction ordering/timing in the pool or by the block proposer. Under normal conditions (fast blocks), the window between submission and inclusion is short, but `QueueTimeout`/`PendingTimeout` allow bundle txs to sit in the pool up to several seconds, and a proposer could intentionally delay bundling a valid-looking (per `VerifyExecutable`) but deadline-expired transaction. No cryptographic or admin privilege is required — any transaction sender using the gasless swap path is exposed, and a block-builder/validator could exploit the missing re-check without violating any other rule enforced in-band.

### Recommendation
Add an explicit deadline check inside `VerifyExecutable` (`kaiax/gasless/impl/getter.go`), e.g., comparing `swapArgs.Deadline` against `g.Chain.CurrentBlock().Time()` (as already done in `checkBalanceForSwap`), so that the check is enforced consistently at every gate that decides swap eligibility: initial tx-pool admission (`GetCheckBalance`), promotion (`IsReady`/`isApproveTxReady`/`isSwapTxReady`), and final block bundling (`ExtractTxBundles`/`IsBundleTx`). This ensures a swap cannot be bundled/executed once its deadline has passed, regardless of how long it has been sitting in the pool.

### Proof of Concept
1. A sender submits `approveTx` (nonce N) + `swapTx` (nonce N+1) with `SwapArgs.Deadline = T`.
2. At submission time (`t0 < T`), `GetCheckBalance`/`checkBalanceForSwap` passes since `T >= currentBlockTime`.
3. The transaction pair sits in the tx pool queue/pending (up to `QueueTimeout`/`PendingTimeout`, or longer through reorg-driven `PreReset`/`PostReset` cycles).
4. At time `t1 > T` (deadline expired), the block proposer calls `ExtractTxBundles`, which calls `g.IsExecutable(approveTx, swapTx)` → `VerifyExecutable`. Since `VerifyExecutable` never checks `Deadline`, all SP1–SP4/Ax/Sx conditions still pass (nonce, token, amounts, repay unchanged), so the pair is bundled into a `builder.Bundle` and included in the block.
5. The swap executes on-chain past its intended deadline, at potentially unfavorable market conditions, violating the sender's declared time-bound intent — with no on-chain (KIP-247 router-side) or bundling-side rejection at the eligibility-check layer that decides inclusion.

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

**File:** kaiax/gasless/impl/tx_pool.go (L175-179)
```go
	// tx.deadline >= currentTimestamp
	deadline := swapArgs.Deadline
	if deadline.Cmp(g.Chain.CurrentBlock().Time()) < 0 {
		return fmt.Errorf("insufficient deadline: deadline=%s, want=%s", deadline.String(), g.Chain.CurrentBlock().Time().String())
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
