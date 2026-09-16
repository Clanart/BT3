### Title
Gasless swap fee-delegation repayment can be manipulated via AMM pool price/reserve manipulation - ([File: kaiax/gasless/impl/tx_pool.go])

### Summary
The KIP-247 Gasless module admits a `GaslessSwapTx` into the pool/block by checking its slippage/repayment requirements against the *live spot* reserves of the underlying AMM (Uniswap-V2-style) pool used by `GaslessSwapRouter`, in the same manner that the referenced report flagged `YAxisVotePower.balanceOf`'s reliance on unprotected Uniswap `getReserves()` values. Because the pool price/reserves used to authorize the swap and its repayment amount are read from the manipulable spot state rather than a TWAP or execution-time-atomic guarantee, an attacker (the gasless transaction's own sender, an unprivileged public-RPC caller) can shift the pool price between admission-time validation and bundle execution to break the fee-delegation guarantee that the block proposer is repaid for gas it fronted.

### Finding Description
`GaslessModule.checkBalanceForSwap` validates a `GaslessSwapTx` before it is promoted into the tx pool / block by calling the swap router's `GetAmountIn` view function, which is priced directly off the AMM pool's current reserves: [1](#0-0) 

This check is only an admission-time filter using the *current* on-chain state (via `bind.CallOpts` with `nil`, i.e., latest/pending state), exactly as `YAxisVotePower.balanceOf` used the current, unprotected `getReserves()` value of the Uniswap pair. The actual gasless bundle - `[LendTxGenerator, GaslessApproveTx, GaslessSwapTx]` - is only executed later, when the block is built: [2](#0-1) 

The economic design assumes the proposer "lends" the user gas fee up front (via `LendTxGenerator`), and the user "repays" that lent amount out of the swap output during `GaslessSwapTx` execution: [3](#0-2) 

Because the `AmountRepay` and `MinAmountOut` fields of the swap are user-signed constants checked against a router quote (`GetAmountIn`) that reflects only the pool's current reserves at validation time, and because there is a time/ordering gap between that validation and actual bundle execution (subject to other transactions, including the attacker's own manipulative trades, being included first in the same or an intervening block), the attacker can:
1. Manipulate the DEX pool reserves (e.g., via one or more swaps ahead of their gasless bundle) so that `checkBalanceForSwap`/`GetAmountIn` reports a rate that lets an undersized `AmountIn`/oversized `AmountRepay` pass validation and be promoted into the block.
2. Revert the price back (or simply let the true price prevail) at actual execution time inside the same block, causing the final on-chain swap to yield less than the `AmountRepay` the proposer/protocol expects to reclaim.

This mirrors the report's root cause precisely: a security-critical financial decision (voting power there; fee-delegation repayment sizing here) is derived from an AMM's raw, flash-manipulable spot reserves instead of a TWAP or an execution-time-atomic check.

### Impact Explanation
If the repayment computed from the manipulated spot price is insufficient to cover the KAIA fronted by the block proposer through `LendTxGenerator`, the proposer either: (a) has the `GaslessSwapTx` revert at execution (after the `LendTxGenerator` transaction has already unconditionally credited the user), causing the lent gas fee to be unrecoverable, or (b) has the swap under-repay the proposer if repayment amounts are enforced too loosely relative to actual swap output. Either outcome is a fee-delegation/gasless-settlement value loss borne by the block proposer/protocol and directly benefits the attacker (the gasless user), matching the class of "unauthorized value movement... fee-delegation abuse, gasless... settlement theft" explicitly listed as in-scope impact.

### Likelihood Explanation
The `GaslessSwapTx` sender is by definition an unprivileged, permissionless account reachable through ordinary transaction submission (mempool). No validator/node compromise, p2p manipulation, or admin privilege is required — the attacker only needs to be able to place a manipulative swap transaction ahead of (or interleaved with) their own gasless bundle, which any account can attempt via normal gas-price bidding or same-block ordering. As with the original finding, exploitation does not require an actual flash loan; an attacker can simply buy/sell their own position around the vulnerable check within a short window, and the severity here — as judged in the original report — remains Medium because the manipulation window is bounded to same-block/near-block timing rather than being freely repeatable at will.

### Recommendation
Do not size `AmountRepay`/`MinAmountOut` validation off the AMM's live spot reserves via a single-block view call. Use a TWAP-based or otherwise manipulation-resistant price oracle for `GetAmountIn`, or make the repayment computation and validation atomic with the swap's own execution (i.e., compute and enforce the repayment amount inside `swapForGas`'s own execution against the actual swap output, and have `checkBalanceForSwap` only be a heuristic optimistic filter, never authorizing final settlement). Ensure `LendTxGenerator`'s gas advance and the user's repayment obligation are enforced within the same atomic bundle such that a failed/undersized repayment causes the entire bundle (including the lend) to fail or be reverted together.

### Proof of Concept
1. Attacker holds tokens in the whitelisted gasless-swap token/pool and observes an upcoming gasless swap they plan to submit with `SwapArgs{AmountIn, MinAmountOut, AmountRepay}` sized against the router's current quote: [1](#0-0) 
2. Immediately before or in the same block as submitting the `GaslessApproveTx`/`GaslessSwapTx` bundle, attacker executes a large swap against the underlying Uniswap-V2-style pool used by `GaslessSwapRouter` to shift reserves and thus the `GetAmountIn` quote in their favor.
3. `checkBalanceForSwap` validates and promotes the bundle using the manipulated quote, and the block builder prepends `LendTxGenerator` so the proposer fronts gas to the attacker's account: [4](#0-3) 
4. Attacker reverses their manipulative trade (or lets natural price recovery occur) before/at the point the `GaslessSwapTx` executes, so the actual on-chain swap output is insufficient to fully cover `AmountRepay`, leaving the proposer's advanced gas fee unrecovered (or the swap reverts, still leaving the proposer's `LendTxGenerator` payment sunk since it is a separate transaction).

Note: I was not able to inspect the on-chain Solidity source of `GaslessSwapRouter.sol` (only the compiled bytecode/ABI bindings are indexed) to confirm exactly how `swapForGas` enforces `AmountRepay` against actual swap output at execution time, nor could I inspect `work/builder/bundle.go`'s atomicity guarantees for `[LendTxGenerator, ApproveTx, SwapTx]` bundles (i.e., whether a reverting `SwapTx` also unwinds the `LendTxGenerator` payment) before the iteration budget was exhausted. Confirming those two points — bundle-level atomicity and the router's execution-time repayment enforcement — would be necessary to fully validate the impact severity and would benefit from a full Devin session with source access to `contracts/kip247/GaslessSwapRouter.sol` and `work/builder/bundle.go`.

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

**File:** kaiax/gasless/README.md (L7-11)
```markdown
Gasless transaction (GaslessTx) consists of two types: gasless approve transaction (GaslessApproveTX), and gasless swap transaction (GaslessSwapTx).

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
