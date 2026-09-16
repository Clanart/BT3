### Title
Gasless bundle LendTx unconditionally transfers KAIA before SwapTx repayment is guaranteed, allowing attacker to steal lent gas fees - ([File: kaiax/gasless/impl/getter.go])

### Summary
The `kaiax/gasless` module lets a block proposer "lend" KAIA gas fees to a gasless swap sender via a `LendTx`, expecting the sender's bundled `SwapTx` to repay the exact amount through a swap-router contract call. The repayment guarantee is enforced only by **static, pre-execution checks** (`VerifyExecutable`), not by re-validating on-chain token balance/allowance/price at the moment of block inclusion, and the three bundled transactions (`LendTx`, optional `ApproveTx`, `SwapTx`) are ordinary, independently-committed EVM transactions with no atomic all-or-nothing rollback. If the `SwapTx` reverts after the `LendTx` has already unconditionally transferred KAIA to the sender, the proposer's value transfer is not undone, and the attacker keeps the lent KAIA at the proposer's expense.

### Finding Description
The bundle is built as `[LendTxGenerator, ApproveTx(optional), SwapTx]`: [1](#0-0) 

`GetLendTxGenerator` creates a plain value-transfer transaction (`LendAmount`) from the proposer's `NodeKey` to the sender, unconditional on the swap succeeding: [2](#0-1) 

The only correctness guarantee before bundling is `VerifyExecutable`, which checks nonce ordering and that `AmountRepay` matches the *formula* `repayAmount()` — it never queries the token's live balance, allowance, or the router's live exchange rate: [3](#0-2) 

The actual on-chain balance/allowance/price checks live in a separate function, `checkBalanceForSwap`, which is not invoked as part of `VerifyExecutable`/`IsExecutable` (the function used by `ExtractTxBundles` at block-building time): [4](#0-3) 

Because `checkBalanceForSwap` is decoupled from bundle construction and executed at a different point in time (tx-pool admission/promotion), there is a time-of-check/time-of-use gap: token balance, allowance, or DEX price can change between the mempool check and the moment the bundle is actually included and executed in a block (e.g., the sender moves/spends the token in a preceding transaction, or price slippage pushes `amountIn`/`minAmountOut` out of range). Since `SwapTx` is a normal transaction, a revert inside the router contract only rolls back `SwapTx`'s own state changes — it does not revert the already-committed `LendTx` value transfer, because Kaia (like Ethereum) commits each transaction's execution result independently within a block.

### Impact Explanation
An attacker who is simply a gasless-swap sender (an unprivileged transaction submitter, exactly the type of actor this incident targets) can cause the SwapTx to fail after passing the softer/staler `IsExecutable`/`VerifyExecutable` checks (e.g., by front-running their own approval/balance with another transaction, or by choosing a token/pool where price moves unfavorably before inclusion). The proposer's `LendTx` will have already sent real KAIA (`lendAmount = ApproveTx.Fee() + SwapTx.Fee()`, i.e., the intrinsic gas cost the proposer pre-pays) to the attacker's address, and this value is not clawed back when the swap subsequently reverts. This is a direct, unauthorized transfer of value from the block proposer to an arbitrary sender — mirroring the ChainSwap root cause where "automatic" cross-chain quota (here: automatic KAIA lending) was granted based on an incompletely validated condition, resulting in value leaving the system to unauthorized/unintended recipients.

### Likelihood Explanation
Every gasless swap sender can attempt this by simply submitting borderline transactions (e.g., depleting the ERC-20 balance/allowance with a parallel transaction, or picking a token pool prone to slippage) shortly before the proposer includes the bundle. No special privilege, validator/consensus role, or cryptographic compromise is required — only ordinary transaction submission, which is squarely in the "single submitted transaction/bundle" threat model this analog scan targets.

### Recommendation
- Re-validate the exact on-chain conditions checked by `checkBalanceForSwap` (balance, allowance, live router exchange rate, deadline) as part of `VerifyExecutable`/`IsExecutable`, immediately before the bundle is included in a block (not only at mempool admission time).
- Ensure the bundle is executed atomically: if `SwapTx` reverts, the block builder should either drop the entire bundle (including the `LendTx`) from the block, or otherwise guarantee the LendTx amount is recovered, so the proposer can never lose KAIA to a swap that fails to repay.

### Proof of Concept
Conceptual sequence (cannot be fully executed without live node/mempool access):
1. Attacker submits `ApproveTx` (approving `SwapRouter` for token `T`) and `SwapTx` (swap `T` for KAIA repayment), satisfying `checkBalanceForSwap`/`checkBalanceForApprove` at mempool-admission time — passing balance and allowance checks.
2. Attacker then submits (or has already lined up) another transaction from the same account that transfers away or burns the approved token balance of `T` before the bundle is included in a block, or waits for DEX price movement that violates the `minAmountOut`/`amountRepay` relationship at execution time.
3. The proposer's bundling logic (`ExtractTxBundles`/`GetLendTxGenerator`) only re-checks the static formula via `VerifyExecutable` (no live balance/allowance/price check), so the bundle `[LendTx, ApproveTx, SwapTx]` is still built and included.
4. `LendTx` executes successfully, transferring `lendAmount` KAIA to the attacker.
5. `SwapTx` reverts inside the router contract (insufficient balance/allowance, or `GetAmountIn`/price no longer satisfying `minAmountOut`), but this revert does not roll back step 4.
6. Attacker keeps the KAIA from `LendTx` with no repayment ever reaching the proposer.

Note: I was not able to fully trace the block-assembly/commit path (`work/worker.go` `ApplyTransactions` and the underlying `blockchain/state_processor`) to confirm there is no separate bundle-level "all txs must succeed or none is included" safeguard; if such a safeguard exists elsewhere in block commit logic, it would mitigate this specific PoC. This should be verified in the actual node code before treating this as fully confirmed exploitable in production.

### Citations

**File:** kaiax/gasless/impl/builder.go (L38-51)
```go
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
