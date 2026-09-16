### Title
Gasless swap balance/allowance checks are validated only at tx-pool admission time and not re-verified when bundling at block assembly, allowing a lent gas fee to be irrecoverably lost if the SwapTx later reverts - ([File: kaiax/gasless/impl/tx_pool.go], [File: kaiax/gasless/impl/builder.go], [File: kaiax/gasless/impl/getter.go])

### Summary
Similar to the Hubble finding — where a margin check is skipped for the "reducing" path even though fees are still charged and can push the trader below the required margin — the Kaia gasless module performs its correctness checks (`VerifyExecutable`/`checkBalanceForSwap`) against the *current* chain-head state at admission time (`IsReady`/`isSwapTxReady`), and reuses the same non-atomic check `IsExecutable` at block-building time in `ExtractTxBundles` [1](#0-0) , without re-validating against the state actually produced by the just-prepended `LendTxGenerator` transaction or by any other transaction that may alter the sender's ERC20 balance/allowance between admission and inclusion. If the swap ultimately reverts on execution (e.g. balance/allowance changed, price moved, or `minAmountOut`/`amountRepay` invariants broken), the `LendTx` — which unconditionally transfers `lendAmount` KAIA to the sender before the swap runs — still succeeds and irrevocably transfers value to the sender, while the repayment logic inside `swapForGas` (which is expected to reclaim the lent KAIA) never executes because the whole swap call reverted.

### Finding Description
The gasless flow is: `LendTxGenerator` (funds sender with KAIA) → optional `ApproveTx` → `SwapTx` (which is expected to repay the lent amount via `swapForGas`). These are bundled together by `ExtractTxBundles` [2](#0-1) , using `GetLendTxGenerator(approveTxs[addr], tx)` which always creates a value-transfer of `lendAmount(approveTxOrNil, swapTx)` to the sender [3](#0-2) .

The correctness pre-conditions for the swap (minAmountOut ≥ amountRepay, required amountIn from AMM quote, allowance, balance, deadline) are checked in `checkBalanceForSwap`, which is only invoked as the tx-pool's alternate balance check (`GetCheckBalance`) [4](#0-3) , and the structural pattern condition (`IsExecutable`/`VerifyExecutable`) is invoked both at promotion time (`isSwapTxReady`) [5](#0-4)  and again at block-assembly time in `ExtractTxBundles` [6](#0-5) . Both call sites query the current chain-head state (via `g.Chain`, `getCurrentStateNonce`, `BlockchainContractBackend`) [7](#0-6) , not the state that will actually exist immediately before the swap executes within the assembled block (i.e., after `LendTx` and any preceding transactions in the block have been applied). None of these checks are re-verified atomically inside the same state transition as the swap itself; the checks and the state-changing/fee-charging action are decoupled exactly as in the Hubble bug, where `assertMarginRequirement` is checked against stale conditions instead of the post-fee state.

The README itself documents that the sender balance check is intentionally "omitted" for gasless transactions and relies on this lending mechanism [8](#0-7) , reinforcing that no independent balance safety net exists on the KAIA side; the entire safety of the lender's funds rests on the swap transaction succeeding and repaying via `swapForGas`. If any condition drifts between the check time and execution time — e.g., token price movement dropping `AmountIn`'s realizable output below `AmountRepay`, or the sender's allowance/balance being altered by an intervening ERC20 transfer that isn't detectable at either check point — the `SwapTx` reverts, but the preceding `LendTx` in the same bundle has already unconditionally paid out `lendAmount` in KAIA to the sender with no compensating repayment.

### Impact Explanation
If the swap fails after the lend succeeds, the block proposer (the lender, funded from `NodeKey`) permanently loses the lent KAIA (`lendAmount`), i.e., unauthorized value movement from the fee-delegation counterparty (proposer) to the gasless sender with no repayment. This is a concrete value-loss/fee-delegation abuse scenario reachable by any unprivileged gasless-swap sender crafting or timing a transaction such that the swap fails after admission but the lend still executes.

### Likelihood Explanation
Requires the transaction pool's/builder's pre-checks to pass (so IsExecutable returns true) while conditions have changed by inclusion time — plausible via price/allowance/balance changes between mempool admission and block assembly (multiple blocks may elapse while a bundle sits in queue/pending, given `QueueTimeout`/`PendingTimeout` of 10 seconds) [9](#0-8) , or via an attacker deliberately manipulating their own ERC20 balance/allowance or AMM price between crafting the transaction and its inclusion. This does not require a malicious node/validator/peer — a normal unprivileged sender submitting gasless transactions can trigger it.

### Recommendation
Ensure the `LendTx` payout and the swap's repayment are enforced atomically with respect to the state actually consumed at execution time, e.g., by re-validating `VerifyExecutable`/`checkBalanceForSwap` against the exact pre-state that will be used when the bundle is applied (post-LendTx, pre-SwapTx) rather than the chain-head state at check time, and/or by making the lend conditional on the swap's success within the same atomic state transition (so a reverted `SwapTx` also reverts the `LendTx`'s value transfer), consistent with how `assertMarginRequirement` should be re-checked after any fee-consuming state change rather than only before it.

### Proof of Concept
1. Sender submits `ApproveTx` (or none) + `SwapTx` satisfying `VerifyExecutable` at the time of tx-pool admission (using state at block N).
2. Between admission and inclusion (bundle sits in pending/queue for up to 10s per `PendingTimeout`/`QueueTimeout`), the sender transfers away or reduces their ERC20 token balance/allowance in a separate ordinary transaction, or the AMM price for the token moves such that `AmountIn` no longer yields `MinAmountOut`.
3. At block assembly, `ExtractTxBundles` re-checks `IsExecutable(approveTx, swapTx)` against the (still slightly stale, or freshly-manipulated) chain state and returns true, producing bundle `[LendTxGenerator, ApproveTx, SwapTx]`.
4. During block execution, `LendTx` succeeds and transfers `lendAmount` KAIA to the sender.
5. `SwapTx` calls `swapForGas`, which reverts due to the now-insufficient balance/allowance/price condition.
6. The `LendTx` value transfer to the sender is already final; the repayment logic inside the reverted `swapForGas` never executes, and the proposer/lender's KAIA is unrecoverable.

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

**File:** kaiax/gasless/impl/tx_pool.go (L33-35)
```go
	QueueTimeout   = 10 * time.Second // bundle tx is removed from txpool.queue after QueueTimeout
	PendingTimeout = 10 * time.Second // bundle tx is removed from txpool.pending after PendingTimeout
	KnownTxTimeout = 30 * time.Second // bundle tx is removed from knownTxs after KnownTxTimeout
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

**File:** kaiax/gasless/impl/tx_pool.go (L269-290)
```go
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
