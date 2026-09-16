### Title
Gasless-swap admission uses stale AMM price, allowing front-running to invalidate the bundled swap after the lender has already advanced gas - (File: kaiax/gasless/impl/tx_pool.go)

### Summary
The Sherlock report describes a TOCTOU (time-of-check/time-of-use) flaw: the amount of tokens actually obtained during flash-loan execution can diverge from the amount validated earlier in the flow, causing the downstream repayment check to revert and creating a DOS. The Kaia `kaiax/gasless` module (KIP-247) has a structurally analogous pattern: transaction-pool admission validates a gasless swap's expected output against the *current* AMM price, but that price can be legitimately changed by any other public transaction before the swap is actually executed as part of an atomic, proposer-funded bundle.

### Finding Description
`GaslessModule.checkBalanceForSwap` queries the live `GaslessSwapRouter` contract for `GetAmountIn(token, minAmountOut)` and validates the swap transaction's declared `amountIn`/`minAmountOut`/`amountRepay` against the *current* on-chain AMM reserves at the moment the check runs: [1](#0-0) 

This check is a mempool-level admission check, re-executed on the *current* head state, not something committed atomically with the eventual block-building execution: [2](#0-1) 

When the block is actually assembled, `ExtractTxBundles` prepends a `LendTxGenerator` transaction — funded by the block proposer — ahead of the `ApproveTx`/`SwapTx` pair, forming an atomic `[LendTx, ApproveTx, SwapTx]` (or `[LendTx, SwapTx]`) bundle: [3](#0-2) 

As documented, "the gas fee of gasless transaction's is funded by block proposer... and user repays the lent amount during gasless swap," and the block-building rule always prepends `LendTxGenerator` before the bundle: [4](#0-3) 

The actual on-chain `swapForGas` call enforces `minAmountOut` (a standard AMM slippage guard) at execution time — this is the analog of `wagmiLeverageFlashCallback`'s post-hoc `holdTokenAmtIn > decodedData.holdTokenDebt` check in the reported bug. Because the tx-pool's `checkBalanceForSwap` validation happens against a state snapshot that can become stale (any intervening transaction that trades against the same pool used by `GaslessSwapRouter` shifts the AMM reserves), a swap that passed mempool admission can still fail its on-chain `minAmountOut` check by the time the bundle is actually included — exactly the "amount acquired differs from the amount expected by the function" root cause described in the report.

### Impact Explanation
Any unprivileged public RPC caller/trader can submit an ordinary swap transaction against the same AMM pool that `GaslessSwapRouter` relies on for pricing, shifting reserves between the time `checkBalanceForSwap` validated a pending `GaslessSwapTx` and the time the proposer actually builds/executes the atomic `[LendTx, ApproveTx, SwapTx]` bundle. This causes the swap's on-chain slippage check to fail, reverting the swap execution after the proposer-funded `LendTxGenerator` has already been prepended into the same atomic bundle. This is a denial-of-service against the gasless/fee-delegation mechanism (repeated griefing of legitimate gasless users and wasted bundle-building effort by proposers), directly mirroring the Sherlock M-2 pattern where a divergence between the previously-checked and actually-realized token amount causes the settlement step to revert.

### Likelihood Explanation
The attack requires only a single ordinary, unprivileged transaction trading against the AMM pool backing the `GaslessSwapRouter` token — something any public RPC caller can submit — timed to land between admission of the victim's `GaslessSwapTx` and block assembly. No special privileges, validator access, or leaked keys are required, making this reachable purely through normal transaction submission.

### Recommendation
Re-validate the swap's expected output (or re-run `checkBalanceForSwap`-equivalent logic) against the state immediately prior to bundle execution at block-building time, not only at tx-pool admission time, or have the bundle-atomicity mechanism treat a failed `SwapTx` as fully excludable without side effects. Note: I was not able to locate/verify the exact atomicity semantics of `work/builder.Bundle` (whether a reverted `SwapTx` reverts the entire bundle including the `LendTx`) within the indexed portion of the codebase, so the precise value-loss vs. pure-DOS characterization of this issue should be confirmed by reviewing `work/builder` bundle execution logic directly (recommend a full Devin session with file access for `work/builder/*` to confirm bundle revert semantics).

### Proof of Concept
1. User A submits `ApproveTx` + `GaslessSwapTx(token, amountIn, minAmountOut, amountRepay, deadline)` calling `GaslessSwapRouter.swapForGas`.
2. `GaslessModule.checkBalanceForSwap` validates `amountIn >= GetAmountIn(token, minAmountOut)` using the AMM pool's *current* reserves and admits the tx to the pool: [1](#0-0) 
3. Before the proposer builds a block containing this bundle, any public sender submits a normal swap on the same underlying AMM pool, shifting reserves and worsening the effective exchange rate for `token`.
4. The proposer's builder detects the gasless tx and constructs the atomic bundle `[LendTxGenerator, ApproveTx, SwapTx]`: [5](#0-4) 
5. During execution, `swapForGas`'s internal AMM call now returns less output than `minAmountOut`, causing the swap to revert — analogous to `holdTokenAmtIn > decodedData.holdTokenDebt` reverting in the referenced report — after the proposer has already advanced gas via the `LendTxGenerator` in the same atomic unit.

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

**File:** kaiax/gasless/impl/tx_pool.go (L128-141)
```go
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
```

**File:** kaiax/gasless/impl/builder.go (L28-51)
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
```

**File:** kaiax/gasless/README.md (L9-35)
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
```
