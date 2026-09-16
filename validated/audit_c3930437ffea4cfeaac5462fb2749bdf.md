### Title
Gasless module's CN-lender solvency check runs only once at startup, allowing gasless swaps to be silently and repeatedly halted when the proposer's balance is later exhausted - ([File: kaiax/gasless/impl/init.go])

### Summary
The gasless module funds users' gas via a `LendTx` paid from the consensus node's (CN) own balance, and the CN is only checked for solvency once, at module `Init()`. There is no per-block or per-bundle re-check that the proposer can actually afford the lend amount before it commits to including a gasless bundle.

### Finding Description
`GaslessModule.Init` checks the CN's balance against a hard-coded minimum (`GaslessLenderMinBal = 1e18`) exactly once, and disables the whole module for that node if it fails: [1](#0-0) . This is the only place `GaslessLenderMinBal` (or any CN balance check) is referenced in the module [2](#0-1) .

Sender-side balance checks for gasless transactions are intentionally skipped in the tx pool (`GetCheckBalance()` only validates token balance/allowance/swap math, never the CN's balance) [3](#0-2) , relying entirely on the proposer to fund the `LendTx` at block-building time: [4](#0-3) . `ExtractTxBundles` builds `[LendTxGenerator, ApproveTx, SwapTx]` bundles for every ready gasless pair found in the pending pool without ever checking the current CN balance against the cumulative lend amount required across all bundles in the block [5](#0-4) .

Consequently, once `Init()` has passed (module enabled), the CN's balance can be depleted over time — e.g., by other CN outgoing payments, or simply by multiple simultaneous gasless bundles whose combined `LendAmount` exceeds the CN's spendable balance in a single block, since none of these amounts are aggregated/reserved against a live balance check. This is analogous to the reported `InsurancePool` bug class: a fund used to "front" a user obligation (there: overcollateralization reserve; here: proposer-funded gas lending) is validated for solvency only at a stale point in time, not against the obligation actually being incurred, leading to failures when the buffer is exhausted.

### Impact Explanation
When the proposer's KAIA balance is insufficient to cover the aggregated `LendTx` amounts for the gasless bundles it tries to include, the `LendTx` execution will fail the sender-balance check in state transition (`checkSenderBalance`/`buyGas`) [6](#0-5) , causing the enclosing bundle (and thus the user's `GaslessSwapTx`, which was allowed into the pool specifically because its own balance check was skipped) to be dropped from the block. Because there is no re-check mechanism after `Init()`, this failure is silent and repeats block after block until the CN's balance is manually replenished — the user's gasless transaction is effectively censored/stuck (analogous to "withdrawals halted temporarily" in the source report), even though the gasless swap itself is fully valid and would repay the lent amount.

### Likelihood Explanation
Reachable by any ordinary user submitting a standards-compliant `GaslessApproveTx`/`GaslessSwapTx` pair via public RPC — no privileged access is required. The likelihood of the CN's balance being exhausted depends on operational factors (how the CN's rewardbase/fee-payer address is managed, and load of concurrent gasless swaps within a block), but the code contains no safeguard (no periodic re-check, no per-block cap tied to actual available balance, no fallback re-disable), making this a systemic gap rather than a one-off edge case.

### Recommendation
Re-validate the CN lender balance immediately before finalizing each block/bundle set (not just once at module `Init`), and cap/reject gasless bundles whose aggregate `LendAmount` in a candidate block exceeds the proposer's currently spendable balance, dropping only the excess bundles rather than silently failing block assembly for all of them. Additionally, consider re-enabling/disabling the module dynamically based on live balance rather than a permanent one-time Init-time flag, and add continuous monitoring so a temporarily insufficient CN balance does not indefinitely censor gasless swap users.

### Proof of Concept
1. A CN node starts with balance ≥ `GaslessLenderMinBal` (1 KAIA), so `Init()` leaves the gasless module enabled: [1](#0-0) .
2. Over time, or within a single block, several unrelated users submit valid `GaslessApproveTx`/`GaslessSwapTx` pairs; each pair independently passes `GetCheckBalance()` (token balance/allowance checks only) and is admitted to the pool: [7](#0-6) .
3. During block building, `ExtractTxBundles` prepends a fresh `LendTxGenerator` per bundle without checking the CN's current, already-decremented balance against the sum of all lend amounts being generated for the block: [5](#0-4) .
4. If the sum of `LendAmount`s exceeds the CN's actual spendable balance (e.g., due to concurrent bundles or the CN's balance having dropped since `Init()`), the later `LendTx`(s) fail `checkSenderBalance` during state transition, causing those bundles — and the wrapped legitimate `GaslessSwapTx`s — to be excluded from the block, repeating every subsequent block until the CN's balance is manually topped up, with no code path automatically detecting or resolving the condition.

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

**File:** kaiax/gasless/impl/constant.go (L21-24)
```go
var (
	GaslessSwapRouterName = "GaslessSwapRouter"
	GaslessLenderMinBal   = big.NewInt(1e18)
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

**File:** blockchain/state_transition.go (L270-292)
```go
func (st *StateTransition) checkSenderBalance(balanceCheck *big.Int, checkOverflow, checkWithValue bool) error {
	senderAddress := st.msg.ValidatedSender()
	actualBalance := st.state.GetBalance(senderAddress)
	balanceCheckWithValue := new(big.Int).Add(balanceCheck, st.msg.Value())
	if checkOverflow {
		balance := balanceCheck
		if checkWithValue {
			balance = balanceCheckWithValue
		}
		if err := st.checkBalanceOverflow(senderAddress, balance); err != nil {
			return err
		}
	}
	if actualBalance.Cmp(balanceCheck) < 0 {
		logger.Debug(errInsufficientBalanceForGas.Error(), "sender", senderAddress.String(),
			"senderBalance", actualBalance.Uint64(), "senderFee", balanceCheck.Uint64(),
			"txHash", st.msg.Hash().String())
		return errInsufficientBalanceForGas
	} else if checkWithValue && actualBalance.Cmp(balanceCheckWithValue) < 0 { // Error due to insufficient Value must be vm.ErrInsufficientBalance.
		return vm.ErrInsufficientBalance
	}
	return nil
}
```
