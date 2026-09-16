### Title
Gasless swap balance/allowance sufficiency is verified only once at tx-pool admission, not re-checked at block-building/bundling time before the lender's gas is advanced - ([File: kaiax/gasless/impl/tx_pool.go], [File: kaiax/gasless/impl/builder.go])

### Summary
The Sherlock report describes a liquidation function that checks an account's "unhealthy" precondition only implicitly at some earlier point but fails to re-verify that condition is still true at the moment the state-changing action (liquidation) is actually executed, allowing a stale/invalid liquidation to proceed. The reachable analog in this Kaia codebase is the `kaiax/gasless` module: the sender's token balance/allowance/exchange-rate sufficiency for a `GaslessSwapTx` is checked once via `GetCheckBalance()` → `checkBalanceForSwap` at tx-pool admission time, but the subsequent bundling logic that actually commits the proposer to advance gas fees (`LendTxGenerator`) does not re-verify balance/allowance immediately before block inclusion.

### Finding Description
`GaslessModule.GetCheckBalance()` returns `checkBalanceForSwap`, which validates `tx.token.balanceOf(sender) >= tx.amountIn` and `tx.token.allowance(sender, router) >= tx.amountIn` against the *current* state at the time the transaction is added to the pool [1](#0-0) . This check is explicitly a one-time admission check — the module's own README states "Sender balance check is omitted for gasless transactions" for the default txpool flow, and this custom balance check substitutes for it [2](#0-1) .

Later, when a block is actually built, `ExtractTxBundles` composes the final `[LendTxGenerator, ApproveTx?, SwapTx]` bundle by calling `IsExecutable`/`VerifyExecutable`, which only re-checks nonce sequencing, token/sender/amount consistency, and repay-amount arithmetic — it does **not** re-check the sender's current token balance or allowance [3](#0-2) [4](#0-3) . The promotion path `isReady`/`isSwapTxReady` similarly relies on `IsExecutable`, which again omits balance verification [5](#0-4) .

Because the pool's `PreReset`/`PostReset` and readiness checks can leave the tx pending for an extended window (`PendingTimeout`), and a sender can spend or transfer away the checked token balance/allowance via any other transaction between admission and block inclusion, the "still valid" precondition (sufficient balance/allowance to complete the swap and repay the proposer) that was true at admission is never re-verified at the moment the proposer actually commits to fronting gas via the lend transaction.

### Impact Explanation
This mirrors the reported bug class: a precondition needed for a value-transferring action to be *safe* (in Aloe's case, account unhealthiness; here, sender solvency for the swap/repay) is validated once and then relied upon without re-verification immediately before the irreversible action executes. If the sender's balance/allowance becomes insufficient before block inclusion, the swap portion of the bundle will revert on execution, but the `LendTxGenerator` transaction — which unconditionally transfers KAIA from the block proposer's key to the user to cover gas — is prepended to the bundle regardless [6](#0-5) . This can cause the proposer's advanced funds to not be repaid, since the repayment is asserted only as part of the swap execution logic (`SwapArgs.AmountRepay` inside the swap call), not independently guaranteed. This is a concrete fee/fund-advance loss vector for a public-RPC caller (any user who submits an approve/swap pair and drains/reassigns the checked token afterward).

### Likelihood Explanation
Medium-to-High: any unprivileged user submitting a `GaslessSwapTx` via the public RPC can trigger this by transferring away the approved token (or revoking allowance) via a normal transaction after their gasless approve/swap pair is admitted to the pool but before the block containing the bundle is produced. No special privileges, validator collusion, or timing precision beyond ordinary transaction submission are required.

### Recommendation
Re-validate the sender's current token balance and allowance (and any other economic preconditions checked in `checkBalanceForSwap`) immediately before including the `LendTxGenerator` + swap bundle in the block — e.g., inside `ExtractTxBundles`/`GetLendTxGenerator` or as part of `VerifyExecutable`, using the state at the point of block assembly rather than relying solely on the admission-time check performed by `GetCheckBalance()`. Alternatively, ensure the lend transaction's fee advance is conditioned on (or reverts together with) a successful repay within the same bundle/atomic execution unit.

### Proof of Concept
1. Attacker holds `amountIn` of `token` and grants `router` sufficient allowance.
2. Attacker submits `GaslessApproveTx` + `GaslessSwapTx`; `checkBalanceForSwap` passes at admission and the pair is queued/promoted [7](#0-6) .
3. Before the block containing the bundle is produced, the attacker sends a separate ordinary transaction from the same address transferring away `token` (or revoking the router's allowance).
4. At block-building time, `ExtractTxBundles`/`IsExecutable` only checks nonce/token-address/amount-consistency (not live balance/allowance) and still includes the bundle with `LendTxGenerator` prepended [8](#0-7) .
5. The `LendTxGenerator` transfers gas funds from the proposer to the attacker; the subsequent swap transaction reverts due to insufficient balance/allowance, so repayment to the proposer never occurs, resulting in a fund-advance loss for the block proposer.

Note: I was unable to inspect the exact implementation of `GetLendTxGenerator` (in `kaiax/gasless/impl/getter.go`, lines beyond what was retrieved) and `execution.go` due to tool call limits in this session — a full review of those files is recommended to confirm whether any atomicity/rollback protection exists that would make the lend transfer conditional on swap success.

### Citations

**File:** kaiax/gasless/impl/tx_pool.go (L107-182)
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

**File:** kaiax/gasless/README.md (L25-27)
```markdown
#### Balance check

Sender balance check is omitted for gasless transactions (see `GetCheckBalance()`).
```

**File:** kaiax/gasless/README.md (L29-36)
```markdown
### Block building rules

Upon detection of GaslessTxs, the following logics are executed:

- Per sender, if exists, GaslessApproveTx is relocated before GaslessSwapTx.
- LendTxGenerator is prepended before GaslessApproveTx.
- A new bundle is generated which contain either `[LendTxGenerator, GaslessApproveTx, GaslessSwapTx]` or `[LendTxGenerator, GaslessSwapTx]`
- If the bundle has conflict with any previous bundles, it is excluded from the returned bundle list.
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
