## Title
Stale on-chain price used for gasless swap admission enables amountIn/slippage bypass at execution time - (File: kaiax/gasless/impl/tx_pool.go)

### Summary
The external report concerns Chainlink price feeds being consumed by `OracleRouterBase`/`OETHOracleRouter` without validating recency (`updatedAt`) or bounds, allowing stale/out-of-range prices to be used for critical mint/redeem accounting. The reachable analog in this Kaia repo is the `kaiax/gasless` module's mempool-admission price check, which reads a live AMM price via `GaslessSwapRouterCaller.GetAmountIn` and validates `swapArgs.AmountIn` against it once, at admission time, based on whatever state the node currently has — not the state at which the transaction will actually execute.

### Finding Description
`GaslessModule.checkBalanceForSwap` computes the "required" `amountIn` for a declared `minAmountOut` by calling the on-chain DEX router (`routerContract.GetAmountIn`) against the *current* state visible to the transaction pool, and only enforces `swapArgs.AmountIn >= requiredAmountIn` at that single point in time: [1](#0-0) 

This check is invoked via `GetCheckBalance`, which is wired into `IsModuleTx`/tx-pool admission flow, i.e., it runs once when the gasless bundle (approve+swap or standalone swap) is submitted or promoted: [2](#0-1) 

There is no re-validation of the AMM price/quote at inclusion time from the perspective of the gasless module itself — the only invariant enforced on-chain is presumably inside the `GaslessSwapRouter.swapForGas` contract (bytecode-only in this repo; Solidity source not indexed), which is expected to independently enforce `minAmountOut`. Because the module's admission-time price check and the contract's execution-time check are two separate reads of AMM reserves that can diverge (reserves move between mempool admission and actual block inclusion, especially across multiple blocks while a tx sits in the pool, or due to reordering/front-running within the same block by other swaps or by the auction/bid mechanism), the mempool-level "amountIn is sufficient for the market price" guarantee is not durable through to execution. This mirrors the reported bug class: a price is read and used to gate an action without confirming it is still valid/fresh/within bounds at the moment the action is actually executed.

### Impact Explanation
If `GetCheckBalance`'s admission-time price check is relied upon by other parts of the system (e.g., bundling/lending logic that fronts gas to the user based on `tx.Fee()` expecting the swap to successfully cover `amountRepay`), a stale quote can let an underfunded or unfavorable swap into the pending pool that later reverts on-chain (wasting the gas sponsor's/lender's fronted gas) or, if the on-chain contract's own slippage protection is weaker than assumed, could let a swap execute at a worse rate than the mempool validated, potentially causing insufficient repayment of fronted gas (`amountRepay`) and fund-flow issues for the gas-lending mechanism. This is a Medium-severity liveness/fund-safety issue for the gasless subsystem's economic assumptions rather than direct fund theft, since final enforcement depends on the (unverified in this index) on-chain contract.

### Likelihood Explanation
Any unprivileged gasless user or a public-RPC caller submitting `approve`+`swapForGas` pairs can trigger this path organically; AMM reserves routinely change between tx submission and block inclusion (multiple pending blocks, other traders, or MEV/auction bid transactions targeting the same pool). No special privilege is needed — this is reachable by a standard gasless transaction submitted through `auction_submitBid`-adjacent or normal tx-pool flow that decodes into `SwapArgs`.

### Recommendation
Re-validate the DEX price/quote (via `GetAmountIn`) immediately prior to promotion/inclusion (e.g., in `IsReady`/bundle building right before execution), not only at initial admission, and/or bound the acceptable staleness (e.g., reject if too many blocks have elapsed since the quote was checked). Confirm that `GaslessSwapRouter.swapForGas` (Solidity source, not available in this index) independently and strictly enforces `minAmountOut` on-chain so that a stale mempool-level check cannot itself cause economic loss — this repo's index does not include the Solidity source, so this cannot be fully confirmed and should be verified directly.

### Proof of Concept
Conceptual PoC (cannot be fully executed without the `GaslessSwapRouter.sol` source, which is not present in the indexed codebase):
1. Submit `approveTx` + `swapTx` (`swapForGas`) with `amountIn` computed to just satisfy `GetAmountIn(token, minAmountOut)` at block N.
2. Before the bundle is promoted/included, submit unrelated large swaps against the same pool (or let organic activity/multiple pending blocks pass) to shift AMM reserves so that the true required `amountIn` for `minAmountOut` increases.
3. Bundle gets promoted based on the original (stale) `checkBalanceForSwap` result from step 1, since `GetCheckBalance` is not necessarily re-invoked with updated reserves before inclusion.
4. At execution, the swap either reverts (wasting fronted gas from the lending mechanism) or, if the contract's slippage enforcement is weaker than the admission-time quote assumed, executes with `amountRepay` not being fully covered.

**Uncertainty note:** I could not locate the `GaslessSwapRouter.sol` Solidity source in this index (only compiled bindings/bytecode in `contracts/bindings/kip247/GaslessSwapRouter.go`) or confirm exactly how often `GetCheckBalance` is re-invoked during tx-pool promotion/reorg cycles in `blockchain/tx_pool.go`. These would need direct source-level confirmation (e.g., via a full Devin session with repository access) to fully validate the severity and exploitability of this TOCTOU gap.

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

**File:** kaiax/gasless/impl/tx_pool.go (L102-141)
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
```
