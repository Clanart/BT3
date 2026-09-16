### Title
Gasless swap bundle can revert after lending KAIA, allowing block proposer fund loss via price manipulation between mempool admission and block inclusion - (File: kaiax/gasless/impl/builder.go, kaiax/gasless/impl/getter.go)

### Summary
The PSM3 report describes a class of bug where an attacker exploits the gap between a balance/price check and the actual execution of a value-transfer operation (TOCTOU), depleting or moving the checked asset in between to break assumptions the depositor/withdrawer relied on. The `kaiax/gasless` module has an analogous check/execute gap: the `swapForGas` amount-out requirement (`minAmountOut`/`amountRepay`) is validated against the underlying DEX pool price only once, at tx-pool admission time (`checkBalanceForSwap`), while the actual bundle (which unconditionally sends real KAIA to the user via `LendTxGenerator` before the swap executes) is assembled and included later during block building.

### Finding Description
`GaslessModule.checkBalanceForSwap` in [1](#0-0)  verifies `swapArgs.AmountIn >= gsr.getAmountIn(minAmountOut)` and other balance/allowance conditions at the moment the transaction is promoted in the pool. However, `VerifyExecutable`, which is the function actually used at block-building/bundle-extraction time (`ExtractTxBundles` -> `IsExecutable`), does **not** re-check the current DEX pool price or `GetAmountIn` against `AmountIn`/`MinAmountOut` — see [2](#0-1) . It only checks static relationships between the approve/swap tx fields (nonces, amounts, `AmountRepay` formula), not the live AMM price.

`ExtractTxBundles` in [3](#0-2)  builds a bundle of `[LendTxGenerator, ApproveTx?, SwapTx]` where the `LendTxGenerator`-produced transaction unconditionally transfers real KAIA from the block proposer to the swap sender (see `GetLendTxGenerator`, [4](#0-3) ) before the `SwapTx` (which is supposed to repay via `swapForGas`) is executed. The bundle is created with `builder.NewBundle(bundleTxs, targetTxHash, false)` [5](#0-4) .

Between the time the gasless swap tx was admitted to the pool (and its `minAmountOut`/`amountRepay` were validated against the router's price at that moment) and the time the block proposer actually assembles and executes the bundle, an unprivileged actor can submit ordinary swap transactions against the same underlying Uniswap-V2-style pool that the `GaslessSwapRouter` relies on (`SwapForGas`, [6](#0-5) ) to move the pool price so that the previously-valid `minAmountOut`/`amountRepay` becomes unreachable. This causes `swapForGas` to revert inside the bundle at execution time, exactly analogous to PSM3's sandwich-and-deplete pattern, except here the impacted party is the block proposer who already lent real KAIA via the `LendTx` before the compensating swap fails.

### Impact Explanation
If the `LendTx` executes successfully but the paired `SwapTx` reverts (due to the price having moved), the proposer's lent KAIA (`lendAmount`, covering both the approve and swap tx fees, [7](#0-6) ) is not repaid, resulting in a direct, unrecoverable value loss for the block proposer for every such bundle. Since gas price/lend amounts scale with tx gas limits, a bot repeatedly triggering this by front-running gasless swap price checks could systematically drain proposer funds — a Medium/High severity fee-delegation/gasless-settlement abuse, unlike the "low risk" PSM3 finding, because here concrete value (KAIA balances belonging to the block proposer) is unconditionally transferred out with no atomic guarantee of repayment.

### Likelihood Explanation
Likelihood is realistic for an unprivileged actor: the attacker only needs to submit ordinary DEX swap transactions on the pool used by the `GaslessSwapRouter` at a suitable point relative to a pending gasless swap tx's inclusion — no special permissions, node access, or validator role required, consistent with allowed threat classes (unprivileged transaction sender / public RPC caller). The precise reliability of this attack (i.e., whether the bundle building guarantees atomicity of `[LendTx, ApproveTx, SwapTx]`, and what the `false` flag on `NewBundle` truly controls — atomicity/revertibility of the whole bundle) could not be fully confirmed from the available index; `work/builder/bundle.go`'s exact semantics were not retrievable in this session, so whether the "bundle" mechanism reverts the `LendTx` when `SwapTx` fails is uncertain and should be verified directly against `work/builder/bundle.go` and `work/worker.go`.

### Recommendation
Re-validate the current DEX/router price (`GetAmountIn`) against `AmountIn`/`MinAmountOut` at bundle-extraction/block-building time in `VerifyExecutable`, not only at mempool admission, and/or ensure the assembled `[LendTx, ApproveTx, SwapTx]` bundle is strictly atomic (i.e., a revert in `SwapTx` also reverts/excludes the `LendTx`) so that KAIA can never be lent without a successful, price-consistent repayment.

### Proof of Concept
1. An unprivileged user submits a valid `GaslessApproveTx` + `GaslessSwapTx` pair to a public Kaia RPC node targeting a token/WKAIA pair on the DEX pool backing `GaslessSwapRouter`, with `minAmountOut` computed against the current pool price (passes `checkBalanceForSwap` in [1](#0-0) , so it is promoted to pending).
2. Before the block proposer includes this bundle, the attacker (any public RPC caller) submits a large swap against the same underlying pool to shift its price so that `swapForGas`'s `minAmountOut` requirement can no longer be satisfied.
3. The proposer's `ExtractTxBundles` ( [3](#0-2) ) still forms the bundle because `VerifyExecutable` does not re-check price at this stage ( [2](#0-1) ), and generates/includes the `LendTx` that unconditionally sends KAIA to the sender.
4. `SwapTx`'s call to `swapForGas` reverts due to the now-insufficient `minAmountOut`, leaving the proposer's lent KAIA unrepaid — the exact "depleting/moving the asset between check and settlement" pattern described in the PSM3 report, translated to the gasless lending/repay flow.

### Citations

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

**File:** contracts/bindings/kip247/GaslessSwapRouter.go (L554-559)
```go
// SwapForGas is a paid mutator transaction binding the contract method 0x80426901.
//
// Solidity: function swapForGas(address token, uint256 amountIn, uint256 minAmountOut, uint256 amountRepay, uint256 deadline) returns()
func (_GaslessSwapRouter *GaslessSwapRouterTransactor) SwapForGas(opts *bind.TransactOpts, token common.Address, amountIn *big.Int, minAmountOut *big.Int, amountRepay *big.Int, deadline *big.Int) (*types.Transaction, error) {
	return _GaslessSwapRouter.contract.Transact(opts, "swapForGas", token, amountIn, minAmountOut, amountRepay, deadline)
}
```
