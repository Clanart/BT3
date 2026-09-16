### Title
Gasless `swapForGas` only enforces `minAmountOut >= amountRepay`, exposing users to sandwich attacks that drain their swap output - (File: kaiax/gasless/impl/tx_pool.go)

### Summary
The `GaslessSwapRouter.swapForGas(token, amountIn, minAmountOut, amountRepay, deadline)` flow lets a gasless user swap an ERC-20 token for the native gas token to pay for their own gas, with the excess going back to the user. Both the mempool-admission check and (per the ABI/tests) the on-chain contract only validate `minAmountOut >= amountRepay` — there is no requirement that `minAmountOut` reflect a real slippage tolerance around the actual expected swap output. This mirrors the reported DODO `sellShares()` issue: the "minimum acceptable amount" parameter is not required to be meaningfully close to the fair value, so a user (or naive wallet/SDK default) can submit `minAmountOut == amountRepay`, and an attacker who observes the pending SwapTx can sandwich it, pushing the realized swap output down to just above `amountRepay` and capturing the difference, leaving the user with `FinalUserAmount` ≈ 0 despite spending `amountIn` of their token.

### Finding Description
`checkBalanceForSwap` in `kaiax/gasless/impl/tx_pool.go` enforces: [1](#0-0) 

This is the *only* floor placed on `minAmountOut` — it must merely cover the gas repayment (`amountRepay`, computed from gas price × gas limits, see `repayAmount` in `kaiax/gasless/impl/getter.go`), not the actual market value of `amountIn` tokens. The optional `ShouldCheckSwapAmount` path further only checks that `amountIn` is sufficient to obtain `minAmountOut` under the *current* on-chain exchange rate at validation time: [2](#0-1) 

None of these checks require `minAmountOut` to be close to the expected AMM output (`amountsOut`) for the trade — a user's `minAmountOut` can legitimately equal `amountRepay` and still pass validation, exactly as the DODO report describes a `baseMinAmount`/`quoteMinAmount` that is technically non-zero but insufficient to protect against price movement. The integration test explicitly demonstrates the router rejects only `minAmountOut < amountRepay`, not `minAmountOut` values that provide no real slippage protection: [3](#0-2) [4](#0-3) 

Since `swapForGas` executes an actual AMM swap (via the wrapped Uniswap-style router) whose output is entirely dictated by pool reserves at execution time, an attacker who observes the pending `SwapTx` (submitted by any unprivileged sender through public RPC as part of the gasless flow) can front-run it with a large trade that moves the pool price, then back-run it to restore price, executing a classic sandwich. Because the user's `minAmountOut` floor is only `amountRepay`, the contract's on-chain slippage check (`require(output >= minAmountOut)`) does not stop the manipulated, unfavorable execution — it only guarantees the gas debt is repaid, not that the user receives fair value for their tokens.

### Impact Explanation
A successful sandwich reduces `FinalUserAmount` (the residual gas token returned to the user after repaying `amountRepay`) toward zero while the user's ERC-20 `amountIn` is fully consumed by the swap, per `SwappedForGas` event semantics: [5](#0-4) 
This is a genuine value-extraction/loss-of-funds scenario for the gasless user, reachable purely by an unprivileged transaction sender abusing normal transaction-ordering/priority in a single block or bundle — matching the "gasless user" and "fee-delegation counterparty" categories explicitly permitted by the rules. The loss scales with the size of `amountIn` relative to pool liquidity and is entirely dependent on the (correctly-formed but insufficiently protective) `minAmountOut` chosen by the user/SDK.

### Likelihood Explanation
Likelihood is moderate-to-high: any wallet/SDK that defaults `minAmountOut` to the bare minimum required by protocol validation (`amountRepay`) — which is the only value the protocol currently mandates — will produce transactions that are trivially sandwichable. The attack requires no special privileges beyond normal transaction submission and does not require validator/proposer collusion, only observing the pending gasless SwapTx and outbidding it within the same block window, which is achievable by any public-RPC-reachable actor.

### Recommendation
Strengthen validation in `checkBalanceForSwap` (and the on-chain `GaslessSwapRouter.swapForGas`) to require `minAmountOut` to be meaningfully bound to the expected swap output (e.g., require `minAmountOut >= amountRepay AND minAmountOut >= f(expectedOutput, maxSlippageBps)`), or require gasless SDKs/wallets to compute `minAmountOut` from a quoted `amountsOut` with an explicit slippage tolerance rather than defaulting to `amountRepay`. Consider also exposing/enforcing a protocol-level maximum allowed slippage for `swapForGas` to prevent users from being sandwiched down to the repayment floor.

### Proof of Concept
1. Gasless user Alice submits `swapForGas(token, amountIn, minAmountOut = amountRepay, amountRepay, deadline)` — this passes all current validations in `checkBalanceForSwap` (`kaiax/gasless/impl/tx_pool.go:107-182`), since it only requires `minAmountOut >= amountRepay`.
2. Attacker observes Alice's pending SwapTx via mempool/public RPC and submits a large swap against the same pool ahead of Alice's transaction to move the price unfavorably for Alice.
3. Alice's SwapTx executes at the manipulated price; the actual AMM output is only slightly above `amountRepay` (contract's `require(output >= minAmountOut)` still passes because `minAmountOut` was set to the bare `amountRepay` floor).
4. `amountRepay` is deducted to cover Alice's gas cost, leaving `FinalUserAmount` near zero — Alice loses essentially all of the value of the `amountIn` tokens she supplied beyond gas repayment.
5. Attacker reverses their trade, capturing the price-impact profit extracted from Alice's swap.

Note: I could not directly inspect the raw Solidity source of `GaslessSwapRouter.sol` (only the compiled Go bindings and bytecode metadata were indexed), so I could not verify whether any additional on-chain slippage/anti-sandwich logic exists beyond what the ABI and integration tests reveal. If a stronger on-chain check exists that isn't reflected in `kaiax/gasless/impl/tx_pool.go` or `tests/gasless_test.go`, it would reduce or eliminate this finding — starting a full Devin session with repository access would allow confirming the exact on-chain implementation.

### Citations

**File:** kaiax/gasless/impl/tx_pool.go (L102-120)
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

**File:** tests/gasless_test.go (L151-163)
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
```

**File:** tests/gasless_test.go (L247-249)
```go
	// reject swapTx when minAmountOut < amountRepay
	_, err = sendSwapTx(t, gsrContract, accounts[0], testTokenAddr, swapAmmount, common.Big0, amountRepaySwap, deadline)
	assert.ErrorContains(t, err, "insufficient minAmountOut")
```

**File:** contracts/bindings/kip247/GaslessSwapRouter.go (L1127-1135)
```go
// GaslessSwapRouterSwappedForGas represents a SwappedForGas event raised by the GaslessSwapRouter contract.
type GaslessSwapRouterSwappedForGas struct {
	Proposer        common.Address
	AmountRepaid    *big.Int
	User            common.Address
	FinalUserAmount *big.Int
	Commission      *big.Int
	Raw             types.Log // Blockchain specific contextual infos
}
```
