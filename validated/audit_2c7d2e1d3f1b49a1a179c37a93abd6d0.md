### Title
Gasless swap (KIP-247) relies on manipulable Uniswap V2 spot reserves for pricing, exposing users to sandwich/front-running value extraction - (File: kaiax/gasless/impl/tx_pool.go, contracts/bindings/kip247/GaslessSwapRouter.go)

### Summary
The gasless-transaction feature (KIP-247) lets a user submit a `SwapForGas` transaction that swaps ERC-20 tokens for KAIA through a Uniswap V2 style AMM (`GaslessSwapRouter` / underlying `UniswapV2Router02`) to repay a block proposer that pre-funded the user's gas. Both the mempool admission check and the on-chain swap price a fixed `token → KAIA` conversion purely off the AMM's live spot reserves, with slippage protection (`minAmountOut`) chosen by the swap sender rather than enforced by the protocol against a fair/oracle price. This is the same root-cause pattern as the referenced DODO finding (reliance on `UniswapV2Library`-style spot-reserve quoting without manipulation-resistant pricing), and it is reachable by any unprivileged public transaction sender who can move the pool's reserves immediately around a victim's gasless swap.

### Finding Description
`GaslessModule.checkBalanceForSwap` in [1](#0-0)  validates a submitted `SwapArgs` by calling `routerContract.GetAmountIn(nil, token, minAmountOut)` — a live, on-chain view call into `GaslessSwapRouter.getAmountIn`, which itself resolves to the pool's *current* reserves (the same `getAmountIn`/`getAmountsIn` spot-reserve math implemented by `UniswapV2Router02`, see the bound accessor at [2](#0-1) ). The only price-protection the protocol enforces is `minAmountOut >= amountRepay` ( [3](#0-2) ) and `amountIn >= requiredAmountIn` computed from that same spot quote — both of which are figures chosen/declared by the transaction sender themselves, not derived from a manipulation-resistant reference price.

The on-chain `swapForGas(token, amountIn, minAmountOut, amountRepay, deadline)` entry point ( [4](#0-3) ) then executes the swap against the AMM at whatever reserves exist at execution time. Because the swap is bundled with a `LendTxGenerator` and optional `ApproveTx` into the same block (per the module's block-building rules, [5](#0-4) ), an unprivileged attacker who observes the pending gasless swap in the public transaction pool can:
1. Submit a higher-gas-price transaction that swaps against the same pool to skew reserves immediately before the victim's gasless swap is included.
2. Let the victim's `swapForGas` execute at the skewed rate — the transaction still succeeds as long as `minAmountOut` (chosen with only a thin margin, matching the pattern shown in the repo's own test at [6](#0-5)  where `margin := swapExpectedOutput / 100`, i.e. only ~1%) is still met.
3. Submit a reverse transaction after the victim's swap to restore reserves and capture the price difference as attacker profit, all within the normal gas-price-ordering of the block — no validator/proposer collusion required.

This is architecturally identical to the DODO cross-chain report: a contract computing a required swap amount from `UniswapV2`-style spot reserves and using that unmodified quote to execute value transfer, with the only guard being a caller-supplied slippage bound rather than a protocol-enforced fair-price check.

### Impact Explanation
A user submitting a gasless swap can have a material portion of their expected `finalUserAmount` (see `SwappedForGas` event fields `FinalUserAmount`/`Commission` in [7](#0-6) ) siphoned by a sandwiching attacker, since the protocol never checks the swap's realized rate against anything other than the sender's own (thin-margin) `minAmountOut`. In the worst case, an attacker can push the realized output down to just above `minAmountOut`/`amountRepay`, extracting nearly all of the user's expected surplus while the transaction still "succeeds," making this a genuine unauthorized value transfer rather than a mere griefing/DoS.

### Likelihood Explanation
Reachable purely from a public RPC call by any unprivileged sender: no special permissions, no validator collusion, and no protocol bug beyond relying on live AMM reserves for both the mempool admission check and the actual swap execution. It requires liquidity thin enough to move within one gas-priority window and a user-selected `minAmountOut` margin narrow enough to still allow sandwiching profit (the shipped test uses a 1% margin), which is a realistic and even encouraged usage pattern since a larger margin only benefits the attacker further while a tighter margin increases DoS likelihood.

### Recommendation
Do not rely solely on caller-declared `minAmountOut` derived from the AMM's instantaneous spot reserves. Consider enforcing a protocol-level maximum acceptable deviation from a manipulation-resistant reference price (e.g., TWAP or oracle-checked bound) inside `GaslessSwapRouter.swapForGas`/`getAmountIn`, and/or restrict gasless swaps to pools with proposer-verified minimum liquidity depth so that reserve manipulation within a single block-inclusion window is economically infeasible.

### Proof of Concept
Not executed against a live network; the vulnerable dependency chain is demonstrated by:
- `checkBalanceForSwap`'s use of a live spot-reserve view call (`routerContract.GetAmountIn`) at [8](#0-7) .
- The gasless test's own slippage margin of 1% of expected output at [9](#0-8) , illustrating the thin, unenforced protection window an attacker can exceed via ordinary front-running.

### Citations

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

**File:** contracts/bindings/uniswap/router/UniswapV2Router02.go (L3975-3980)
```go
// GetAmountIn is a free data retrieval call binding the contract method 0x85f8c259.
//
// Solidity: function getAmountIn(uint256 amountOut, uint256 reserveIn, uint256 reserveOut) pure returns(uint256 amountIn)
func (_IUniswapV2Router02 *IUniswapV2Router02CallerSession) GetAmountIn(amountOut *big.Int, reserveIn *big.Int, reserveOut *big.Int) (*big.Int, error) {
	return _IUniswapV2Router02.Contract.GetAmountIn(&_IUniswapV2Router02.CallOpts, amountOut, reserveIn, reserveOut)
}
```

**File:** contracts/bindings/kip247/GaslessSwapRouter.go (L554-566)
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

**File:** kaiax/gasless/README.md (L29-36)
```markdown
### Block building rules

Upon detection of GaslessTxs, the following logics are executed:

- Per sender, if exists, GaslessApproveTx is relocated before GaslessSwapTx.
- LendTxGenerator is prepended before GaslessApproveTx.
- A new bundle is generated which contain either `[LendTxGenerator, GaslessApproveTx, GaslessSwapTx]` or `[LendTxGenerator, GaslessSwapTx]`
- If the bundle has conflict with any previous bundles, it is excluded from the returned bundle list.
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
