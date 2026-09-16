### Title
Gasless swap balance check uses stale on-chain DEX price at tx-pool admission, allowing flash-loan price manipulation to steal proposer-lent gas funds - ([File: kaiax/gasless/impl/tx_pool.go])

### Summary
The `kaiax/gasless` module (KIP-247) lets a proposer front gas fees for a user's `swapForGas` transaction, expecting to be repaid via the swap's output token amount. Admission of a `GaslessSwapTx` into the tx pool validates that `amountIn` is sufficient to cover `minAmountOut` by querying the `GaslessSwapRouter.getAmountIn()` view function against a DEX pool's *current* reserves at validation time [1](#0-0) . Because this AMM-based quote can be manipulated within the same block (classic flash-loan/large-swap reserve manipulation) between validation and actual execution, a malicious actor can distort the pool price so that by the time the bundled `[LendTx, ApproveTx, SwapTx]` executes on-chain, the real swap yields less than `amountRepay`, or the sender's swap consumes manipulated reserves to their advantage while the proposer's lent gas is not fully recouped.

### Finding Description
`checkBalanceForSwap` in `kaiax/gasless/impl/tx_pool.go` computes the required `amountIn` via `routerContract.GetAmountIn(nil, token, minAmountOut)`, which reads the DEX pool's current reserves through the whitelisted `GaslessSwapRouter` contract [1](#0-0) . This check is a point-in-time admission/promotion guard, not a guarantee that the same reserve ratio will hold when the transaction is actually executed inside a produced block. The actual repayment amount is computed deterministically by `repayAmount`/`lendAmount` based on advertised gas fees rather than the realized swap output [2](#0-1) , and the proposer's `LendTxGenerator` unconditionally advances KAIA to the user before the swap executes, as reflected in the bundling logic `[LendTxGenerator, ApproveTx, SwapTx]` [3](#0-2) . Because DEX reserves referenced by `GetAmountIn` can be moved by an attacker executing large swaps (economically equivalent to a flash-loan price-manipulation attack) immediately before the bundle executes in the same block, the sufficiency check performed at pool-admission time can be stale relative to the state at inclusion time, and the sender-controlled `AmountIn`/`MinAmountOut`/`AmountRepay` values are only checked against a snapshot that an attacker with a single transaction (or bundle position ahead of the gasless bundle) can invalidate.

### Impact Explanation
If the swap under-delivers versus what was validated (due to prior-in-block price manipulation), the on-chain `swapForGas` execution would either revert (denying service, wasting proposer-advanced gas that must still be repaid/accounted) or — if the contract's internal checks are weaker than assumed — allow the user to obtain a favorable swap while under-repaying the proposer. This directly maps to the reported bug class ("flashloan and price manipulation... draining funds") applied to the gasless subsidy mechanism: value that the proposer/protocol advances (gas fee funding) can be misappropriated or lost due to reliance on a manipulable AMM-derived quote validated before execution. This is a fee-delegation/gasless-settlement integrity issue reachable by any ordinary transaction sender.

### Likelihood Explanation
Reachable by any unprivileged account: an attacker only needs to submit ordinary transactions (e.g., large swaps against the same pool the `GaslessSwapRouter` uses) positioned ahead of a target gasless bundle within the same block, or manipulate the pool state right before their own gasless swap is picked up for bundling. No special privileges, validator collusion, or off-chain elevated access are required — the check is a plain view-call sufficiency check performed against mutable AMM state and is by design not re-validated with a slippage/price protection scheme tied to actual execution price.

### Recommendation
- Enforce repayment sufficiency using on-chain execution-time output (i.e., have `swapForGas` itself revert if realized swap output < `amountRepay` after the swap executes, and ensure the tx-pool/bundling layer treats a revert as fatal to the bundle so LendTx and ApproveTx can be safely dropped or reverted together).
- Consider re-validating `GetCheckBalance` against the state immediately prior to bundle execution (i.e., right before block assembly finalizes the bundle position) rather than only at admission/promotion, and/or use TWAP-based or otherwise manipulation-resistant pricing for `getAmountIn`.
- Ensure the bundle mechanics guarantee atomicity: if `SwapTx` reverts (e.g., due to insufficient output), the `LendTxGenerator`-advanced funds must not be permanently lost by the proposer; add safeguards (e.g., minimum output enforcement mirrored on-chain, not just at pool admission).

### Proof of Concept
1. Attacker (or any DEX participant) executes a large swap on the token pair used by `GaslessSwapRouter`/DEX pool, shifting reserves so that `getAmountIn(token, minAmountOut)` returns a lower "required" value than what will actually be needed once state updates land in the same block.
2. A user's `GaslessApproveTx`/`GaslessSwapTx` pair is validated at admission via `checkBalanceForSwap`, which calls `routerContract.GetAmountIn(nil, token, minAmountOut)` against the state at that time and passes [1](#0-0) .
3. The gasless bundle `[LendTxGenerator, ApproveTx, SwapTx]` is built and included in the block, with the proposer already advancing gas funds via `LendTxGenerator` [4](#0-3) .
4. By execution time in the same block, reserves have moved (e.g., due to the attacker's earlier manipulation tx or natural ordering), so the swap executes at a worse rate than assumed during admission, causing insufficient repayment to the proposer relative to `repayAmount` [5](#0-4) , or causing the swap tx to revert after gas was already advanced.

Note: I was not able to fully confirm from the index whether `checkBalanceForSwap`/`GetCheckBalance` is re-invoked immediately before final bundle inclusion within the same block (versus only at initial pool admission/promotion), nor whether `GaslessSwapRouter.swapForGas`'s Solidity implementation enforces a strict on-chain revert against actual realized output vs. `amountRepay` at execution time — this would need direct inspection of `contracts/*/GaslessSwapRouter.sol` source (only the generated Go bindings were indexed) and the exact tx-pool re-validation lifecycle in `blockchain/tx_pool.go`/`blockchain/tx_list.go`, which the current index did not surface in full. Confirming these two points would either strengthen or falsify the severity of this finding; if `swapForGas` already enforces `amountRepay` on-chain against actual output on every execution (a `require` after the swap call), then the admission-time check is merely an optimization and the exploit reduces to denial-of-service (reverted bundle, wasted lend) rather than value theft. I'd recommend a follow-up Devin session with full repo access to inspect the actual Solidity source of `GaslessSwapRouter.sol` and the `blockchain/tx_pool.go` re-validation flow before treating this as a confirmed Medium/High finding.

### Citations

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
