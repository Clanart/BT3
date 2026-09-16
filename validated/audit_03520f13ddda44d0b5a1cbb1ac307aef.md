### Title
Gasless bundle settlement can lend real KAIA fee without re-verifying on-chain token balance/allowance, enabling gasless settlement theft - ([File: kaiax/gasless/impl/builder.go])

### Summary
The `kaiax/gasless` module accepts an `ApproveTx`+`SwapTx` (or standalone `SwapTx`) pair as a "gasless" transaction, and if judged *executable*, the block builder injects a synthetic `LendTx` that unconditionally transfers real KAIA (the pre-paid gas fee) to the sender before the `SwapTx` actually executes and proves the sender has the funds to repay. The "executable" judgement used at bundling time (`IsExecutable`/`VerifyExecutable`) only checks static transaction-field consistency (nonce sequencing, declared amounts, and an arithmetic repay formula) — it never re-checks the sender's actual on-chain ERC20 balance or router allowance, which is the analogue of "burning the required NFT1 Group token" in the CVE. That balance/allowance check exists only in a separate function, `checkBalanceForSwap`/`checkBalanceForApprove`, that is not called from the bundling path.

### Finding Description
`GaslessModule.ExtractTxBundles` (called during block assembly) builds the transaction bundle solely based on `IsExecutable`: [1](#0-0) 

`IsExecutable` delegates to `VerifyExecutable`, which validates: swap/approve tx shape, sender consistency, nonce sequencing, and that `SwapTx.amountRepay` matches `repayAmount()` — a value derived purely from gas price × gas limit of the transactions, not from any real token balance: [2](#0-1) [3](#0-2) 

The actual on-chain balance/allowance verification (`tx.token.balanceOf(sender) >= amountIn`, `tx.token.allowance(sender, router) >= amountIn`) is implemented in a *different* function, `checkBalanceForSwap`, gated behind `GaslessConfig.ShouldCheckToken()`: [4](#0-3) 

This function is only reachable through `GetCheckBalance()`, which is a separate hook used at transaction-pool admission time, not from `IsExecutable`/`ExtractTxBundles`/`isReady` (the code paths that actually decide whether to bundle a `LendTx` real-value transfer): [5](#0-4) [6](#0-5) 

`GetLendTxGenerator` unconditionally creates a signed value-transfer of `lendAmount()` KAIA to the sender, ordered *before* the `ApproveTx`/`SwapTx` in the bundle: [7](#0-6) 

Because the balance/allowance check is decoupled from the bundling/executability decision, there is a state-staleness window: a sender can pass the tx-pool's one-time `checkBalanceForSwap` when they still hold sufficient tokens/allowance, then transfer the tokens away or revoke approval before the block proposer actually assembles the bundle. `IsExecutable` at bundling time will still return `true` (it only checks tx-field arithmetic), so the proposer emits the `LendTx` that pays the sender real KAIA. The subsequent `SwapTx` then reverts inside the `GaslessSwapRouter` contract (insufficient balance/allowance at EVM execution time), but the `LendTx` value transfer to the sender is a separate, already-included transaction and is not automatically clawed back.

### Impact Explanation
An unprivileged gasless user can extract real KAIA (the lend/fee amount) from the fee-lending mechanism without ever completing (or being able to complete) the compensating token swap, because the bundling-time "executability" check never re-validates the actual balance/allowance backing the repayment — directly analogous to the CVE's false-positive validation of an NFT1 Child Genesis without verifying the required parent-token burn. This is unauthorized value movement / fee-delegation abuse reachable from a single submitted transaction pair by any gasless user.

### Likelihood Explanation
Reachable purely by submitting an `ApproveTx`(optional)+`SwapTx` pair as an ordinary unprivileged sender — no operator, validator, or node-privilege is required. The attacker only needs to time a token transfer/approval-revocation between initial tx-pool admission (where `checkBalanceForSwap` runs once) and the block proposer's bundle extraction (where it does not run again), which is a normal, attacker-controllable sequencing of transactions within the mempool/block-building window.

### Recommendation
Invoke `checkBalanceForSwap`/`checkBalanceForApprove` (or an equivalent up-to-date on-chain balance/allowance check against the state the block is being built on) inside `IsExecutable`/`VerifyExecutable`, or explicitly within `ExtractTxBundles`/`isSwapTxReady`/`isApproveTxReady`, immediately before generating and including the `LendTx`, so that the lend is only issued when the sender demonstrably holds sufficient token balance and allowance at the exact state used for bundling.

### Proof of Concept
1. Attacker acquires just enough allowed ERC20 token balance and approves the `SwapRouter` for `MaxUint256`.
2. Attacker submits `ApproveTx` (nonce N) then `SwapTx` (nonce N+1) with `amountRepay` correctly computed via `repayAmount()`; both pass tx-pool admission, including the one-time `checkBalanceForSwap`/`checkBalanceForApprove` checks.
3. Before the block containing this pair is built, attacker submits (or has pending) another transaction that transfers away the ERC20 balance or revokes the allowance to the router.
4. When the block proposer calls `ExtractTxBundles`, `IsExecutable`/`VerifyExecutable` still returns true (it never re-checks balance/allowance), so a `LendTx` transferring `lendAmount()` KAIA to the attacker is generated and included ahead of the `ApproveTx`/`SwapTx`.
5. The `SwapTx` reverts on-chain (insufficient token balance/allowance verified inside the router contract), but the attacker has already received the `LendTx` KAIA payout with no compensating swap value delivered.

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

**File:** kaiax/gasless/impl/tx_pool.go (L232-290)
```go
// Check promotion condition.
func (g *GaslessModule) isReady(txs map[uint64]*types.Transaction, i uint64, ready types.Transactions) bool {
	tx := txs[i]

	if g.IsApproveTx(tx) && i < uint64(math.MaxUint64) {
		return g.isApproveTxReady(tx, txs[i+1])
	}

	if g.IsSwapTx(tx) {
		var prevTx *types.Transaction
		if len(ready) > 0 {
			prevTx = ready[len(ready)-1]
		}
		return g.isSwapTxReady(tx, prevTx)
	}

	return false
}

// isApproveTxReady assumes that the caller checked `g.IsApproveTx(approveTx)`
func (g *GaslessModule) isApproveTxReady(approveTx, nextTx *types.Transaction) bool {
	addr, err := types.Sender(g.signer, approveTx)
	if err != nil {
		return false
	}
	nonce := g.getCurrentStateNonce(addr)

	if approveTx.Nonce() != nonce {
		return false
	}
	if nextTx == nil || !g.IsSwapTx(nextTx) {
		return false
	}

	return g.IsExecutable(approveTx, nextTx)
}

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
