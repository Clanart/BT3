### Title
Front-running / price-manipulation DoS and fund loss in `kaiax/gasless` swap-for-gas settlement - (File: `kaiax/gasless/impl/tx_pool.go`, `kaiax/gasless/impl/getter.go`)

### Summary
The `kaiax/gasless` module (KIP-247) lends KAIA to a gas-less user via an unconditional `LendTx` and expects the user's `GaslessSwapTx` to repay the block proposer by swapping tokens for KAIA through a whitelisted `GaslessSwapRouter`. The swap's required input amount is validated against the AMM's *current* exchange rate only at tx-pool admission time. Because `LendTx` and `GaslessSwapTx` are two independent transactions (not one atomic call), an attacker who moves the underlying pool price between admission and block inclusion can make the swap revert on-chain due to slippage protection, while the `LendTx` payment to the user has already unconditionally succeeded — analogous to the reported `InfiltrationPeriphery.healWithEth` front-run, but here the victim of value loss is the block proposer rather than a healing user.

### Finding Description
`checkBalanceForSwap` enforces slippage/amount rules purely from the current chain state at tx-pool admission time: [1](#0-0) 

This mirrors exactly the pattern flagged in the report: an on-chain price quote (`GetAmountIn`) is fetched once, and the caller relies on it holding by the time of execution, with only a `minAmountOut`/`amountRepay` guard enforced inside the router at execution: [2](#0-1) 

Crucially, the KIP-247 flow splits the "loan" and the "repayment" into two separate, non-atomic transactions:
- `MakeLendTx`/`GetLendTxGenerator` builds a plain native-value transfer from the proposer to the user for `lendAmount` — this always succeeds once mined, with no dependency on the swap's outcome: [3](#0-2) [4](#0-3) 

- `ExtractTxBundles` only groups `[LendTxGenerator, ApproveTx, SwapTx]` into a bundle and places it relative to a `TargetTxHash`; it performs no re-check that the AMM price at the time of *actual inclusion* still satisfies the slippage the sender committed to when the tx was originally admitted to the pool: [5](#0-4) 

Because block assembly and bundle incorporation occur after arbitrary intervening transactions (including a searcher's own DEX trade against the same token pool, submitted with normal priority or via the `auction` module targeting an earlier transaction), an attacker can shift the pool's reserves so that, by the time `swapForGas` executes, the realized output is below `swapArgs.MinAmountOut`/`AmountRepay`. The swap call then reverts (as guaranteed by the router/AMM's own `amountOutMin` check), but the preceding `LendTx` in the same bundle has already unconditionally transferred `lendAmount` KAIA to the user — this transfer is not reversible by the failure of the subsequent `SwapTx`.

### Impact Explanation
- The block proposer's lent KAIA (`lendAmount`, i.e., the fee-delegation float) is not repaid when the swap reverts, since repayment (`amountRepay`) only happens inside a successful `swapForGas` call; this is a direct, non-recoverable value loss for the proposer — a fee-delegation abuse / gasless settlement theft, not merely a low-impact DoS.
- Even a single-block attacker (any unprivileged sender able to submit an ordinary swap transaction against the same AMM pool used by the target token) can repeatedly grief specific users/tokens, since the gasless swap's viability check at admission time provides no protection against price shifts that occur before actual block inclusion.
- This also produces user-facing DoS: legitimate users' `GaslessSwapTx` reverts, consuming their nonce and forcing them to resubmit, while gaining nothing (their token/approve state is unaffected, but the attempt is wasted and the proposer absorbs the loss).

### Likelihood Explanation
Any unprivileged party who can submit a normal (or auction-bid) transaction against the same DEX pool backing the whitelisted `GaslessSwapRouter`/token pair can attempt this, requiring no special permissions, validator status, or malicious node behavior — only the ability to place a transaction ahead of the target's gasless bundle in the same or an earlier block. This is a routine, transaction-only capability explicitly reachable in scope (gasless module, pool admission, and public transaction submission).

### Recommendation
- Re-validate the swap's expected output against the *current* execution-time state immediately before including the `[LendTx, ApproveTx, SwapTx]` bundle in the block (e.g., re-run `checkBalanceForSwap`/`GetAmountIn` against the block-building state, not only the tx-pool admission state), and drop/exclude bundles that would revert.
- Make the loan conditional on swap success where possible (e.g., structure `LendTx` and repayment such that the proposer's KAIA transfer and the swap's repayment are enforced atomically, or add a settlement step that reclaims/reverts the lend if the swap fails within the same block).
- Consider tightening the `IsExecutable`/`VerifyExecutable` checks to include a price-impact/staleness bound so that stale quotes captured well before inclusion cannot be exploited by an intervening trade.

### Proof of Concept
1. User submits `ApproveTx` + `GaslessSwapTx` (`token -> WKAIA`) with `MinAmountOut = AmountRepay` computed from the pool's reserves at submission time (per `checkBalanceForSwap`).
2. Attacker observes the pending gasless bundle and submits an ordinary large swap of the same token pair (or wins an auction bid targeting a transaction that precedes the bundle) to shift the pool reserves so the realized output for the user's `AmountIn` falls below `MinAmountOut`.
3. The block proposer's `ExtractTxBundles` still builds `[LendTxGenerator, ApproveTx, SwapTx]` from the pool without re-checking against the now-shifted reserves (`kaiax/gasless/impl/builder.go` lines 28-72).
4. In the block, `LendTx` succeeds unconditionally, transferring `lendAmount` KAIA to the user (`kaiax/gasless/impl/getter.go` lines 268-313, 346-367).
5. `SwapTx`'s `swapForGas` call reverts due to the AMM's `amountOutMin`/slippage check because the attacker's trade dropped the realized output below `AmountRepay`.
6. The proposer's `lendAmount` KAIA transfer stands (never repaid), while the swap and its repayment never occur — the proposer has been drained by the manipulated price, and the user's `GaslessSwapTx` attempt is wasted.

### Citations

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

**File:** kaiax/gasless/impl/tx_pool.go (L128-141)
```go
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
