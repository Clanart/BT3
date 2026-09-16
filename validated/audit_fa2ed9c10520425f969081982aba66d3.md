### Title
Gasless module's "sender must be EOA" anti-abuse restriction is enforced only at tx-pool admission time and can be bypassed by an intra-block code-deployment ordering, letting a contract drain proposer-funded gas lending - ([File: kaiax/gasless/impl/tx_pool.go])

### Summary
The `kaiax/gasless` module implements KIP-247 gasless transactions: the proposer lends the sender gas fee via a proposer-generated `LendTxGenerator` transaction, and the sender is expected to repay it during the bundled `GaslessSwapTx`. A configurable restriction, `ShouldCheckSenderCode()`, is meant to ensure this proposer-funded lending is only extended to externally-owned accounts (EOA), not to contracts [1](#0-0) . This "allow only EOA senders" restriction is exactly the kind of admission-time policy check that the external report's bug class targets: a restriction attached to a privileged/asset-moving flow that is enforced on one code path but not re-validated at the point where the actual value transfer occurs.

### Finding Description
`GetCheckBalance()` is the only entry point that runs `checkBalanceForApprove`/`checkBalanceForSwap`, which in turn call `g.getCurrentHasCode(sender)` to reject contract-controlled senders when `ShouldCheckSenderCode()` is enabled [2](#0-1) [3](#0-2) [4](#0-3) . This check is invoked from the tx pool's `validateTx`/`add` path (module admission) [5](#0-4)  and again during `txList.Filter` when the pool is reset to a new head [6](#0-5) .

However, the actual value-moving step — the proposer prepending a `LendTxGenerator` transaction ahead of the approve/swap bundle — happens later, during block building in `ExtractTxBundles`, which reads the already-admitted pending pool contents without re-running `GetCheckBalance`/`ShouldCheckSenderCode` against the state as it will exist at the point of execution within the same block [7](#0-6) . Because Kaia block building can include multiple transactions from the same sender or interleaved transactions that change the sender's code (e.g., an EIP-7702 `SetCode` authorization transaction, which this codebase explicitly supports via `tx.SetCodeAuthorities()`/`AuthList()` in the tx pool [8](#0-7) ), a sender that was validated as "has no code" at pool-admission/Filter time can have code attached to its address by an earlier transaction in the very same block before the gasless bundle executes. The restriction check therefore only reflects the state at the last pool reset, not the state at actual execution time, so the EOA-only restriction on proposer-funded lending can be circumvented.

### Impact Explanation
If a contract-controlled address can obtain proposer-funded gas lending that was policy-restricted to EOAs, it can leverage arbitrary contract logic during `GaslessSwapTx`/`GaslessApproveTx` execution (e.g., reentrancy, callback manipulation, or partial/non-repayment logic) to avoid fully repaying the lent amount, resulting in fee-delegation/gasless settlement theft against the block proposer — a concrete unauthorized value movement consistent with the "gasless...settlement theft" impact category.

### Likelihood Explanation
Exploitability depends on: (1) the node operator enabling `ShouldCheckSenderCode()` as its anti-abuse control, and (2) an attacker's ability to have a code-deploying transaction (e.g., SetCode authorization or CREATE2 self-destruct-then-redeploy pattern) land in the same block, before the gasless bundle, without triggering another `Filter`/admission re-check in between. This requires some block-building timing control but is achievable by a single unprivileged submitter crafting sequenced transactions/bundles targeting their own account, so likelihood is plausible for a determined attacker, though it is gated by module configuration and the exact intra-block re-validation behavior of `ExtractTxBundles`/`LendTxGenerator`, which I could not fully inspect due to index coverage limits (`GetLendTxGenerator`, `IsExecutable`, `PreRunTx`/`PostRunTx` implementations were not retrievable in full).

### Recommendation
Re-run `GetCheckBalance()` (including `ShouldCheckSenderCode`) against the exact state snapshot that will precede execution of the bundle at block-building time in `ExtractTxBundles`/`GetLendTxGenerator`, not just at the last tx-pool head reset, so that intra-block state changes (including EIP-7702 code delegation) cannot invalidate the restriction between admission and execution.

### Proof of Concept
Conceptual sequence (not fully verified against `GetLendTxGenerator`/bundle execution internals due to index limits):
1. Attacker submits a `GaslessApproveTx`/`GaslessSwapTx` pair from address `A` while `A` has no code; it passes `checkBalanceForApprove`/`checkBalanceForSwap` (`ShouldCheckSenderCode` passes) and is admitted to the pool.
2. Attacker also submits (or has pending) a transaction that attaches code to `A` (e.g., an EIP-7702 SetCode-authorized transaction) with a lower nonce/earlier ordering, without triggering a `txList.Filter` re-check between admission and block assembly.
3. During block building, `ExtractTxBundles` builds `[LendTxGenerator, GaslessApproveTx, GaslessSwapTx]` for `A` based on the earlier-validated pool state [9](#0-8) .
4. The block executes the code-attaching tx first, then the lend/approve/swap bundle — now `A` has code, but the "no code" restriction was never re-verified against this execution-time state, so proposer-funded lending is extended to a contract-controlled account.

### Citations

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

**File:** kaiax/gasless/impl/tx_pool.go (L74-100)
```go
func (g *GaslessModule) checkBalanceForApprove(approveArgs *ApproveArgs) error {
	token := approveArgs.Token
	bc := backends.NewBlockchainContractBackend(g.Chain, nil, nil)

	if g.GaslessConfig.ShouldCheckSenderCode() {
		if g.getCurrentHasCode(approveArgs.Sender) {
			return errors.New("sender with code is not allowed")
		}
	}

	if g.GaslessConfig.ShouldCheckToken() {
		tokenContract, err := sc_erc20.NewERC20(token, bc)
		if err != nil {
			return err
		}

		// tx.token.balanceOf(sender) > 0
		tokenBalance, err := tokenContract.BalanceOf(nil, approveArgs.Sender)
		if err != nil {
			return err
		}
		if tokenBalance.Sign() <= 0 {
			return fmt.Errorf("insufficient sender token balance: token=%s, have=%s, want=nonzero", token.Hex(), tokenBalance.String())
		}
	}
	return nil
}
```

**File:** kaiax/gasless/impl/tx_pool.go (L102-126)
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
```

**File:** blockchain/tx_pool.go (L918-932)
```go
	// If module recognizes the tx, run an alternative balance check and then skip the default balance check later.
	shouldSkipBalanceCheck := false
	for _, module := range pool.modules {
		if module.IsModuleTx(tx) {
			if checkBalance := module.GetCheckBalance(); checkBalance != nil {
				shouldSkipBalanceCheck = true
				err := checkBalance(tx)
				if err != nil {
					logger.Trace("[tx_pool] invalid funds of module transaction sender", "from", from, "txhash", tx.Hash().Hex())
					return err
				}
			}
			break
		}
	}
```

**File:** blockchain/tx_pool.go (L1016-1024)
```go
	if tx.Type() == types.TxTypeEthereumSetCode {
		if len(tx.AuthList()) == 0 {
			return errors.New("set code tx must have at least one authorization tuple")
		}
	}

	if err := pool.validateAuth(tx); err != nil {
		return err
	}
```

**File:** blockchain/tx_list.go (L406-414)
```go
		// balance check for module transaction
		for _, module := range pool.modules {
			if module.IsModuleTx(tx) {
				if checkBalance := module.GetCheckBalance(); checkBalance != nil {
					return checkBalance(tx) != nil
				}
				break
			}
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
