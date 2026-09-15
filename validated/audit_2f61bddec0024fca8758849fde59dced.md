### Title
Gasless bundle building (`VerifyExecutable`/`ExtractTxBundles`) omits sender-code (EOA) check enforced only at tx-pool admission - ([File: kaiax/gasless/impl/getter.go])

### Summary
The Kaia gasless module (KIP-247) enforces a "sender must be an EOA without code" check only in the transaction-pool admission path (`checkBalanceForApprove`/`checkBalanceForSwap`, gated by `ShouldCheckSenderCode()`), but the actual block-building/execution path that decides whether a gasless bundle (proposer-funded `LendTxGenerator` + `GaslessApproveTx`/`GaslessSwapTx`) is assembled and executed (`VerifyExecutable`/`IsExecutable`, used from `ExtractTxBundles`) never re-checks this condition. This mirrors the reported bug class: one code path (`depositERC20`) enforces the "must be EOA" guard while its sibling function that performs the same fund-moving action (`depositERC20To`) omits it.

### Finding Description
`GaslessModule.checkBalanceForApprove` and `checkBalanceForSwap` — invoked only through `GetCheckBalance()`, which is used to gate transaction-pool admission — reject a gasless tx if the sender currently has code, but only `if g.GaslessConfig.ShouldCheckSenderCode()` (default true, `BalanceCheckLevelAll`): [1](#0-0) [2](#0-1) 

This is documented as a security property in the module's own test suite: "reject swapTx originating from an EOA with code": [3](#0-2) 

However, the function that actually decides whether the gasless bundle is executable and gets assembled into a block — `VerifyExecutable` (wrapped by `IsExecutable`) — only checks router/token whitelisting, sender-match between approve/swap, allowance sufficiency, nonce sequencing, and repay-amount correctness. It never queries whether the sender account currently has code: [4](#0-3) 

`ExtractTxBundles`, the `kaiax.TxBundlingModule` hook invoked by the block-building `worker` to actually construct bundles (`[LendTxGenerator, GaslessApproveTx?, GaslessSwapTx]`), calls `IsExecutable` directly — not `GetCheckBalance()`: [5](#0-4) 

The `IsReady`/`isApproveTxReady`/`isSwapTxReady` functions used for tx-pool *promotion* (queue→pending) likewise call only `IsExecutable`, not `GetCheckBalance`: [6](#0-5) 

Because Kaia supports EIP-7702-style set-code authorization for EOAs (`validate7702`, `TxTypeAccountUpdate`/set-code paths), an address that was a plain EOA at the moment its `GaslessSwapTx` was admitted to the pool can acquire code afterward (e.g., a preceding transaction in the same block or a subsequently mined block installs code on that address) before the bundle is actually assembled/executed. Since bundling/execution relies solely on `VerifyExecutable`, which performs no code check, the bundle (including the proposer-funded `LendTxGenerator`) is still built and executed even though the "no code" invariant the module depends on no longer holds at execution time.

### Impact Explanation
The "sender has no code" check exists specifically to stop a smart-contract-controlled address from using the gasless-lending mechanism, presumably because a contract account can execute arbitrary logic in response to receiving the proposer-lent KAIA (e.g., in a fallback/receive path, or in a subsequent call it controls before repayment is settled), which the constant-sum accounting in `repayAmount`/`lendAmount` does not defend against. Because the guard is only enforced at the moment of admission and not re-validated at the moment the bundle is actually built and the proposer's `LendTx` is generated and executed, a contract that becomes code-bearing between admission and execution can still receive proposer-funded gas lending, undermining the fee-delegation trust model of the gasless bundle and enabling abuse of proposer-funded value (loss to the block proposer / gasless subsidy pool) that the "EOA-only" rule was designed to prevent. This falls under "fee delegation abuse" / "gasless settlement theft" categories.

### Likelihood Explanation
Exploitation requires only standard, permission-less capabilities available to any transaction sender: (1) submit a `GaslessSwapTx`/`GaslessApproveTx` from an address while it is still a plain EOA so it passes `GetCheckBalance`, and (2) install code on that same address (via a set-code/account-update transaction) before the block containing the gasless bundle is finalized. Both actions are ordinary, publicly reachable transactions; no validator/node compromise is needed. The check is silently bypassed by design (it exists in one code path but not the other), making this a deterministic logic gap rather than a probabilistic race.

### Recommendation
Move the sender-code ("EOA-only") check out of the tx-pool-only `checkBalanceForApprove`/`checkBalanceForSwap` and into `VerifyExecutable` (or call it from within `VerifyExecutable`) so that the same invariant is enforced immediately before a bundle is actually assembled/executed in `ExtractTxBundles`/`isApproveTxReady`/`isSwapTxReady`, not just at initial pool admission. This ensures the EOA guarantee holds at the moment the proposer's lend transaction is actually generated and funds are put at risk, closing the time-of-check/time-of-use gap.

### Proof of Concept
1. Attacker address `A` is a plain EOA. Attacker submits `GaslessApproveTx`/`GaslessSwapTx` from `A`; `GetCheckBalance` runs `checkBalanceForApprove`/`checkBalanceForSwap`, `getCurrentHasCode(A)` is false, checks pass, tx admitted to the pool.
2. Before the block proposer calls `ExtractTxBundles` to build the block containing `A`'s gasless tx (or in an earlier position within the same block), attacker submits/gets included a set-code/account-update transaction that installs contract code at `A`.
3. When the worker calls `ExtractTxBundles` → `IsExecutable`/`VerifyExecutable` for `A`'s pending gasless tx, no code-check is performed [4](#0-3) ; the function returns success purely based on router/token/allowance/nonce/repay checks.
4. `GetLendTxGenerator` still generates and signs a proposer-funded `LendTx` sending KAIA to `A`, and the bundle `[LendTx, ApproveTx?, SwapTx]` is included in the block [7](#0-6) , even though `A` is no longer a bare EOA — violating the invariant the module's own test (`tests/gasless_test.go:264-267`) asserts should be rejected.

### Citations

**File:** kaiax/gasless/impl/tx_pool.go (L74-82)
```go
func (g *GaslessModule) checkBalanceForApprove(approveArgs *ApproveArgs) error {
	token := approveArgs.Token
	bc := backends.NewBlockchainContractBackend(g.Chain, nil, nil)

	if g.GaslessConfig.ShouldCheckSenderCode() {
		if g.getCurrentHasCode(approveArgs.Sender) {
			return errors.New("sender with code is not allowed")
		}
	}
```

**File:** kaiax/gasless/impl/tx_pool.go (L107-126)
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

**File:** tests/gasless_test.go (L264-267)
```go
	// reject swapTx originating from an EOA with code
	sendSetCodeTx(t, chain, transactor, accounts[0])
	_, err = sendSwapTx(t, gsrContract, accounts[0], testTokenAddr, swapAmmount, minAmountOut, amountRepaySwap, deadline)
	assert.ErrorContains(t, err, "sender with code is not allowed")
```

**File:** kaiax/gasless/impl/getter.go (L203-266)
```go
func (g *GaslessModule) IsExecutable(approveTxOrNil, swapTx *types.Transaction) bool {
	err := g.VerifyExecutable(approveTxOrNil, swapTx)
	if err != nil {
		return false
	}
	return true
}

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

**File:** kaiax/gasless/impl/getter.go (L273-313)
```go
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
