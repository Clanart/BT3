Based on my research, the strongest analog to the Perennial "trigger a costly protocol action with a dust amount before its compensating fee is verified/collected" bug class in the Kaia codebase is in the **`kaiax/gasless`** module (KIP-247), which lets a block proposer unconditionally front KAIA gas fees to a user before the user's compensating swap has been confirmed to actually produce that value.

### Title
Proposer-funded `LendTx` is unconditionally included ahead of an uncollateralized `SwapTx` repayment in the gasless (KIP-247) bundle - (File: kaiax/gasless/impl/getter.go, kaiax/gasless/impl/builder.go)

### Summary
The gasless module builds a bundle `[LendTxGenerator, ApproveTx?, SwapTx]` for every transaction pair that satisfies only static, signature/nonce/amount-equality checks (`VerifyExecutable`), and the `LendTx` transfers real KAIA value from the block proposer to the user's account [1](#0-0) . The dynamic checks that actually verify the swap is economically sound — that `amountIn` is sufficient at the *current* AMM price and that the token/allowance/balance are real (`checkBalanceForSwap`) — are performed only as a separate, non-atomic tx-pool admission check [2](#0-1) , not re-verified at the moment `ExtractTxBundles` assembles the bundle for inclusion [3](#0-2) .

### Finding Description
`IsExecutable`/`VerifyExecutable`, which gates whether the proposer's `LendTxGenerator` is prepended to a bundle, only checks static equalities (sender match, token match, nonce sequencing, and that `AmountRepay == repayAmount(...)`, a value computed purely from the transactions' own gas fields) [4](#0-3) . It does **not** check that the declared `amountIn`/`minAmountOut` actually reflect the live AMM exchange rate, or that the sender holds the token/allowance — those checks live in `checkBalanceForSwap`, which calls the router's `GetAmountIn` against the *current* price and is only invoked during tx-pool promotion [2](#0-1) .

Because `LendAmount = ApproveTx.Fee() + SwapTx.Fee()` is paid to the user via a plain value-transfer `LendTx` signed by the node key before the `SwapTx` executes and (attempts to) repay it [5](#0-4) [6](#0-5) , any drift between the price checked at admission time and the price at block-execution time (e.g., from an intervening swap that shifts the AMM reserves, or simply re-submission across blocks while pending) is exactly analogous to the Perennial bug: a cheap, dust-sized action (a `swapForGas` call with minimal `amountIn`) unconditionally triggers an expensive, unilaterally-funded protocol action (the proposer's KAIA advance) whose compensating fee/repayment is only checked separately and not atomically re-verified against the state at the moment the value is actually paid out.

### Impact Explanation
If the `SwapTx` fails to fully repay the lent amount (because the AMM price moved between tx-pool admission and inclusion, or across repeated pending-then-rebroadcast attempts), the proposer's advanced KAIA is not recovered, mirroring the "theft of yield/protocol funds by abusing the keeper role" impact in the source report — here the proposer plays the role of the `keeper`. Repeating this at low cost (dust `amountIn`, minimal gas) across blocks could drain proposer funds reserved for gasless-lending, which could degrade or disable the gasless (KIP-247) feature (a DoS analogous to the oracle-fee-drain DoS in the original report).

### Likelihood Explanation
This requires an unprivileged transaction sender (or gasless-swap-transaction submitter) to be able to submit dust-value swap transactions repeatedly, and either wait for/ engineer AMM price movement between validation and inclusion, or exploit any gap between the tx-pool's `checkBalanceForSwap` check and the builder's `ExtractTxBundles`/execution. This is reachable purely from public transaction submission (no privileged role needed), matching the required threat model. However, I could not fully confirm from the indexed code whether: (1) the on-chain `GaslessSwapRouter.swapForGas` contract itself enforces `amountOut >= amountRepay` strictly on-chain (the Solidity source is not present in the index — only compiled bytecode/Go bindings were found), and (2) whether the block-builder's bundle execution is atomic (i.e., whether the whole bundle, including the already-executed `LendTx`, is rolled back if `SwapTx` reverts). Both of these directly determine whether the proposer's loss is actually realizable versus fully mitigated on-chain. This uncertainty could not be resolved with the available tools/index limits.

### Recommendation
Re-verify the dynamic, price-dependent conditions of `checkBalanceForSwap` (live AMM rate, balance, allowance) at the exact point the bundle is assembled/included in `ExtractTxBundles`/`IsExecutable`, not only at tx-pool admission time, and confirm/enforce that the bundle's execution is atomic so that `LendTx` cannot land in a block whose paired `SwapTx` fails to fully repay it.

### Proof of Concept
Not fully constructible from the indexed code alone: a working PoC would require the `GaslessSwapRouter.sol` source (not present in the index) to confirm whether `swapForGas` enforces `amountOut >= amountRepay` on-chain, and would require observing `work/builder.Bundle` execution semantics to confirm whether a reverting `SwapTx` rolls back the preceding `LendTx` in the same bundle. I recommend starting a Devin session with repository file access to pull `GaslessSwapRouter.sol` (if available outside the index) and the `work/builder` bundle-execution code to confirm bundle atomicity, then construct a timing PoC: (1) submit `ApproveTx`+`SwapTx` with dust `amountIn` sized to be barely sufficient at admission-time AMM price, (2) have a second account submit a swap in the same target block that shifts the AMM price against the pending bundle, (3) observe whether the `LendTx` lands while `SwapTx` reverts or underpays.

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

**File:** kaiax/gasless/impl/tx_pool.go (L102-141)
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
