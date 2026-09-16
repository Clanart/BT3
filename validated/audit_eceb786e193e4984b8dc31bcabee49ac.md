### Title
Gasless swap promotion (`VerifyExecutable`) omits price/repayment-sufficiency checks that are enforced only at tx-pool admission - ([File: kaiax/gasless/impl/getter.go], [File: kaiax/gasless/impl/tx_pool.go])

### Summary
Similar to the Unitas `getLatestPrice` finding, where a computed value (price) is validated on one code path but not on another that reaches the same sensitive logic, the Kaia gasless module validates the swap's exchange-rate/repayment sufficiency (`tx.minAmountOut >= tx.amountRepay` and `tx.amountIn >= gsr.getAmountIn(minAmountOut)`) only inside `checkBalanceForSwap` (invoked once via `GetCheckBalance()` at tx-pool admission time), but the block-assembly promotion path `IsReady` → `isSwapTxReady`/`isApproveTxReady` → `IsExecutable` → `VerifyExecutable` never re-checks these values before a bundle is promoted and included in a block.

### Finding Description
`checkBalanceForSwap` in [1](#0-0)  validates:
- `tx.minAmountOut >= tx.amountRepay`
- `tx.amountIn >= gsr.getAmountIn(minAmountOut)` (current pool exchange-rate sufficiency), gated behind `ShouldCheckSwapAmount()`

This function is only reachable via `GetCheckBalance()`, which is invoked once when a gasless transaction is admitted into the pool: [2](#0-1) .

However, the function that actually decides whether a gasless bundle is *promoted for block inclusion*, `VerifyExecutable`, checks only structural conditions Sx/Ax/AP1/SP1/SP2/SP3/SP4 (token whitelisting, approve/swap linkage, nonce sequencing, and repay-amount equality) — it never re-validates `minAmountOut >= amountRepay` or the pool-derived `amountIn` sufficiency: [3](#0-2) .

`VerifyExecutable`/`IsExecutable` is the function used both:
1. During block-assembly readiness checks `isApproveTxReady`/`isSwapTxReady` (called from `IsReady`, the txpool promotion hook that gates what gets included in blocks): [4](#0-3) 
2. Via the public RPC-exposed `debug_isGaslessTx` / `IsGaslessTx` API path, which calls `VerifyExecutable` directly without ever calling `checkBalanceForSwap`: [5](#0-4) 

Because pool liquidity/price can shift between the moment a swap tx is admitted to the pool (when `checkBalanceForSwap` ran) and the later moment it is actually promoted/included in a block (when only `VerifyExecutable` runs), the repayment-sufficiency check is stale by the time inclusion is decided — mirroring exactly the reported bug class: the value threshold/sufficiency check exists on one call path but is silently skipped on another path that reaches the same sensitive state-mutating outcome.

### Impact Explanation
The gasless flow works by having the block proposer pre-fund the user's gas via a `LendTx` (generated in `GetLendTxGenerator`), with the expectation that the subsequent `SwapTx` will produce enough output token to satisfy `amountRepay` (repaying the proposer): [6](#0-5)  and [7](#0-6) . If `VerifyExecutable` (the actual gate for promotion/inclusion) does not re-verify that `minAmountOut >= amountRepay` and that `amountIn` is still sufficient at current exchange rate, a bundle whose swap can no longer produce enough output to repay the proposer's lent gas can still be promoted and included in a block. This risks the proposer's lent KAIA/gas not being repaid — a concrete value-loss/fee-delegation-abuse scenario for the block proposer, who is effectively acting as the fee-delegation counterparty in this design.

### Likelihood Explanation
Likelihood is moderate: this requires the pool price/reserves for the swap-target token to shift meaningfully between tx-pool admission and block inclusion (e.g., due to other swaps or blocks being produced while the gasless tx sits in the pool, or via the tx being resubmitted/reordered), and it depends on whether `ShouldCheckSwapAmount()`/`ShouldCheckToken()` config flags are disabled in a given deployment, and whether `checkBalanceForSwap` is re-invoked by the tx pool on every re-validation cycle (I could not fully verify all call sites of `GetCheckBalance` within `blockchain/tx_pool.go` due to iteration limits, so I cannot confirm with certainty whether the pool always re-checks balance validity right before promotion, which would mitigate this).

### Recommendation
Add the same sufficiency checks performed in `checkBalanceForSwap` (`minAmountOut >= amountRepay`, and re-querying `gsr.getAmountIn(minAmountOut)` against current pool state) directly into `VerifyExecutable`, so that the promotion/inclusion decision path enforces the identical invariant as the tx-pool admission path, removing the asymmetry between the two validation flows.

### Proof of Concept
1. Submit a valid `GaslessApproveTx` + `GaslessSwapTx` pair when pool price is favorable; `checkBalanceForSwap` passes at admission (`amountIn >= gsr.getAmountIn(minAmountOut)` and `minAmountOut >= amountRepay`).
2. Before the transaction pair is promoted/included, other swaps shift the pool's exchange rate such that `minAmountOut` no longer covers `amountRepay` at current price.
3. At promotion time, `IsReady` → `isSwapTxReady` → `IsExecutable` → `VerifyExecutable` is called, which does not re-check `minAmountOut >= amountRepay` or price sufficiency (only checks nonce/token/approve/repay-amount-equality conditions), so the bundle is still promoted.
4. Bundle (`LendTx`, `ApproveTx`, `SwapTx`) is included in the block; the swap executes but produces insufficient token output relative to the amount the proposer expected to be repaid, resulting in the proposer's lent gas not being fully repaid. [3](#0-2) [1](#0-0)

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

**File:** kaiax/gasless/impl/tx_pool.go (L107-142)
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

**File:** kaiax/gasless/impl/getter.go (L346-367)
```go
func lendAmount(approveTxOrNil, swapTx *types.Transaction) *big.Int {
	r := new(big.Int)

	// R2 = ApproveTx.Fee() if exists
	if approveTxOrNil != nil {
		r.Add(r, approveTxOrNil.Fee())
	}

	// R3 = SwapTx.Fee()
	r.Add(r, swapTx.Fee())

	// LendAmount = R2 + R3
	return r
}

func repayAmount(approveTxOrNil, swapTx *types.Transaction) *big.Int {
	// R1 = LendTx.Fee() = SwapTx.GasPrice() * TxGas
	r1 := new(big.Int).Mul(swapTx.GasPrice(), new(big.Int).SetUint64(params.TxGas))

	// RepayAmount = R1 + R2 + R3
	return new(big.Int).Add(r1, lendAmount(approveTxOrNil, swapTx))
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
