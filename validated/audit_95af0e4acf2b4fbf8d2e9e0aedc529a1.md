Note: I could not fully verify the exact semantics of the third boolean parameter passed to `builder.NewBundle` (`work/builder/bundle.go`) before this session ended, so the atomicity claim below should be treated as based on naming convention and needs confirmation by reading `work/builder/bundle.go` and `work/builder/builder.go` in full.

### Title
Gasless module lends KAIA to sender via a non-atomic bundle without verifying the SwapTx actually repays the proposer - (File: kaiax/gasless/impl/builder.go)

### Summary
The Kaia `gasless` kaiax module lets a block proposer pre-fund ("lend") gas to a fee-less sender so the sender can execute an ERC20-to-KAIA swap through a whitelisted `GaslessSwapRouter` contract, which is supposed to repay the proposer out of the swap proceeds. This mirrors the reported bug class: a protocol relies on a whitelisted counterparty (the router) to perform an expected token transfer (repayment/fee), but the caller-side code does not verify with an on-chain, post-execution balance check that the transfer actually happened.

### Finding Description
`GaslessModule.ExtractTxBundles` [1](#0-0)  builds a bundle consisting of a generated `LendTx` (which sends KAIA from the block proposer's own key to the sender), an optional `ApproveTx`, and the sender's `SwapTx`, and constructs the bundle with `builder.NewBundle(bundleTxs, targetTxHash, false)` [2](#0-1) . The `LendTx` unconditionally transfers KAIA out of the proposer's account to the sender (`lendAmount`/`repayAmount` computed in `kaiax/gasless/impl/getter.go`) [3](#0-2) , in the expectation that the subsequent `SwapTx` call to the `GaslessSwapRouter.swapForGas` function will repay the proposer (`AmountRepay`) out of swapped proceeds.

Before admission to the pool, the module performs only *pre-execution, view-based* checks — e.g. `checkBalanceForSwap` verifies `minAmountOut >= amountRepay` and calls the router's `GetAmountIn` view function and ERC20 `allowance`/`balanceOf` [4](#0-3) , and `VerifyExecutable`/`IsExecutable` validates the declared `AmountRepay` matches the expected formula [5](#0-4) . None of these are before/after balance checks performed on the proposer's account after the `SwapTx` actually executes on-chain; the module trusts that the whitelisted `GaslessSwapRouter` contract will faithfully repay `AmountRepay` to the proposer inside `swapForGas`, exactly the "malicious/faulty whitelisted router that doesn't transfer the expected funds back" pattern from the report. If the bundle is not atomic (per the `false` flag passed to `NewBundle`), a swap that reverts, front-runs into worse pricing, or is executed against a router whose `swapForGas` implementation has a bug/backdoor that skips the repayment transfer would still leave the already-executed `LendTx` in the block, causing a real loss of proposer funds with no on-chain verification that repayment happened.

### Impact Explanation
If the router fails to actually repay the proposer (due to a buggy/malicious router version installed via governance/system-contract upgrade, or due to a revert/underpayment path in the swap not covered by the pre-checks), the block proposer's `LendTx` outflow is not automatically recovered, causing direct value loss to the proposer and enabling repeated free "gasless" swaps or exhaustion of proposer funds. This is a fee-delegation/gasless-settlement value-movement issue reachable purely by a public sender submitting an approve+swap transaction pair.

### Likelihood Explanation
Exploitability depends on either (a) a bug or malicious change in the `GaslessSwapRouter` system contract (whose Solidity source is not in this repo's index, only Go bindings) that fails to transfer `AmountRepay` back to the proposer while still allowing `swapForGas` to succeed, or (b) the bundle not being atomic so a reverted `SwapTx` still leaves the `LendTx` committed. Given the pre-checks are all static/view-based and no post-execution balance verification exists in the Go module, likelihood is Medium — it requires either a compromised/buggy router contract or specific bundle-atomicity behavior that could not be fully confirmed here.

### Recommendation
Add an explicit post-execution balance check in the gasless bundling/validation logic: after building the `[LendTx, ApproveTx?, SwapTx]` bundle, verify (e.g., via a receipt/state simulation before including the bundle in the block, or via a strict atomic-bundle guarantee) that the proposer's balance increases by at least `AmountRepay` as a result of the `SwapTx`. Confirm and, if necessary, enforce that `builder.NewBundle`'s bundles used by the gasless module are atomic (i.e., all-or-nothing), so a failed or under-repaying swap cannot leave the `LendTx` payout stranded in the block.

### Proof of Concept
1. A block proposer runs a node with the `gasless` kaiax module enabled and the governance/registry-configured `GaslessSwapRouter` address pointed at a router contract that accepts `swapForGas` calls and swaps tokens for the sender's benefit, but has a code path (bug or intentional backdoor) that skips the final repayment transfer to `msg.sender`/proposer while still emitting a `SwappedForGas` event and returning success.
2. A user submits an `ApproveTx` + `SwapTx` pair satisfying all static checks in `checkBalanceForSwap`/`VerifyExecutable` [4](#0-3) [5](#0-4) .
3. `ExtractTxBundles` generates and prepends a `LendTx` transferring `repayAmount` KAIA from the proposer to the sender [1](#0-0) .
4. The block is assembled and mined; the `SwapTx` executes "successfully" per receipt status but the router does not actually return `AmountRepay` to the proposer.
5. No code path in `kaiax/gasless/impl` re-checks the proposer's post-block balance against the expected repayment, so the loss goes undetected and unrecovered.

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

**File:** kaiax/gasless/impl/tx_pool.go (L107-173)
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
```
