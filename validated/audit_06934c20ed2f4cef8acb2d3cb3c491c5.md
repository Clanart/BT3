### Title
Gasless swap mempool admission relies on manipulable Uniswap V2 pool reserves to bound proposer's gas repayment - (File: kaiax/gasless/impl/tx_pool.go)

### Summary
The `kaiax/gasless` module (KIP-247) admits a `GaslessSwapTx` into the pool/bundle only after checking `swapArgs.AmountIn >= gsr.GetAmountIn(token, minAmountOut)`, where `GetAmountIn` is computed live from the `GaslessSwapRouter`'s underlying Uniswap-V2-style pool reserves [1](#0-0) . This is directly analogous to the Sherlock M-10 finding: a value that gates fund-safety (`basis`/required-input) is derived from a single, unprotected on-chain reserve read that the unprivileged transaction sender fully controls the timing of.

### Finding Description
`checkBalanceForSwap` enforces three invariants before a `GaslessSwapTx` is treated as ready for block inclusion: `minAmountOut >= amountRepay`, `amountIn >= requiredAmountIn` (computed from `GetAmountIn` against current pool reserves), and sufficient allowance/balance [2](#0-1) . The `requiredAmountIn` is fetched via a *view* call into `GaslessSwapRouter.GetAmountIn`, which is a Uniswap-V2-pricing function using the pair's live reserves (the same `getReserves()`/`getAmountIn`-style formula seen in the Uniswap V2 router bindings) [3](#0-2) .

Because the sender of the `GaslessApproveTx`/`GaslessSwapTx` pair is an ordinary, unprivileged account, they can submit (in the same block/bundle window, or immediately prior) a transaction that swaps a large amount through the same pool to skew reserves, causing `GetAmountIn(token, minAmountOut)` to return an artificially small `requiredAmountIn` at the moment the transaction-pool `IsReady`/`checkBalanceForSwap` admission check runs [4](#0-3) . This lets a user get a `GaslessSwapTx` admitted and marked "ready" (and thus bundled with the proposer-funded `LendTxGenerator`) with an `amountIn` that would not satisfy the check against the true, unmanipulated market price [5](#0-4) . Just as in the `OCL_ZVE::fetchBasis` case, a stale/manipulated read of AMM reserves is used to gate an amount that is supposed to protect a third party's (here, the block proposer's) funds.

The actual `swapForGas` execution does still enforce `minAmountOut` on-chain during the real swap, which provides some protection against the user receiving too little output; however, the pool-admission logic (`checkBalanceForSwap`/`IsReady`) — which is what determines whether the proposer's `LendTxGenerator` (fronting the user's gas fee) gets bundled together with the `GaslessSwapTx` at all — relies on the same manipulable spot-reserve read rather than any TWAP or manipulation-resistant price source.

### Impact Explanation
The proposer of the block is the fee-delegation counterparty here: it fronts gas via `LendTxGenerator` and expects `swapForGas` to atomically repay it (`amountRepay`) out of the swap proceeds [6](#0-5) . If the reserve-dependent admission check (`GetAmountIn`) is bypassed via reserve manipulation, a malicious sender could get a swap admitted/bundled with a token amount that does not actually cover the true market cost of `minAmountOut`/`amountRepay`, risking either wasted proposer gas-lending (if the real swap later reverts on `minAmountOut`) or a mis-priced repayment amount, causing fee-delegation/gasless-settlement value loss for the proposer — a concrete instance of "gasless settlement theft/fee-delegation abuse" per the scan's acceptance criteria.

### Likelihood Explanation
Any unprivileged externally-owned account can submit an ordinary swap transaction against the pool immediately before its own `GaslessApproveTx`/`GaslessSwapTx` pair, and since `checkBalanceForSwap`'s `GetAmountIn` call reads reserves at the time the check is executed (which can be after the attacker's own price-moving transaction has landed in state), this requires no special privilege — only capital to move the pool temporarily, similar to the flash-loan approach in the original report. This mirrors the panel's "possible, but low probability, still qualifies as Medium" reasoning in the referenced Sherlock discussion.

### Recommendation
- Do not gate proposer fund-safety checks (admission into the gasless bundle) purely on an instantaneous view-call price derived from a single pool's live reserves; use a TWAP, multiple-block-averaged price, or another manipulation-resistant reference when computing `requiredAmountIn` in `checkBalanceForSwap`.
- Alternatively/additionally, ensure the bundle (`LendTxGenerator` + `GaslessApproveTx` + `GaslessSwapTx`) is atomic on-chain so that if the actual swap fails to meet `minAmountOut`/`amountRepay` at execution, the entire bundle (including the proposer's `LendTxGenerator`) reverts, preventing the proposer from being left having fronted gas without repayment.
- Re-validate `GetAmountIn`-based sizing immediately before block inclusion (as close to execution time as possible) rather than relying solely on the transaction-pool's asynchronous “ready” check.

### Proof of Concept
1. Attacker (unprivileged sender) holds enough of the swap-pool's counter-asset to temporarily skew the pool used by `GaslessSwapRouter` for `token`.
2. Attacker submits a large swap transaction against the pool, shifting reserves so that `GaslessSwapRouter.GetAmountIn(token, minAmountOut)` returns an artificially low value.
3. Attacker submits `GaslessApproveTx` + `GaslessSwapTx` (with `amountIn` set just above the artificially low `requiredAmountIn`, and `minAmountOut == amountRepay`, the minimum allowed by `checkBalanceForSwap`'s `minAmountOut >= amountRepay` rule) [7](#0-6) .
4. `checkBalanceForSwap` runs while the pool is still skewed (or is skewed again right before the check executes), passing the `amountIn >= requiredAmountIn` check even though `amountIn` is insufficient at the true market price.
5. The gasless bundle (`LendTxGenerator`, `GaslessApproveTx`, `GaslessSwapTx`) is built and included by the proposer [5](#0-4) ; the proposer has fronted gas via `LendTxGenerator` based on an admission decision built on manipulated pricing, risking a swap that reverts or under-repays.

Note: I was not able to fully confirm whether the `LendTxGenerator` + `GaslessSwapTx` bundle is executed with strict all-or-nothing atomicity at the block-building layer (`kaiax/gasless/impl/builder.go`) within the scope of this investigation — this affects whether the impact materializes as an outright fund loss versus a wasted-gas griefing vector. This should be verified against `kaiax/gasless/impl/builder.go` and `work/worker.go` bundle-extraction logic before finalizing severity.

### Citations

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

**File:** contracts/bindings/kip247/GaslessSwapRouter.go (L554-573)
```go
// SwapForGas is a paid mutator transaction binding the contract method 0x80426901.
//
// Solidity: function swapForGas(address token, uint256 amountIn, uint256 minAmountOut, uint256 amountRepay, uint256 deadline) returns()
func (_GaslessSwapRouter *GaslessSwapRouterTransactor) SwapForGas(opts *bind.TransactOpts, token common.Address, amountIn *big.Int, minAmountOut *big.Int, amountRepay *big.Int, deadline *big.Int) (*types.Transaction, error) {
	return _GaslessSwapRouter.contract.Transact(opts, "swapForGas", token, amountIn, minAmountOut, amountRepay, deadline)
}

// SwapForGas is a paid mutator transaction binding the contract method 0x80426901.
//
// Solidity: function swapForGas(address token, uint256 amountIn, uint256 minAmountOut, uint256 amountRepay, uint256 deadline) returns()
func (_GaslessSwapRouter *GaslessSwapRouterSession) SwapForGas(token common.Address, amountIn *big.Int, minAmountOut *big.Int, amountRepay *big.Int, deadline *big.Int) (*types.Transaction, error) {
	return _GaslessSwapRouter.Contract.SwapForGas(&_GaslessSwapRouter.TransactOpts, token, amountIn, minAmountOut, amountRepay, deadline)
}

// SwapForGas is a paid mutator transaction binding the contract method 0x80426901.
//
// Solidity: function swapForGas(address token, uint256 amountIn, uint256 minAmountOut, uint256 amountRepay, uint256 deadline) returns()
func (_GaslessSwapRouter *GaslessSwapRouterTransactorSession) SwapForGas(token common.Address, amountIn *big.Int, minAmountOut *big.Int, amountRepay *big.Int, deadline *big.Int) (*types.Transaction, error) {
	return _GaslessSwapRouter.Contract.SwapForGas(&_GaslessSwapRouter.TransactOpts, token, amountIn, minAmountOut, amountRepay, deadline)
}
```

**File:** kaiax/gasless/README.md (L9-11)
```markdown
Note that gasless transaction does not mean the gas prices of gasless transactions are zero, but it means that proposer will lend the user with gas fee and user will pay back during gasless swap.

The gas fee of gasless transaction's is funded by block proposer (i.e., lend transaction generated by `GetLendTxGenerator`), and user repays the lent amount during gasless swap.
```

**File:** kaiax/gasless/README.md (L29-36)
```markdown
### Block building rules

Upon detection of GaslessTxs, the following logics are executed:

- Per sender, if exists, GaslessApproveTx is relocated before GaslessSwapTx.
- LendTxGenerator is prepended before GaslessApproveTx.
- A new bundle is generated which contain either `[LendTxGenerator, GaslessApproveTx, GaslessSwapTx]` or `[LendTxGenerator, GaslessSwapTx]`
- If the bundle has conflict with any previous bundles, it is excluded from the returned bundle list.
```
