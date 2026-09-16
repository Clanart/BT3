## Analysis

I could not find evidence that bundle transactions in this codebase are executed with atomic revert-together semantics at the EVM/block-building layer — bundling here (`work/builder`) only controls **ordering/placement** of transactions (`incorporate`, `IncorporateBundleTx`), not shared success/failure. Each transaction (`LendTx`, `ApproveTx`, `SwapTx`) is still a separate, independently-included transaction on-chain [1](#0-0) . This matters because the `LendTx` unconditionally transfers KAIA value from the proposer to the user before the `SwapTx` executes and repays it [2](#0-1) , while `repayAmount`/`lendAmount` are fixed, price-independent values derived only from gas fees [3](#0-2) .

### Title
Gasless swap admission check uses stale AMM price, allowing lender value loss when `getAmountIn`/price decreases before execution - ([File: kaiax/gasless/impl/tx_pool.go])

### Summary
`checkBalanceForSwap` validates `SwapArgs.AmountIn` against the router's `GetAmountIn(token, minAmountOut)` computed from the **current state at admission/promotion time** [4](#0-3) . The proposer's `LendTx` value, however, is fixed and independent of the swap's actual on-chain output — it is derived purely from gas fee arithmetic (`lendAmount`) and executes as its own, separately-included transaction [5](#0-4) .

### Finding Description
This mirrors the reported bug class: a stale/decreased price check is not re-validated at execution time, and the "honest counterparty" (the AMM staker in the original report; here, the block proposer funding the gasless swap) absorbs the loss when price moves adversely between check-time and execution-time.

In this codebase:
1. `checkBalanceForSwap` is invoked at tx-pool admission/promotion using a live contract call to the `GaslessSwapRouter`, computing `requiredAmountIn` for the declared `minAmountOut` [6](#0-5) .
2. The block-building bundling logic (`ExtractTxBundles`) places `[LendTxGenerator, ApproveTx(optional), SwapTx]` together for the *same* block, but this is achieved by ordering constraints in `work/builder`, not by an EVM-atomic guarantee that a failing `SwapTx` reverts the preceding `LendTx` [7](#0-6) , [1](#0-0) .
3. `swapForGas`'s on-chain slippage protection (`minAmountOut`) is enforced only inside the `SwapTx` itself; the test suite confirms the router reverts when computed output falls short (`"reject swapTx when amountIn < router.GetAmountIn(minAmountOut)"`) [8](#0-7) . If the pool price shifts unfavorably (AMM reserves change from other trades executed between the pool-admission check and actual block inclusion — e.g. other pending swaps on the same token, or MEV/back-running by the sender themselves), the on-chain `SwapTx` can revert due to insufficient output, while the `LendTx` KAIA transfer to the sender has already unconditionally succeeded as its own transaction.
4. Because `LendTx` and `SwapTx` are independent transactions and only the `SwapTx` enforces the price-derived guarantee, a decreased effective price at execution time is "not catered for" with respect to the value already advanced by the proposer.

### Impact Explanation
If exploitable, this allows an unprivileged gasless-swap sender to receive lent KAIA (gas funding from the block proposer/relayer) without ever repaying it, because the repayment-enforcing `SwapTx` reverts due to stale-price validation, while the `LendTx` value transfer is not contingent on the `SwapTx`'s success. This is a direct, fee-delegation/gasless value-movement loss to the proposer, matching the required impact class (fee-delegation/gasless settlement theft).

### Likelihood Explanation
Requires: (a) the attacker controls the token/AMM pool used for the swap (or can influence its price between pool-check time and inclusion, e.g. via their own or a colluding party's prior trade in the same block/mempool window), and (b) their `SwapTx` is admitted based on a `checkBalanceForSwap` snapshot that becomes stale by execution. Given `GaslessConfig.ShouldCheckSwapAmount()` is a re-checked, single point-in-time gate and no re-validation occurs immediately before inclusion in the block, likelihood is plausible but requires precise timing/state manipulation of the pool reserves, which I was **not able to fully verify** — I could not confirm from available code whether the check is re-run immediately before block assembly (which would close this window) or only once at pool promotion.

### Recommendation
Re-validate `checkBalanceForSwap`'s `GetAmountIn`/price condition against the state immediately preceding block inclusion (not just at pool admission), or make `LendTx` value transfer conditional/refundable on `SwapTx` success (e.g., via a single atomic multicall/bundle-revert semantics, or a claw-back mechanism), so a decreased AMM price at execution time cannot let the sender walk away with lent KAIA without repayment.

### Proof of Concept
Not independently confirmed against a running node/testnet; based on static code analysis of `checkBalanceForSwap` ( [9](#0-8) ), `GetLendTxGenerator`/`lendAmount` ( [10](#0-9) ), and bundle ordering ( [7](#0-6) ). I was unable to confirm whether the framework enforces true atomic bundle revert-together semantics elsewhere (e.g., in the miner/worker code that executes bundles), which would be decisive in determining whether this analog is truly exploitable or is already mitigated. A Devin session with full repository/test-execution access would be needed to build a concrete PoC exercising bundle execution semantics under adversarial price movement.

### Citations

**File:** work/builder/builder.go (L69-116)
```go
// IncorporateBundleTx incorporates bundle transactions into the transaction list.
// Caller must ensure that there is no conflict between bundles.
func IncorporateBundleTx(txs []*types.Transaction, bundles []*Bundle) ([]*TxOrGen, error) {
	ret := make([]*TxOrGen, len(txs))
	for i, tx := range txs {
		ret[i] = NewTxOrGenFromTx(tx)
	}

	for _, bundle := range bundles {
		var err error
		ret, err = incorporate(ret, bundle)
		if err != nil {
			return nil, err
		}
	}
	return ret, nil
}

// incorporate assumes that `txs` does not contain any bundle transactions.
func incorporate(txs []*TxOrGen, bundle *Bundle) ([]*TxOrGen, error) {
	ret := make([]*TxOrGen, 0, len(txs)+len(bundle.BundleTxs))
	targetFound := false

	// 1. place bundle at the beginning
	if bundle.TargetTxHash == (common.Hash{}) {
		ret = append(ret, bundle.BundleTxs...)
		targetFound = true
	}

	// 2. place bundle after TargetTxHash
	for _, txOrGen := range txs {
		// if tx-in-bundle, the tx will be appended when target is found.
		if bundle.Has(txOrGen) {
			continue
		}
		ret = append(ret, txOrGen)
		if txOrGen.Id == bundle.TargetTxHash {
			targetFound = true
			ret = append(ret, bundle.BundleTxs...)
		}
	}

	if !targetFound {
		return nil, ErrFailedToIncorporateBundle
	}

	return ret, nil
}
```

**File:** kaiax/gasless/impl/getter.go (L268-367)
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

func (g *GaslessModule) updateAddresses(header *types.Header) error {
	g.gaslessInfoMu.Lock()
	defer g.gaslessInfoMu.Unlock()

	swapRouter, tokens, err := getGaslessInfo(g.Chain, header)
	// proceed even if there is something wrong with multicall contract
	if err != nil {
		g.swapRouter = common.Address{}
		g.allowedTokens = map[common.Address]bool{}
		logger.Warn("there is something wrong with multicall contract", "err", err.Error())
		return nil
	}

	g.swapRouter = swapRouter

	g.allowedTokens = map[common.Address]bool{}
	for _, addr := range tokens {
		// all tokens are allowed if nil
		if g.GaslessConfig.AllowedTokens == nil {
			g.allowedTokens[addr] = true
		}
		for _, allowed := range g.GaslessConfig.AllowedTokens {
			if addr == allowed {
				g.allowedTokens[addr] = true
			}
		}
	}

	return nil
}

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

**File:** tests/gasless_test.go (L251-253)
```go
	// reject swapTx when amountIn < router.GetAmountIn(minAmountOut)
	_, err = sendSwapTx(t, gsrContract, accounts[0], testTokenAddr, common.Big0, minAmountOut, amountRepaySwap, deadline)
	assert.ErrorContains(t, err, "insufficient amountIn")
```
