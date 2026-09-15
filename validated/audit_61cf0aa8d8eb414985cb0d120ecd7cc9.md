### Title
Gasless-swap sender-code/balance restrictions are enforced only at tx-pool admission and are never re-checked at bundle/block-assembly time - ([File: kaiax/gasless/impl/builder.go])

### Summary
The Gasless module's "reporter-cannot-do-X" style restriction (only EOAs, sufficiently funded/approved accounts may consume gasless-swap lending) is implemented in `checkBalanceForApprove`/`checkBalanceForSwap` and only invoked through `GetCheckBalance()`, which is wired into the tx-pool add/promotion path [1](#0-0) [2](#0-1) . This check enforces the `ShouldCheckSenderCode` (EOA-only) restriction as well as token balance/allowance conditions. However, the code path that actually turns a pending approve/swap pair into an executed, gas-lending bundle at block assembly - `ExtractTxBundles` → `IsExecutable`/`VerifyExecutable` - never calls `checkBalanceForApprove`/`checkBalanceForSwap` or `getCurrentHasCode`; it only re-validates the structural Ax/Sx/AP1/SP1-4 conditions [3](#0-2) [4](#0-3) .

### Finding Description
This mirrors the GitLab report's bug class: a privilege/eligibility check (`Reporter` cannot upload Designs) is enforced only in the context where the object is first created/admitted (the reporter's own project), but a second code path (`Move to`) transplants the already-created object into a new context (the private project) without re-validating the permission that should gate it there. In Kaia's gasless module the analogous split is:

- Context A (tx-pool admission/promotion): `checkBalanceForApprove`/`checkBalanceForSwap`, gated by `GaslessConfig.ShouldCheckSenderCode()`, enforce that only EOAs (no contract code) with real token balance/allowance can participate in gasless swaps [5](#0-4) [6](#0-5) .
- Context B (block assembly / bundling): `ExtractTxBundles` builds the actual gas-lending bundle (`GetLendTxGenerator`) purely from `IsApproveTx`/`IsSwapTx`/`IsExecutable` checks, which validate token/spender whitelisting, sender-equality, nonce sequencing and repay-amount arithmetic (`VerifyExecutable`), but do not call `getCurrentHasCode` or re-verify balance/allowance the way `checkBalanceForApprove/checkBalanceForSwap` do [7](#0-6) .

Because these are two independently-implemented and independently-invoked checks (one only reachable via `GetCheckBalance()`, wired through `blockchain/tx_pool.go`/`tx_list.go`, and the other reachable via the `TxBundlingModule` interface used in `work/worker.go`'s `ApplyTransactions`/`commitBundleTransaction` [8](#0-7) [9](#0-8) ), any state change to the sender's account (most notably gaining contract code, e.g. via EIP-7702-style code delegation/account update) that happens between "Context A" (pool admission time) and "Context B" (actual block building) is never re-validated. A swap tx that was legitimately queued while the sender was a plain EOA can still be included by `ExtractTxBundles`/`GetLendTxGenerator` and lent gas even after the sender has become a contract account - exactly analogous to the reporter's design surviving the "Move" into a context where it should have been disallowed.

### Impact Explanation
`GetLendTxGenerator` results in the protocol/gasless-relayer effectively lending KAIA (paying gas) on behalf of the sender, an economically valuable action that the `ShouldCheckSenderCode` restriction is specifically meant to gate to EOAs only (to avoid contract accounts gaming the exchange-rate/repay logic, re-entrancy, or abusing the swap router in ways an EOA cannot). Because the eligibility gate is not re-checked at the point where value (gas lending) is actually granted, an attacker can route around an EOA-only restriction and obtain fee-delegation-like gas lending for a smart-contract-controlled account, which is unauthorized value movement/fee abuse within the gasless settlement flow.

### Likelihood Explanation
Exploitation requires only a single unprivileged actor: submit a valid approve/swap pair as an EOA (passing `checkBalanceForApprove`/`checkBalanceForSwap` at admission), then before the transactions are packed into a block, transition the same address to a contract account (e.g., via an EIP-7702/account-update style transaction) so that `getCurrentHasCode` would have returned true had it been re-checked. Since `ExtractTxBundles`/`VerifyExecutable` do not perform this check, the bundle can still be included by the block builder. This is entirely achievable by a public RPC caller with no elevated privileges, using ordinary transaction submission timing, making the likelihood non-trivial for chains that enable `ShouldCheckSenderCode`.

### Recommendation
Re-invoke (or fold in) the same sender-eligibility and balance/allowance checks performed by `checkBalanceForApprove`/`checkBalanceForSwap` (in particular `ShouldCheckSenderCode`) inside `VerifyExecutable`/`IsExecutable`, so that the bundling/block-assembly path enforces identical eligibility rules as tx-pool admission, immediately before `GetLendTxGenerator` is invoked in `ExtractTxBundles`. This ensures the restriction is validated in the same context where the privileged action (gas lending) is finally granted, not only in the context where the transaction was first queued.

### Proof of Concept
1. Enable `GaslessConfig.ShouldCheckSenderCode()` so gasless swaps are restricted to EOAs.
2. From an EOA `A`, submit a valid `approveTx` + `swapTx` pair; they pass `checkBalanceForApprove`/`checkBalanceForSwap` in `kaiax/gasless/impl/tx_pool.go` and get promoted to pending.
3. Before the block builder processes them, have `A` submit (and get it applied first, in an earlier block/nonce slot) an account-code-granting transaction so that `getCurrentHasCode(A)` would now return `true`.
4. When the block builder calls `ExtractTxBundles` (`kaiax/gasless/impl/builder.go`), it only calls `IsExecutable`/`VerifyExecutable` (`kaiax/gasless/impl/getter.go`), which never calls `getCurrentHasCode`; the approve/swap pair is still bundled with `GetLendTxGenerator`, and gas is lent to `A` even though `A` is now a contract account that should have been disallowed by `ShouldCheckSenderCode`.

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

**File:** kaiax/gasless/impl/getter.go (L195-266)
```go
// IsGaslessPattern checks following conditions:
// Ax. IsApproveTx conditions (if ApproveTx != nil)
// Sx. IsSwapTx conditions
// AP1. ApproveTx.from == SwapTx.from
// SP1. ApproveTx.to == SwapTx.token
// SP2. ApproveTx.amount >= SwapTx.amountIn
// SP3. ApproveTx.nonce+1 == SwapTx.nonce and Gasless transactions are head for nonce
// SP4. SwapTx.amountRepay = RepayAmount(ApproveTx, SwapTx)
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

**File:** work/worker.go (L606-613)
```go
func (env *Task) ApplyTransactions(txs *types.TransactionsByPriceAndNonce, bc BlockChain, nodeAddr common.Address, txBundlingModules []builder.TxBundlingModule) []*types.Log {
	var (
		arrayTxs                 = builder.Arrayify(txs)
		incorporatedTxs, bundles = builder.ExtractBundlesAndIncorporate(arrayTxs, txBundlingModules)
		totalTxs                 = len(incorporatedTxs)
		totalBundles             = len(bundles)
		coalescedLogs            []*types.Log
	)
```

**File:** work/worker.go (L877-954)
```go
func (env *Task) commitBundleTransaction(bundle *builder.Bundle, bc BlockChain, nodeAddr common.Address, vmConfig *vm.Config) (error, *types.Transaction, []*types.Log) {
	lastSnapshot := env.state.Copy()
	gasUsedSnapshot := env.header.GasUsed
	blobGasUsedSnapshot := env.header.BlobGasUsed
	blobsSnapshot := env.blobs
	tcountSnapshot := env.tcount
	txs := []*types.Transaction{}
	receipts := []*types.Receipt{}
	logs := []*types.Log{}

	markAllTxUnexecutable := func() {
		for _, txOrGen := range bundle.BundleTxs {
			if txOrGen.IsConcreteTx() {
				tx, _ := txOrGen.GetTx(0)
				tx.MarkUnexecutable(true)
			}
		}
	}

	restoreEnv := func() {
		env.state.Set(lastSnapshot)
		env.header.GasUsed = gasUsedSnapshot
		env.tcount = tcountSnapshot
		// blob related env are restored to the snapshot
		env.header.BlobGasUsed = blobGasUsedSnapshot
		env.blobs = blobsSnapshot
	}

	var totalTxSize uint64 = 0
	for _, txOrGen := range bundle.BundleTxs {
		tx, err := txOrGen.GetTx(env.state.GetNonce(nodeAddr))
		if err != nil {
			logger.Error("TxGenerator error", "error", err)
			markAllTxUnexecutable()
			restoreEnv()
			return kerrors.ErrTxGeneration, nil, nil
		}

		env.state.SetTxContext(tx.Hash(), common.Hash{}, env.tcount)
		receipt, _, err := bc.ApplyTransaction(env.config, &nodeAddr, env.state, env.header, tx, &env.header.GasUsed, vmConfig)
		// Bundled tx will be rejected with any receipt.Status other than success.
		// There may be cases where a revert occurs within the EVM, which could result in an attack on a tx sender in an already executed bundle.
		if err != nil || receipt.Status != types.ReceiptStatusSuccessful {
			if err != vm.ErrInsufficientBalance && err != vm.ErrTotalTimeLimitReached {
				markAllTxUnexecutable()
			}
			receiptStatus := ""
			if receipt != nil {
				receiptStatus = strconv.FormatUint(uint64(receipt.Status), 10)
			}
			logger.Warn("ApplyTransaction error, restoring env",
				"blockNum", env.header.Number.String(), "txHash", tx.Hash().String(),
				"error", err, "receiptStatus", receiptStatus,
			)
			restoreEnv()
			if err == nil {
				err = kerrors.ErrRevertedBundleByVmErr
			}
			return err, tx, nil
		}

		env.tcount++
		totalTxSize += uint64(tx.Size())
		txs = append(txs, tx)
		receipts = append(receipts, receipt)
		logs = append(logs, receipt.Logs...)
		if tx.Type() == types.TxTypeEthereumBlob {
			env.blobs += len(tx.BlobHashes())
			*env.header.BlobGasUsed += tx.BlobGas()
		}
	}

	env.size += totalTxSize
	env.txs = append(env.txs, txs...)
	env.receipts = append(env.receipts, receipts...)

	return nil, nil, logs
}
```
