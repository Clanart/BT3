### Title
Gasless module's `Disable`/paused status is exposed but not enforced in the tx-pool promotion and execution logic - (File: kaiax/gasless/impl/tx_pool.go, kaiax/gasless/impl/init.go)

### Summary
The `GaslessModule` maintains a disabled/paused flag (`GaslessConfig.Disable`), which is set automatically at startup for consensus nodes lacking sufficient balance, and is exposed via `IsDisabled()`. However this status is checked only in the informational `debug_isGaslessTx`/`GaslessInfo` RPC methods, not in the actual transaction-pool readiness and bundling logic that determines whether gasless transactions are promoted and lent gas. This mirrors the CoreOracle pattern: a pause flag exists, but the functions that actually drive business logic never consult it, so failure (or unintended success) surfaces only deep in the flow, or not at all.

### Finding Description
`IsDisabled()` is defined in [1](#0-0)  and is set when a consensus node's balance is insufficient to act as a lender: [2](#0-1) .

The only place `IsDisabled()` is read is the RPC API used for external introspection: [3](#0-2) .

None of the actual transaction-pool decision points check `IsDisabled()`:
- `PreAddTx`, which controls whether a bundle transaction is queued, only checks `IsBundleTx` and queue limits, never the disabled flag: [4](#0-3) .
- `IsReady`/`isReady`, which decides promotion of approve/swap transactions to pending and triggers `knownTxs.add`, likewise never checks `IsDisabled()`: [5](#0-4) .
- `isApproveTxReady`/`isSwapTxReady`, which ultimately call `IsExecutable`/`VerifyExecutable` to authorize the gasless bundle (approve + swap + lend), also omit any disabled/paused check: [6](#0-5)  and [7](#0-6) .

Because the module's pause status was designed as an emergency/safety flag (e.g., insufficient lender balance) but is never enforced in the code path that actually decides which transactions are ready, bundled, and lent gas for, the block-building logic can continue accepting and processing GaslessApproveTx/GaslessSwapTx pairs and issuing `LendTxGenerator` funding transactions even after the module has been marked disabled. This is the same root cause class as the CoreOracle report: exposing pause state without gating the functions that rely on it, so failures (or unintended continued operation) occur deep in the execution flow rather than being rejected up front.

### Impact Explanation
If the module is disabled mid-operation (e.g., a CN's balance drops below `GaslessLenderMinBal` after `Init`, or the flag is otherwise toggled), the tx-pool promotion logic in `tx_pool.go`/`getter.go` has no gate to stop processing gasless bundles. This can lead to the CN continuing to generate `LendTxGenerator` fee-lending transactions from its own balance for users, an unintended value movement/fee-delegation abuse scenario that the pause mechanism was meant to prevent, since the check is only surfaced for external RPC callers rather than enforced internally where it matters.

### Likelihood Explanation
Any unprivileged gasless user can submit an approve+swap transaction pair through the normal RPC/tx-pool submission path. The disabled check is bypassed for free by all internal tx-pool mechanics (`PreAddTx`, `IsReady`, `VerifyExecutable`) since they never call `IsDisabled()`. No special privileges are required to trigger the affected code paths — only for the flag to have been set (e.g., automatically via low CN balance at startup), which is an operator-triggerable/environment-triggered condition, not attacker controlled, which somewhat limits likelihood versus a fully attacker-triggerable Medium/High.

### Recommendation
Add an explicit `IsDisabled()` check at the start of `PreAddTx`, `IsReady`/`isReady`, and `VerifyExecutable` (or a single gating point that all of them go through) so that when the module is disabled, gasless transactions are neither queued, promoted, nor bundled/lent for. This mirrors the CoreOracle recommendation of either gating every relevant function with the pause check or requiring all callers to check status before proceeding — but the fix belongs in the internal enforcement, not just the external status-reporting API.

### Proof of Concept
1. Configure a `GaslessModule` where `NodeType == common.CONSENSUSNODE` and the node key's balance is below `GaslessLenderMinBal`, causing `Init` to set `g.GaslessConfig.Disable = true` per [2](#0-1) .
2. Submit a valid `GaslessApproveTx` followed by a valid `GaslessSwapTx` through the standard tx-pool submission flow.
3. Observe that `PreAddTx` ( [4](#0-3) ) and `IsReady`/`isApproveTxReady`/`isSwapTxReady` ( [8](#0-7) ) never call `g.IsDisabled()`, so the bundle is still considered ready and executable via `VerifyExecutable` ( [7](#0-6) ), despite the module being marked disabled — only the `debug_isGaslessTx`/`gaslessInfo` RPC would separately report `IsDisabled() == true` to callers who choose to check it ( [3](#0-2) ).

### Citations

**File:** kaiax/gasless/impl/init.go (L87-95)
```go
	// Disable module if CN (lender) does not have sufficient balance
	if g.NodeType == common.CONSENSUSNODE {
		nodeAddr := crypto.PubkeyToAddress(opts.NodeKey.PublicKey)
		balance := g.getCurrentStateBalance(nodeAddr)
		if balance.Cmp(GaslessLenderMinBal) < 0 {
			g.GaslessConfig.Disable = true
			logger.Warn("disabling gasless module due to insufficient balance", "node", nodeAddr.Hex(), "balance", balance.String())
		}
	}
```

**File:** kaiax/gasless/impl/init.go (L100-102)
```go
func (g *GaslessModule) IsDisabled() bool {
	return g.GaslessConfig.Disable
}
```

**File:** kaiax/gasless/impl/api.go (L135-148)
```go
func (s *GaslessAPI) GaslessInfo() *GaslessInfoResult {
	s.b.gaslessInfoMu.RLock()
	defer s.b.gaslessInfoMu.RUnlock()

	at := []common.Address{}
	for addr := range s.b.allowedTokens {
		at = append(at, addr)
	}
	return &GaslessInfoResult{
		IsDisabled:    s.b.IsDisabled(),
		SwapRouter:    s.b.swapRouter,
		AllowedTokens: at,
		MaxBundleTxs:  s.b.GetMaxBundleTxsInPending(),
	}
```

**File:** kaiax/gasless/impl/tx_pool.go (L38-53)
```go
func (g *GaslessModule) PreAddTx(tx *types.Transaction, local bool) error {
	g.knownTxsMu.RLock()
	defer g.knownTxsMu.RUnlock()

	if knownTx, ok := g.knownTxs.get(tx.Hash()); ok && knownTx.elapsedPromotedOrAddedTime() < KnownTxTimeout {
		return ErrUnableToAddKnownBundleTx
	}

	if g.IsBundleTx(tx) {
		if uint(g.knownTxs.numQueue()) >= g.GetMaxBundleTxsInQueue() {
			return ErrBundleTxQueueFull
		}
		g.knownTxs.add(tx, TxStatusQueue)
	}
	return nil
}
```

**File:** kaiax/gasless/impl/tx_pool.go (L184-290)
```go
// Check promotion condition and enforce pending pool flow control.
func (g *GaslessModule) IsReady(txs map[uint64]*types.Transaction, next uint64, ready types.Transactions) bool {
	g.knownTxsMu.Lock()
	defer g.knownTxsMu.Unlock()

	tx, ok := txs[next]
	if !ok {
		return false
	}

	if !g.isReady(txs, next, ready) {
		return false
	}

	if g.IsBundleTx(tx) {
		// If prev tx is bundle tx, there's no need to check the knownTxs limit because it has been checked in the previous `IsReady()` execution.
		isPrevTxBundleTx := len(ready) != 0 && g.IsBundleTx(ready[len(ready)-1])
		if isPrevTxBundleTx {
			g.knownTxs.add(tx, TxStatusPending)
			return true
		}

		maxBundleTxsInPending := g.GetMaxBundleTxsInPending()
		if maxBundleTxsInPending != math.MaxUint64 {
			numExecutable := uint(g.knownTxs.numExecutable())

			numSeqTxs := uint(1)
			for i := next + 1; i < next+uint64(len(txs)); i++ {
				if tx, ok := txs[i]; ok && g.IsBundleTx(tx) {
					numSeqTxs++
				} else {
					break
				}
			}

			// false if there is possibility of exceeding max bundle tx num
			if numExecutable+numSeqTxs > maxBundleTxsInPending {
				logger.Trace("Not promoting a tx because of exceeding max bundle tx num", "tx", tx.Hash().String(), "numExecutable", numExecutable, "maxBundleTxsInPending", maxBundleTxsInPending)
				return false
			}
		}

		g.knownTxs.add(tx, TxStatusPending)
	}

	return true
}

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
