### Title
Gasless swap price staleness between tx-pool admission and block-execution can cause `LendTx` fee credit to be lost when the paired `SwapTx` reverts - ([File: kaiax/gasless/impl/tx_pool.go])

### Summary
The `kaiax/gasless` module quotes the AMM exchange rate once, at tx-pool admission time, via `routerContract.GetAmountIn(nil, token, minAmountOut)` in `checkBalanceForSwap` [1](#0-0)  and only checks `minAmountOut >= amountRepay` [2](#0-1) , but the actual repayment amount owed to the fee-lending proposer is fixed at admission time by `repayAmount()` using `swapTx.GasPrice() * TxGas` [3](#0-2) , while the on-chain `swapForGas` execution happens later against the router's *live* AMM price. If the AMM price for the token moves between admission-time check and block-inclusion execution (analogous to Smilee's `swapPrice` vs `oraclePrice` divergence), the on-chain swap can yield less output than the declared `minAmountOut`/`amountRepay`, causing `swapForGas` to revert — but the `LendTx` that already credited the sender with KAIA (`lendAmount`) is a separate, already-mined transaction and is not rolled back.

### Finding Description
The gasless flow bundles `[LendTxGenerator, GaslessApproveTx(optional), GaslessSwapTx]` [4](#0-3) . `GetLendTxGenerator` creates and signs a real value-transfer transaction that sends `lendAmount(approveTxOrNil, swapTx)` KAIA to the swap sender before the swap executes [5](#0-4) [6](#0-5) . `VerifyExecutable`/`checkBalanceForSwap` only statically validate that the *declared* `amountRepay` equals `repayAmount()` and that, at admission time, `AmountIn >= GetAmountIn(minAmountOut)` from the router's current reserves [7](#0-6) [1](#0-0) . This is the same pattern as the Smilee `PositionManager::mint()` bug: a premium/output amount computed off one price source (at admission time) is later checked against a different, possibly worse, price at actual settlement time (inside `swapForGas`, which internally performs `getAmountsOut`/repay checks against the router's live reserves). If the pool price moves unfavorably between admission and block-building — which any market participant or the sender itself can influence with an ordinary swap earlier in the same block — the `swapForGas` call reverts on-chain (insufficient repay/output), while the `LendTx` funding the sender has already been mined as an independent transaction and its effect is not reversed.

### Impact Explanation
This differs from the Smilee case (pure DoS) in a materially worse way: because `LendTx` and `SwapTx` are two independent transactions rather than one atomic call, a reverted `SwapTx` does not undo the KAIA already lent to the sender in `LendTx`. The block proposer that funds `LendTxGenerator` loses the lent KAIA whenever the paired swap fails at execution time due to this price/quote staleness, which is a concrete unauthorized value transfer/fund-loss condition rather than a mere revert, satisfying the "concrete unauthorized value movement" bar in this analysis.

### Likelihood Explanation
Any unprivileged gasless user (or a third party trading against the same pool within the block) can move the AMM price for the whitelisted token between the tx-pool admission check and the actual block-execution of `swapForGas`, since `checkBalanceForSwap`'s price check is not re-verified at the moment of inclusion and there is no atomic linkage between `LendTx` and `SwapTx` beyond ordering. This is reachable purely through normal gasless-transaction submission plus routine AMM trading activity, requiring no privileged access.

### Recommendation
Re-validate the router's live exchange rate against `minAmountOut`/`amountRepay` immediately before block inclusion (not only at tx-pool admission), and/or make `LendTx` and `SwapTx` execution atomic (e.g., have `LendTx`'s effect conditioned on `SwapTx` succeeding, or fold both into a single transaction/precompile call) so a reverted swap cannot leave a lent amount stranded with the sender.

### Proof of Concept
1. Sender submits `ApproveTx` + `SwapTx(token, amountIn, minAmountOut, amountRepay, deadline)` where `amountRepay = repayAmount()` is computed from the current gas price, and `minAmountOut` is derived from `GetAmountIn` at current pool reserves, per `checkBalanceForSwap` [1](#0-0) .
2. `IsReady`/`isSwapTxReady` promote the bundle and `GetLendTxGenerator` produces a `LendTx` that unconditionally transfers `lendAmount` KAIA to the sender [8](#0-7) .
3. Before the block containing `[LendTx, ApproveTx, SwapTx]` is finalized, the price of `token` in the AMM pool moves (e.g., another trade in an earlier position of the same block) such that the actual swap output at execution is less than `minAmountOut`/`amountRepay`.
4. `LendTx` executes successfully, crediting the sender with KAIA.
5. `SwapTx` (`swapForGas`) reverts on-chain due to insufficient output/repay versus the now-stale quoted amounts, leaving the sender with the lent KAIA and no completed repayment — a fund loss for the fee-lending proposer.

Note: The Solidity source of `GaslessSwapRouter.sol` (only Go bindings such as `contracts/bindings/kip247/GaslessSwapRouter.go` were indexed) could not be located in the indexed codebase, so the exact internal repay/revert branching of `swapForGas` could not be directly confirmed; this analysis is based on the ABI signature and the Go-side admission/repay-amount logic. Confirming the exact contract-level repay check would require access to the full contract source, which may not be available due to index size limits — a Devin session with full repository access could verify this directly.

### Citations

**File:** kaiax/gasless/impl/tx_pool.go (L115-120)
```go
	// tx.minAmountOut >= tx.amountRepay
	minAmountOut := swapArgs.MinAmountOut
	amountRepay := swapArgs.AmountRepay
	if minAmountOut.Cmp(amountRepay) < 0 {
		return fmt.Errorf("insufficient minAmountOut: minAmountOut=%s, amountRepay=%s", minAmountOut.String(), amountRepay.String())
	}
```

**File:** kaiax/gasless/impl/tx_pool.go (L128-142)
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

**File:** kaiax/gasless/impl/getter.go (L346-359)
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
```

**File:** kaiax/gasless/impl/getter.go (L361-367)
```go
func repayAmount(approveTxOrNil, swapTx *types.Transaction) *big.Int {
	// R1 = LendTx.Fee() = SwapTx.GasPrice() * TxGas
	r1 := new(big.Int).Mul(swapTx.GasPrice(), new(big.Int).SetUint64(params.TxGas))

	// RepayAmount = R1 + R2 + R3
	return new(big.Int).Add(r1, lendAmount(approveTxOrNil, swapTx))
}
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
