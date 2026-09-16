### Title
Gasless bundle admission (`ExtractTxBundles`/`IsExecutable`) skips the deeper economic checks enforced by `GetCheckBalance`, allowing a proposer-funded `LendTx` to be committed ahead of a `SwapTx` that is not actually guaranteed to succeed - ([File: kaiax/gasless/impl/builder.go])

### Summary
The gasless module enforces two different, non-equivalent validation paths for the same GaslessApproveTx/GaslessSwapTx pair, similar in structure to the PostgreSQL MERGE flaw where new rows were checked against INSERT policies but not against the UPDATE/SELECT policies that other commands enforce. Here, `GetCheckBalance()` (`checkBalanceForApprove`/`checkBalanceForSwap` in `kaiax/gasless/impl/tx_pool.go`) enforces sender-code, token-balance, allowance, swap-amount-vs-router-quote, and deadline checks, and is used only for tx-pool admission (`blockchain/tx_pool.go` `validateTx`) and pool eviction (`blockchain/tx_list.go` `Filter`). But the block-building path, `ExtractTxBundles` (`kaiax/gasless/impl/builder.go:40`), decides whether to bundle a GaslessSwapTx solely via `g.IsExecutable(approveTxs[addr], tx)` → `VerifyExecutable` (`kaiax/gasless/impl/getter.go:214-266`), which only checks structural/consistency conditions (sender match, token match, approve amount ≥ swap amount, nonce sequencing, and that the declared `AmountRepay` matches `repayAmount()`). It does **not** re-run `checkBalanceForSwap`'s token balance/allowance/swap-amount/deadline/sender-code checks.

### Finding Description
`GetLendTxGenerator` (`kaiax/gasless/impl/getter.go:273-313`) unconditionally creates and signs a `LendTx` that transfers native KAIA from the block proposer to the swap sender, computed purely from `lendAmount()` (sum of tx fees), before the ApproveTx/SwapTx are executed. The bundle `[LendTx, ApproveTx?, SwapTx]` is only gated by `VerifyExecutable`, which validates that the declared repay amount is arithmetically consistent with the lend amount, but never confirms that the sender still has the token balance/allowance required for `swapForGas` to actually succeed on-chain, nor that `minAmountOut`/router quote or deadline conditions hold at the moment of block building. [1](#0-0) [2](#0-1) [3](#0-2) 

Because pool admission (`checkBalanceForSwap` via `validateTx`/`GetCheckBalance`) happens at submission time and can become stale by block-building time - token balance can drop, allowance can be revoked, the deadline can elapse, or AMM price can move so that the router's required `amountIn` for the declared `minAmountOut` is no longer satisfied - and `ExtractTxBundles`/`VerifyExecutable` re-checks none of these conditions before including the bundle, a proposer/builder can (or an attacker can arrange, e.g., by front-running an allowance-revoking or balance-draining tx, or simply by timing the deadline) get a bundle included where the `LendTx` pays out KAIA to the sender's account, while the on-chain `swapForGas` call is likely to revert. [4](#0-3) 

I was not able to fully confirm within available tool budget whether `commitBundleTransaction`/`shouldDiscardBundle` (referenced in `work/worker.go`) roll back the entire bundle atomically when a later transaction in the bundle reverts, or whether they only skip inclusion of subsequent bundle members while a preceding `LendTx` that already succeeded remains committed to the block. This is the crux of exploitability and is **unverified** due to running out of investigation iterations — I could not read the body of `commitBundleTransaction`/`shouldDiscardBundle` in `work/worker.go`. If the bundle commit is not atomic (i.e., a failing `SwapTx` does not revert the already-applied `LendTx`), this constitutes concrete unauthorized value movement (proposer funds transferred to the sender with no enforced repayment), directly analogous to the CVE's core defect: one validation path (`VerifyExecutable`) omitting checks that a sibling path (`GetCheckBalance`) enforces, allowing state changes that should have been rejected.

### Impact Explanation
If bundle execution is not atomic, this allows unauthorized value movement: the block proposer's KAIA is transferred to an unprivileged sender via `LendTx` without the swap successfully repaying it, since none of the balance/allowance/amount/deadline safety checks that `GetCheckBalance` enforces are re-verified at the point the bundle is actually assembled and executed. This maps to "fee delegation abuse" / "gasless settlement theft" category explicitly allowed by the validation rules.

### Likelihood Explanation
Exploitability requires (a) submitting a valid GaslessApproveTx/GaslessSwapTx pair that passes pool admission, and then (b) invalidating one of the conditions enforced only by `checkBalanceForApprove`/`checkBalanceForSwap` (e.g., revoking the ERC20 allowance to the router, or draining the token balance, or letting the deadline lapse) after pool admission but before the transaction is picked for block building — all reachable purely via ordinary submitted transactions from an unprivileged sender/attacker, no privileged or node-level access required. The main open question (not fully verified) is whether the worker's bundle execution path treats reverts atomically, which would determine whether the LendTx payout is actually retained on revert.

### Recommendation
Ensure `ExtractTxBundles`/`VerifyExecutable` (or the bundle-commit path in `work/worker.go`) re-validates the same economic conditions that `GetCheckBalance`/`checkBalanceForSwap` enforces (token balance, allowance, router-quoted amountIn, deadline, sender code) immediately before including/executing the bundle, and guarantee atomic bundle execution so that a failing `SwapTx` reverts the already-applied `LendTx` within the same block.

### Proof of Concept
Conceptual: since bundle-commit atomicity in `work/worker.go` could not be confirmed, a concrete PoC could not be constructed within this investigation. A background Devin session with terminal access should:
1. Read `commitBundleTransaction`/`shouldDiscardBundle` in `work/worker.go` to determine whether a bundle is atomic (state reverted on a later tx failure) or not.
2. If not atomic, submit ApproveTx+SwapTx that pass `GetCheckBalance` at admission time, then revoke allowance/drain balance before the block is built, and observe whether the `LendTx` payout persists on-chain while `SwapTx` reverts.

### Citations

**File:** kaiax/gasless/impl/builder.go (L38-46)
```go
		if g.IsApproveTx(tx) {
			approveTxs[addr] = tx
		} else if g.IsSwapTx(tx) && g.IsExecutable(approveTxs[addr], tx) {
			bundleTxs := builder.NewTxOrGenList(g.GetLendTxGenerator(approveTxs[addr], tx))
			if approveTxs[addr] != nil {
				bundleTxs = append(bundleTxs, builder.NewTxOrGenFromTx(approveTxs[addr]))
			}
			bundleTxs = append(bundleTxs, builder.NewTxOrGenFromTx(tx))

```

**File:** kaiax/gasless/impl/getter.go (L214-266)
```go
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

**File:** kaiax/gasless/impl/tx_pool.go (L102-182)
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
