## Analysis Result [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) 

### Title
Unconditional gas-lending before verified swap repayment in Gasless module - (File: kaiax/gasless/impl/getter.go)

### Summary
The `kaiax/gasless` module implements KIP-247 gasless transactions by having the proposer "lend" KAIA to a user via `GetLendTxGenerator` before the user's `GaslessApproveTx`/`GaslessSwapTx` executes and repays that loan through an on-chain token swap. The repayment correctness depends entirely on the swap succeeding at actual block-execution time, but the admission-time checks (`checkBalanceForSwap`, `VerifyExecutable`) only validate static state (balances, allowances, deadlines, quoted `GetAmountIn`) rather than guaranteeing the swap will not revert when it is actually executed in the block being built.

### Finding Description
`GetLendTxGenerator` unconditionally constructs and signs a `TxTypeEthereumDynamicFee` transaction that transfers real KAIA value (`lendAmount(approveTxOrNil, swapTx)`, computed purely from `Fee()` of the approve/swap transactions) from the proposer's node key to the swap sender: [5](#0-4) 

`lendAmount`/`repayAmount` are pure arithmetic over transaction fee fields; they do not depend on whether the subsequent `GaslessSwapTx` actually completes successfully on-chain: [2](#0-1) 

`ExtractTxBundles` builds the block-inclusion bundle as `[LendTxGenerator, ApproveTx?, SwapTx]` with `TargetRequired=false`, meaning this bundle is not conditioned on the success of any preceding target transaction: [6](#0-5) 

The only pre-inclusion validation of the swap's viability, `checkBalanceForSwap`, reads current state (allowance, balance, router's `GetAmountIn` quote, deadline) at admission time via `sc_erc20.NewERC20`/`kip247.NewGaslessSwapRouterCaller` calls against the router and token contracts: [7](#0-6) 

Because the router's exchange rate (`GetAmountIn`) can move between the moment the tx is validated for pool admission and the moment the bundle is actually assembled/executed in a block (e.g., due to other trades hitting the AMM pool, or the router itself being any pool-based DEX contract reachable by the whitelisted token), `swapForGas` can revert on execution even though `VerifyExecutable`/`checkBalanceForSwap` passed. `VerifyExecutable` performs only static arithmetic checks (nonce sequencing, repay-amount equality) and never simulates the swap outcome: [8](#0-7) 

This mirrors the reported bug class: a payment-relevant on-chain interaction (here, `swapForGas` against a token/AMM contract) whose success is trusted at validation time but can fail at settlement time, while a real value transfer (the lend) has already been irrevocably committed based on that trust.

### Impact Explanation
If the `GaslessSwapTx` reverts during actual block execution after the `LendTx` has already unconditionally transferred KAIA to the sender, the proposer's lent funds are not repaid by the failed swap. This is a genuine value-movement risk: KAIA leaves the proposer's node-key balance via a signed, included transaction, while the compensating repayment transaction — which is only checked for static validity, not simulated for execution success — can fail. This directly affects the reachable "gasless settlement theft" bug class named in scope.

### Likelihood Explanation
Reachability requires only a normal unprivileged user submitting a `GaslessApproveTx`/`GaslessSwapTx` pair that passes the static `checkBalanceForSwap`/`VerifyExecutable` checks, and market/pool conditions (which any public participant can influence by trading against the same AMM pool, since `GaslessSwapRouter`/token contracts are ordinary on-chain contracts) shifting between admission and block assembly so that `GetAmountIn`/slippage bounds are violated at execution time. No validator, peer, or node compromise is needed — this is reachable purely through public transaction submission and public trading against the router's liquidity pool.

### Recommendation
Re-verify swap executability (e.g., via a state-simulating call to the router's expected output/slippage bounds) immediately before finalizing the bundle inclusion in the block, not only at initial tx-pool admission. Alternatively, make the bundle atomic/target-required with respect to swap success so the `LendTx` is only included if the `SwapTx` is confirmed (via simulation) to succeed with the current state, preventing lent KAIA from being sent when repayment cannot be guaranteed.

### Proof of Concept
1. Attacker (or any actor) submits an approve/swap gasless pair that satisfies `VerifyExecutable`/`checkBalanceForSwap` at admission time (correct nonce, sufficient balance/allowance, valid deadline, `minAmountOut` computed against the router's quoted `GetAmountIn` at that moment).
2. Before the block containing this bundle is assembled, market activity (or the attacker's own additional trades against the same pool) shifts the router's exchange rate so that the swap's actual on-chain execution violates `minAmountOut`/slippage and reverts inside `swapForGas`.
3. The bundle `[LendTxGenerator, ApproveTx, SwapTx]` is still built via `ExtractTxBundles`; the `LendTx` unconditionally transfers `lendAmount` KAIA from the proposer's node key to the sender.
4. The `SwapTx` reverts on execution, so the KAIA that was supposed to repay the lend (via successful `swapForGas`) is never returned to the proposer, resulting in an uncompensated KAIA outflow from the proposer's account.

**Note on verification limits:** I was unable to fully confirm within the available search budget whether the block-building/worker logic that applies `Bundle.BundleTxs` treats the bundle as atomic (i.e., would discard the `LendTx` if a later tx in the same bundle reverts) — this would materially affect whether the described loss is actually realized on-chain or is instead prevented by an atomicity guarantee elsewhere in the miner/worker pipeline (outside `kaiax/gasless` and `work/builder/bundle.go`, which I was able to inspect). This should be verified against the block-assembly/worker code (e.g., `work/builder` execution path or `miner`/`worker` packages) before treating this as conclusively exploitable.

### Citations

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
