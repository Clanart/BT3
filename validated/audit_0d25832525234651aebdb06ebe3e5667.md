### Title
Gasless swap admission checks are stale by block-inclusion time, allowing proposer to lend gas against a no-longer-valid balance/price condition - (File: kaiax/gasless/impl/builder.go)

### Summary
The Sherlock report's bug class is: a solvency/eligibility condition is verified once at an early step (`liquidatePartyA` requiring `availableBalance < 0`), but a later step in the same multi-step flow (`setSymbolsPrice`) is allowed to act on stale state without re-verifying that same condition, letting the actor exploit the gap between check-time and act-time. The Kaia gasless module has the same structural pattern: `GetCheckBalance()` performs `checkBalanceForSwap`/`checkBalanceForApprove` (token balance, allowance, and price-derived `amountIn` sufficiency) only at transaction-pool admission time [1](#0-0) , but the actual bundling decision that causes the proposer to fund a `LendTx` happens later in `ExtractTxBundles`, which re-checks only structural conditions (`IsExecutable`/`VerifyExecutable`: nonce sequencing, sender match, token match, repay amount arithmetic) and does **not** re-invoke the balance/allowance/price checks [2](#0-1) [3](#0-2) .

### Finding Description
`checkBalanceForSwap` enforces at admission time that `swapArgs.AmountIn >= gsr.getAmountIn(minAmountOut)` (a live AMM-price-dependent quantity), token allowance, and token balance [4](#0-3) . This check is performed once when the transaction is added to the pool via `GetCheckBalance()` [1](#0-0) . Per the module's own documentation, "Sender balance check is omitted for gasless transactions" during subsequent `Ready`/promotion processing [5](#0-4) . Transactions can then sit in the queue/pending pool for up to `QueueTimeout`/`PendingTimeout` (10 seconds each) before being promoted [6](#0-5) .

At block-building time, `ExtractTxBundles` calls `IsExecutable`/`VerifyExecutable`, which only checks nonce ordering, sender/token consistency, approve-amount sufficiency, and repay-amount arithmetic (SP1–SP4) — it never re-queries the router's live exchange rate, the sender's current token balance, or current allowance [7](#0-6) [2](#0-1) . Because the block proposer prepends a `LendTx` that unconditionally transfers native KAIA gas funds to the sender as part of the bundle before the swap executes and repays [8](#0-7) , and because `lendAmount`/`repayAmount` are fixed at bundle-construction time from the transactions' declared gas parameters rather than re-derived from current AMM state [9](#0-8) , a window exists where the price-dependent sufficiency condition validated at admission may no longer hold when the bundle is actually included several blocks later, without any re-verification comparable to `liquidatePartyA`'s fresh re-check of `availableBalance < 0` immediately before acting.

### Impact Explanation
If the underlying AMM price/reserves move unfavorably between admission-time `checkBalanceForSwap` and inclusion-time `ExtractTxBundles`/`IsExecutable`, the proposer still lends gas via `LendTx` based on stale sufficiency data. If the swap subsequently fails to produce enough output to satisfy `minAmountOut`/`amountRepay` inside the `GaslessSwapRouter` contract execution, the swap transaction reverts on-chain, but the `LendTx` has already unconditionally transferred value to the sender within the same bundle. This is a fee-delegation/gasless-settlement risk: the proposer (fee lender) can be left funding gas for a sender whose repay guarantee is no longer backed by sufficient balance/allowance/price conditions, since the only enforcement mechanism is a check performed at a point in time disconnected from the point of actual value transfer.

### Likelihood Explanation
Likelihood is moderate: it requires (a) a gasless approve/swap pair to sit in the pool near the `QueueTimeout`/`PendingTimeout` boundary (up to 10 seconds) [6](#0-5) , and (b) meaningful price/balance/allowance movement in that window — realistic for volatile tokens or if the sender/attacker deliberately front-runs their own allowance/balance to pass admission and then drains it before inclusion, gaming the proposer similarly to how the original report describes gaming price re-checks around a liquidation window.

### Recommendation
Re-invoke `GetCheckBalance()` (or an equivalent live balance/allowance/price sufficiency check) immediately before or within `ExtractTxBundles`/`IsExecutable`, at the point where the `LendTx` is actually generated and the bundle is committed to the block, rather than relying solely on the stale admission-time check performed in the transaction pool.

### Proof of Concept
1. Attacker submits `ApproveTx` + `SwapTx` for a gasless swap; at admission, `checkBalanceForSwap` confirms `AmountIn >= gsr.getAmountIn(minAmountOut)` and sufficient token balance/allowance [4](#0-3) .
2. The pair sits in the pool (up to `QueueTimeout`/`PendingTimeout`) [6](#0-5) .
3. Attacker transfers away the approved token balance or the AMM price moves such that `AmountIn` is no longer sufficient for `minAmountOut`.
4. At block building, `ExtractTxBundles` → `IsExecutable`/`VerifyExecutable` only re-checks nonce/token/repay-amount arithmetic, not balance/allowance/price sufficiency, so the bundle (including `LendTxGenerator`) is still produced [10](#0-9) [11](#0-10) .
5. `LendTx` transfers gas funds to the sender; `SwapTx` then reverts on-chain due to insufficient balance/output, leaving the proposer's lent value unbacked by a successful repayment.

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

**File:** kaiax/gasless/impl/tx_pool.go (L107-173)
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

**File:** kaiax/gasless/README.md (L25-27)
```markdown
#### Balance check

Sender balance check is omitted for gasless transactions (see `GetCheckBalance()`).
```
