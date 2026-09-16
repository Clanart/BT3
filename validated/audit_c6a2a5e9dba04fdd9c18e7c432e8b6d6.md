### Title
Gasless swap admission checks a stale AMM exchange rate, so lent gas is not guaranteed to be repaid when the price moves before block inclusion - (File: kaiax/gasless/impl/tx_pool.go)

### Summary
`checkBalanceForSwap` validates a `GaslessSwapTx`'s `minAmountOut`/`amountIn` against the router's exchange rate read at the moment of tx-pool admission, not at the moment the swap is actually executed on-chain. Because the proposer prepays the user's gas (`LendTxGenerator`) expecting to be repaid out of the swap's output, any AMM price movement between admission and inclusion can make the earlier-approved `amountIn`/`minAmountOut` insufficient for `amountRepay` at execution time, directly analogous to the reported `PartyBFacetImpl`/`PartyAFacetImpl.sendQuote` "no slippage protection" issue where a price move between quote creation and execution breaks the trade.

### Finding Description
The Gasless module (KIP-247) lets a proposer front the gas fee for a user's transaction and recoups it by having the user's `GaslessSwapTx` swap ERC-20 tokens for KAIA and repay `amountRepay` to the proposer, enforced by `SwappedForGas`. Before a `GaslessSwapTx` is admitted to the pool, `checkBalanceForSwap` performs: [1](#0-0) 

This function checks `minAmountOut >= amountRepay` and `amountIn >= router.GetAmountIn(token, minAmountOut)` using a live call to the Uniswap-style router (`kip247.NewGaslessSwapRouterCaller(...).GetAmountIn`) evaluated against the *current* chain state at admission time: [2](#0-1) 

This check is only run when the transaction is (re-)validated for pool admission/promotion (`GetCheckBalance`/`PreAddTx`), not re-verified immediately before the transaction is actually executed in a block: [3](#0-2) 

Between the time this check passes and the time the bundle `[LendTxGenerator, GaslessApproveTx, GaslessSwapTx]` is actually built and executed, the AMM reserves backing `GetAmountIn`/`GetAmountsOut` can shift — from ordinary trading activity, from other swaps ordered earlier in the same block, or simply because the tx sits in the pool until a future block (bounded only by the user-supplied `deadline`). The values `amountIn`, `minAmountOut`, and `amountRepay` embedded in the already-signed `GaslessSwapTx` cannot be adjusted to reflect this, and there is no notion of "slippage tolerance" in the design (see the KIP-247 gasless README describing the fixed-parameter swap flow): [4](#0-3) 

The end-to-end test confirms these checks are evaluated with hard-coded, pre-computed amounts (`minAmountOut`, `amountRepay`) derived from the pool state observed before the transactions are sent, with no mechanism to re-derive them at inclusion time: [5](#0-4) [6](#0-5) 

Since the module's own repayment guarantee (`minAmountOut >= amountRepay`) is validated only against a point-in-time snapshot of the AMM rate rather than at settlement, this is the same root cause class as the reported bug: a quote/price commitment made ahead of execution with no tolerance band, so intervening price movement invalidates the assumption the commitment was built on.

### Impact Explanation
If the AMM price moves unfavorably for the user between admission and block inclusion, the on-chain swap can yield less than `amountRepay` KAIA even though it passed the off-chain `checkBalanceForSwap` gate. Because the proposer (or the `LendTxGenerator`'s issuing key) has already advanced gas value expecting repayment via the swap, the gasless flow's implicit repayment guarantee is broken by price movement it does not defend against, which can cause the proposer's lent value not to be fully recouped, or the swap execution to revert (wasting the pre-lent gas and burning the user's opportunity to complete the gasless flow, forcing resubmission). This affects an unprivileged, single-transaction path — any gasless-swap sender or block proposer processing the mempool — matching the "gasless … settlement theft"/"fee-delegation abuse" class explicitly in scope.

### Likelihood Explanation
Medium. It requires only ordinary AMM price movement (or another swap being ordered ahead of the gasless swap tx in the same block, e.g. by MEV/searcher activity or normal trading) between the time `checkBalanceForSwap` runs and the time the bundle is actually executed — no privileged access, validator collusion, or off-chain component compromise is needed. The `deadline` field bounds only staleness in time, not price movement, so the window is realistically block-to-block.

### Recommendation
Re-validate `minAmountOut`/`amountIn`/`amountRepay` against the router's live exchange rate immediately before/at bundle execution (not only at pool admission), and/or reject or re-price the swap if the AMM state has diverged materially from the state at admission time. Alternatively, require an explicit slippage-tolerance parameter that the on-chain `swapForGas` enforces atomically against the *actual* execution-time rate, so admission and execution price checks cannot diverge.

### Proof of Concept
Conceptual scenario (cannot be fully reproduced without executing a live devnet, but derivable purely from the code paths cited above):
1. User A submits `approve` + `swapForGas(token, amountIn, minAmountOut, amountRepay, deadline)` where `minAmountOut` and `amountIn` were computed against the router's current reserves, satisfying `checkBalanceForSwap` (`kaiax/gasless/impl/tx_pool.go:107-142`).
2. Before A's bundle is selected for inclusion, other swaps against the same token pair execute (either included ahead of A's bundle in the same block, or in prior blocks while A's tx waits in the pool up to `deadline`), shifting the pool's reserves.
3. When A's bundle `[LendTxGenerator, GaslessApproveTx, GaslessSwapTx]` finally executes, the actual on-chain swap output for the fixed `amountIn` is now below the `amountRepay` that was pre-validated as safe, or the swap reverts outright — even though the transaction was accepted by the pool's slippage-style check earlier.
4. This mirrors the reported `sendQuote`/`PartyBFacetImpl` issue: a price commitment fixed at quote/admission time with no tolerance band, invalidated by price movement before settlement.

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

**File:** kaiax/gasless/impl/tx_pool.go (L102-142)
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
```

**File:** kaiax/gasless/README.md (L9-36)
```markdown
Note that gasless transaction does not mean the gas prices of gasless transactions are zero, but it means that proposer will lend the user with gas fee and user will pay back during gasless swap.

The gas fee of gasless transaction's is funded by block proposer (i.e., lend transaction generated by `GetLendTxGenerator`), and user repays the lent amount during gasless swap.

### Transaction pool rules

#### Ready

This module is responsible for promoting gasless transactions.
Sender's nonce of GaslessSwapTx is checked to distinguish if GaslessApproveTx is expected. If `tx.nonce == GetNonce(sender) + 1`, GaslessApproveTx is expected. If `tx.nonce == GetNonce(sender)`, GaslessApproveTx is not expected.

If GaslessApproveTx is expected, GaslessApproveTx and GaslessSwapTx can be promoted when they are both ready for execution.
Otherwise, GaslessSwapTx can be promoted when it is ready for execution.

See ready condition [KIP-247](https://kips.kaia.io/KIPs/kip-247) and the implementation `IsExecutable(approveTxOrNil, swapTx *types.Transaction) bool`.

#### Balance check

Sender balance check is omitted for gasless transactions (see `GetCheckBalance()`).

### Block building rules

Upon detection of GaslessTxs, the following logics are executed:

- Per sender, if exists, GaslessApproveTx is relocated before GaslessSwapTx.
- LendTxGenerator is prepended before GaslessApproveTx.
- A new bundle is generated which contain either `[LendTxGenerator, GaslessApproveTx, GaslessSwapTx]` or `[LendTxGenerator, GaslessSwapTx]`
- If the bundle has conflict with any previous bundles, it is excluded from the returned bundle list.
```

**File:** tests/gasless_test.go (L150-174)
```go

	var (
		gasPriceBN         = new(big.Int).Mul(big.NewInt(50), bigGkei)
		R1                 = new(big.Int).Mul(big.NewInt(21000), gasPriceBN)
		R2                 = new(big.Int).Mul(big.NewInt(100000), gasPriceBN)
		R3                 = new(big.Int).Mul(big.NewInt(500000), gasPriceBN)
		ammontRepay        = new(big.Int).Add(R1, new(big.Int).Add(R2, R3))
		amountRepaySwap    = new(big.Int).Add(R1, R3)
		transferToken      = new(big.Int).Mul(big.NewInt(100), bigKaia)
		swapExpectedOutput = amountsOut[1]
		margin             = new(big.Int).Div(swapExpectedOutput, big.NewInt(100))
		minAmountOut       = new(big.Int).Add(ammontRepay, margin)
		deadline           = new(big.Int).Add(chain.CurrentBlock().Time(), big.NewInt(300))
	)

	//// Reject when token balance is zero, approval is zero.

	// reject approveTx when token balance is zero
	_, err = sendApproveTx(t, testTokenContract, accounts[1], gsrAddr, abi.MaxUint256)
	assert.ErrorContains(t, err, "insufficient sender token balance")
	assert.ErrorContains(t, err, "have=0, want=nonzero")

	// reject swapTx when token isn't yet approved
	_, err = sendSwapTx(t, gsrContract, accounts[1], testTokenAddr, swapAmmount, minAmountOut, ammontRepay, deadline)
	assert.ErrorContains(t, err, "insufficient approval: approval=0")
```

**File:** tests/gasless_test.go (L245-253)
```go
	//// Reject obviously reverting SwapTx.

	// reject swapTx when minAmountOut < amountRepay
	_, err = sendSwapTx(t, gsrContract, accounts[0], testTokenAddr, swapAmmount, common.Big0, amountRepaySwap, deadline)
	assert.ErrorContains(t, err, "insufficient minAmountOut")

	// reject swapTx when amountIn < router.GetAmountIn(minAmountOut)
	_, err = sendSwapTx(t, gsrContract, accounts[0], testTokenAddr, common.Big0, minAmountOut, amountRepaySwap, deadline)
	assert.ErrorContains(t, err, "insufficient amountIn")
```
