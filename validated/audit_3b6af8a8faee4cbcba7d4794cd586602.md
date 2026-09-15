### Title
Gasless swap tx-pool admission uses spot AMM price (`GetAmountIn`) instead of a manipulation-resistant oracle, enabling stale/sandwichable slippage checks - (File: kaiax/gasless/impl/tx_pool.go)

### Summary
The externally reported bug describes `CrvDepositorWrapper` relying on an infrequently-updated TWAP oracle from a low-liquidity pool, combined with a fixed slippage tolerance, causing frequent revert or sandwich exposure. The Kaia analog is in the gasless swap admission logic: `checkBalanceForSwap` in `kaiax/gasless/impl/tx_pool.go` validates a user-declared `minAmountOut` against a **live spot price** read from the `GaslessSwapRouter` contract (`GetAmountIn`), not a time-weighted or otherwise manipulation-resistant price. This check happens at tx-pool admission time, while actual on-chain settlement happens later (block building/execution), leaving a time-of-check/time-of-use gap that can be exploited by a searcher/proposer to sandwich the swap or by an adversarial AMM state to bypass the intended repayment guarantee.

### Finding Description
`GetCheckBalance()` returns a validator that calls `checkBalanceForSwap` for every `GaslessSwapTx` admitted to or promoted within the tx pool: [1](#0-0) 

Inside `checkBalanceForSwap`, when `ShouldCheckSwapAmount()` is enabled, the module queries the router for the amount of input token required to guarantee `minAmountOut`, using the router's **current on-chain reserves/spot price** via `GetAmountIn`: [2](#0-1) 

This is analogous to the reported issue: the check is a single point-in-time price read (spot price, not TWAP/Chainlink), performed by an unprivileged flow (any user submitting a `swapForGas` transaction that will be picked up by the tx pool and later included/lent gas by the proposer). Because:
1. The price query happens once, at admission/promotion time, based on whatever the pool's `getReserves`-style state is at that instant.
2. The tx may sit in the pool (up to `PendingTimeout`/`QueueTimeout` = 10 seconds) before being bundled by the proposer via `LendTxGenerator, GaslessApproveTx, GaslessSwapTx`.
3. Actual execution occurs in a later block where the AMM pool state can have moved (via ordinary trading, or an adjacent transaction/bundle in the same block placed before the swap).

There is a time-of-check-time-of-use gap between the tx-pool admission price check and actual settlement, exactly mirroring the C4 report's core problem: relying on a spot/rarely-updated price feed for a slippage-sensitive operation where a fixed tolerance (`minAmountOut`) is meant to bound the sender's cost, but the entity building the block (the proposer, who also fronts the gas via `LendTxGenerator`) or a following bundle can move the price between check and settlement.

### Impact Explanation
If the swap actually settles for less output than expected due to price movement between the tx-pool check and execution, the transaction still succeeds because `swapForGas`'s own `minAmountOut` bound is enforced on-chain — but the gas the proposer lent (`amountRepay`) may no longer be recoverable in full if the output happens to fall between the tx-pool's optimistic `requiredAmountIn` check and the actual settlement price, since the pool-side estimate used for **admission** is not the same guarantee enforced by the contract. More importantly, since the proposer is also the one assembling the block and lending gas via `GetLendTxGenerator`, a malicious proposer could manipulate the router's reserves within the same block (front-running/sandwiching the gasless swap with their own trade) to push the actual settlement price down to the edge of `minAmountOut`, extracting value from the swap output that would otherwise go to repaying gas/commission, while the tx-pool-level `checkBalanceForSwap` had validated against a now-stale higher price. This is a fee/value-extraction vector reachable by a single block proposer or colluding searcher issuing an ordinary transaction, fitting the "fee delegation/gasless settlement abuse" and "reward redirection" categories in scope.

### Likelihood Explanation
Medium. It requires the block proposer (or a searcher bundling around the proposer) to intentionally trade against the same `GaslessSwapRouter` pool within the same block to shift the price before the gasless swap executes — a capability every proposer/validator naturally has when assembling blocks, and does not require any privileged access beyond normal transaction submission. The `ShouldCheckSwapAmount` check only bounds the **initial admission**, not runtime price movement, so the actual settlement is exposed to the same spot-price manipulability the original report describes for `CrvDepositorWrapper`.

### Recommendation
- Do not rely solely on the router's instantaneous `GetAmountIn` (spot reserve ratio) for admission decisions; if a manipulation-resistant reference price (e.g., a TWAP over multiple blocks, or an external price feed) is available, use it to sanity-check `minAmountOut`/`amountIn` at admission time.
- Re-validate `minAmountOut` immediately prior to bundling/execution (as close as possible to actual settlement) rather than only at initial tx-pool admission, to shrink the time-of-check/time-of-use gap.
- Since the proposer already controls bundle ordering (`LendTxGenerator, GaslessApproveTx, GaslessSwapTx`), consider constraining the proposer's ability to insert intervening swaps against the same router/pool within the same block as a gasless swap bundle, or require the on-chain `swapForGas` `minAmountOut` bound to also account for a maximum single-block price impact.

### Proof of Concept
1. A user submits a `GaslessApproveTx` + `GaslessSwapTx` pair with `SwapArgs{Token, AmountIn, MinAmountOut, AmountRepay, Deadline}` as validated by `decodeSwapTx`/`checkBalanceForSwap`. [3](#0-2) 
2. At admission, `checkBalanceForSwap` queries the router's `GetAmountIn(token, minAmountOut)` against the pool's current spot reserves and passes because `AmountIn >= requiredAmountIn` at that moment. [2](#0-1) 
3. The proposer, who also supplies the lent gas via `GetLendTxGenerator` and assembles the final bundle `[LendTxGenerator, GaslessApproveTx, GaslessSwapTx]`, inserts its own trade against the same `GaslessSwapRouter`-connected AMM pool immediately before the bundle in the same block, shifting reserves and worsening the effective exchange rate.
4. The gasless swap still executes and passes the on-chain `minAmountOut` bound (since that's a hard floor), but the actual amount available beyond `amountRepay` (used for commission/refund to user) is reduced compared to what was estimated at tx-pool admission — extracting value that the spot-price admission check could not have detected because it was evaluated before the price was moved.

Note: The actual Solidity source for `GaslessSwapRouter.getAmountIn`/reserve mechanics was not available in the indexed codebase (only the compiled ABI/bytecode bindings in `contracts/bindings/kip247/GaslessSwapRouter.go` were found), so the exact AMM formula (e.g., constant-product vs. TWAP) could not be directly confirmed from source; this assessment is based on the Go-side caller semantics (`GetAmountIn(nil, token, minAmountOut)` called with `nil` `CallOpts`, implying a live/latest-state call) and the module's documented behavior. A Devin session with full repository access would be needed to inspect `GaslessSwapRouter.sol` directly to confirm whether any TWAP/oracle protection already exists in the contract itself.

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

**File:** kaiax/gasless/impl/tx_pool.go (L128-142)
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
	}
```

**File:** kaiax/gasless/impl/getter.go (L140-180)
```go
}

func decodeSwapTx(tx *types.Transaction, signer types.Signer) (args *SwapArgs, ok bool) {
	to, inputs, ok := decodeFunctionCall(tx, routerSwapFunc)
	if !ok {
		return nil, false
	}
	token, ok := inputs["token"].(common.Address)
	if !ok {
		return nil, false
	}
	amountIn, ok := inputs["amountIn"].(*big.Int)
	if !ok {
		return nil, false
	}
	minAmountOut, ok := inputs["minAmountOut"].(*big.Int)
	if !ok {
		return nil, false
	}
	amountRepay, ok := inputs["amountRepay"].(*big.Int)
	if !ok {
		return nil, false
	}
	deadline, ok := inputs["deadline"].(*big.Int)
	if !ok {
		return nil, false
	}
	from, err := types.Sender(signer, tx)
	if err != nil {
		return nil, false
	}
	return &SwapArgs{
		Sender:       from,
		Router:       to,
		Token:        token,
		AmountIn:     amountIn,
		MinAmountOut: minAmountOut,
		AmountRepay:  amountRepay,
		Deadline:     deadline,
	}, true
}
```
