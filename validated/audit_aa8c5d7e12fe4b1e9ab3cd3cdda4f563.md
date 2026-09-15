### Title
Gasless swap admission check can be bypassed by donating tokens directly to the underlying DEX pair before `getAmountIn` is evaluated, breaking the lender-repayment invariant - (File: kaiax/gasless/impl/tx_pool.go)

### Summary
The Basin finding shows that an AMM's `x*y*EXP_PRECISION = k²` invariant can be silently broken when a user donates tokens directly to the pool and calls a reserve-syncing function, causing downstream code that assumes the invariant holds to compute wrong values and misbehave. Kaia's gasless-transaction module (KIP-247) relies on an analogous invariant: it trusts `GaslessSwapRouter.getAmountIn(token, minAmountOut)`, which is derived from the live reserves of the underlying DEX pair (Uniswap-V2-style `getReserves`/`sync`), to guarantee that `amountIn` is sufficient for the swap to output at least `amountRepay` to the block proposer who lent the gas. Any unprivileged token holder can manipulate the pair's reserves (direct token transfer + `sync()`) between the moment the tx-pool admission check reads the reserves and the moment the swap is actually executed on-chain, invalidating the assumption baked into the txpool admission check.

### Finding Description
`GaslessModule.checkBalanceForSwap` in [1](#0-0)  enforces the gasless-swap invariants purely against a *pending-state snapshot* read via `backends.NewBlockchainContractBackend`:
- `tx.minAmountOut >= tx.amountRepay`
- `tx.amountIn >= gsr.getAmountIn(token, minAmountOut)` — this pulls live reserves from the DEX pair, analogous to Basin's `_getReserves()`.

This check happens once, at tx-pool admission/promotion time [2](#0-1) , using whatever reserve state the DEX pair currently reports. The DEX pair contracts used by the gasless flow are UniswapV2-style pairs, which expose a public, unauthenticated `sync()` function [3](#0-2)  that reconciles `reserve0/reserve1` to the pair's actual token balances. Because `sync()`/direct-transfer-then-`sync()` is callable by any unprivileged address (exactly the "donate + sync" pattern in the Basin report), an attacker can:
1. Observe a pending `ApproveTx + SwapTx` gasless bundle in the mempool with a computed `requiredAmountIn` based on reserves at admission time.
2. Front-run it with a plain token transfer to the pair followed by `sync()` (or an actual swap) that shifts the reserves.
3. By the time the bundle is actually executed on-chain (after `LendTxGenerator` has already funded gas to the sender, per the module's block-building rules described in [4](#0-3) ), the real swap output for the *same* `amountIn`/`minAmountOut` pair can be smaller than the value that was validated at admission time.

The on-chain `GaslessSwapRouter` contract does carry its own `minAmountOut` enforcement (this is why the test in [5](#0-4)  shows explicit reverts for insufficient `amountIn`/`minAmountOut`), so a straightforward value-theft is not directly demonstrable from the indexed contract bindings alone — the actual Solidity source of `GaslessSwapRouter.sol` was not available in the indexed codebase to confirm whether `minAmountOut` is checked strictly against the *actual* execution-time reserves (protecting the proposer) or whether any state divergence between the txpool's admission check and block-execution-time state can cause a swap that the txpool decided was "ready"/promotable to subsequently revert or under-deliver during block building, wasting the proposer's already-issued `LendTxGenerator` gas advance.

### Impact Explanation
If the on-chain slippage check in `GaslessSwapRouter` is strict (which the binding names suggest), the primary impact is availability/DoS-style: the block proposer's `LendTxGenerator` transaction, which unconditionally advances gas to the gasless sender [6](#0-5) , is included in the block before the swap; if reserve manipulation causes the subsequent `SwapTx` to revert at execution time (after passing the admission-time check), the proposer's lent gas is not repaid (the repayment mechanism runs inside the swap transaction as shown by `SwappedForGas`/`FinalUserAmount` in [7](#0-6) ), causing the proposer to lose the fronted gas — a fee-delegation/gasless settlement loss reachable from an unprivileged public token holder, mirroring the Medium-severity "function/availability impacted + no direct step to permanently fix without external intervention" classification in the Basin report.

### Likelihood Explanation
Likelihood is limited by several factors that could not be fully resolved from the indexed code:
- The actual `GaslessSwapRouter.sol` source was not present in the index, so the precise on-chain enforcement order of `minAmountOut` vs. reserve state could not be directly verified.
- `LendTxGenerator`/bundling ordering means the lend tx and swap tx are proposer-generated in the same bundle typically in the same block, narrowing (but not eliminating) the window for reserve manipulation between admission and inclusion.
- `ShouldCheckSwapAmount()` is a configurable gate [8](#0-7) , meaning the check may or may not always be active depending on `GaslessConfig`.

Given these unresolved dependencies on the actual router contract logic, this should be treated as a **plausible but unverified** analog rather than a confirmed exploit.

### Recommendation
- Re-validate `amountIn >= getAmountIn(token, minAmountOut)` and reserve-derived pricing at block-execution time inside `GaslessSwapRouter`, not only at txpool admission time, and make the LendTx atomic/conditional on swap success within the same bundle so a manipulated swap cannot leave the proposer's lent gas unrepaid.
- Consider re-checking `checkBalanceForSwap` immediately before block inclusion (not only at promotion) using the exact state the swap will execute against.
- Obtain and review the actual `GaslessSwapRouter.sol` contract logic (not present in this index) to confirm whether `minAmountOut`/repayment is strictly enforced against final swap output at execution time.

### Proof of Concept
Not fully constructible from the indexed repository because the Solidity source of `GaslessSwapRouter` (specifically its `swapForGas`/repayment enforcement logic) is not available in the index — only Go bindings and the txpool-side admission check (`kaiax/gasless/impl/tx_pool.go`) were found. A concrete PoC would require:
1. Deploying/observing a pending gasless `ApproveTx + SwapTx` bundle targeting a known `token`/pair.
2. Front-running with `token.transfer(pair, X)` + `pair.sync()` (per `IUniswapV2Pair.Sync` in [3](#0-2) ) to shift reserves after the txpool's `getAmountIn` check but before block execution.
3. Observing whether the swap subsequently reverts/under-delivers relative to `amountRepay`, and whether the `LendTxGenerator` gas advance is left unrepaid.

Because step 3's outcome depends on `GaslessSwapRouter.sol` internals not present in the index, this cannot be confirmed as a full working exploit from Ask-mode alone. A Devin session with full repository/checkout access would be needed to inspect the actual contract source and validate the attack end-to-end.

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

**File:** kaiax/gasless/impl/tx_pool.go (L107-142)
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
```

**File:** contracts/bindings/uniswap/factory/UniswapV2Factory.go (L3125-3130)
```go
// Sync is a paid mutator transaction binding the contract method 0xfff6cae9.
//
// Solidity: function sync() returns()
func (_IUniswapV2Pair *IUniswapV2PairTransactor) Sync(opts *bind.TransactOpts) (*types.Transaction, error) {
	return _IUniswapV2Pair.contract.Transact(opts, "sync")
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

**File:** tests/gasless_test.go (L220-237)
```go
	// since gasPrice may be less than the theoretical value, we check that the current balance is greater than or equal to FinalUserAmount.
	// expected: (pre balance) = 0
	// expected: (current balance) >= FinalUserAmount
	_, _, gaslessBlockNum, _ := chain.GetTxAndLookupInfo(swapTx.Hash())
	preState, _, err := node.APIBackend.StateAndHeaderByNumber(context.Background(), rpc.BlockNumber(gaslessBlockNum-1))
	if err != nil {
		t.Fatal(err)
	}
	currentState, _, err := node.APIBackend.StateAndHeaderByNumber(context.Background(), rpc.BlockNumber(gaslessBlockNum))
	if err != nil {
		t.Fatal(err)
	}
	swappedForGasEvent, err := gsrContract.ParseSwappedForGas(*swapTxReceipt.Logs[len(swapTxReceipt.Logs)-1]) // SwappedForGas is issued at the end of swapForGas
	if err != nil {
		t.Fatal(err)
	}
	require.True(t, preState.GetBalance(accounts[0].Addr).Cmp(common.Big0) == 0)
	require.True(t, currentState.GetBalance(accounts[0].Addr).Cmp(swappedForGasEvent.FinalUserAmount) != -1)
```

**File:** tests/gasless_test.go (L245-258)
```go
	//// Reject obviously reverting SwapTx.

	// reject swapTx when minAmountOut < amountRepay
	_, err = sendSwapTx(t, gsrContract, accounts[0], testTokenAddr, swapAmmount, common.Big0, amountRepaySwap, deadline)
	assert.ErrorContains(t, err, "insufficient minAmountOut")

	// reject swapTx when amountIn < router.GetAmountIn(minAmountOut)
	_, err = sendSwapTx(t, gsrContract, accounts[0], testTokenAddr, common.Big0, minAmountOut, amountRepaySwap, deadline)
	assert.ErrorContains(t, err, "insufficient amountIn")

	// reject swapTx when balance < amountIn
	// the test acc first received `transferToken` but used up some. So it has less than `transferToken`.
	_, err = sendSwapTx(t, gsrContract, accounts[0], testTokenAddr, transferToken, minAmountOut, amountRepaySwap, deadline)
	assert.ErrorContains(t, err, "insufficient balance")
```
