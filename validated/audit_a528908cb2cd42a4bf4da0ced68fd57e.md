### Title
Gasless `SwapTx` admission relies on manipulable spot-price `getAmountIn` with no TWAP protection - ([File: kaiax/gasless/impl/tx_pool.go])

### Summary
The Kaia gasless module gates `SwapTx` mempool admission using `GaslessSwapRouter.GetAmountIn(token, minAmountOut)`, which (per the Uniswap-V2-style bindings in the repo) is a pure spot-price computation from a pair's *instantaneous* `getReserves()` — i.e., a zero-period price with no time-averaging whatsoever. This is the same bug class as the external report (oracle price computed over too short/no time window, making it trivially manipulable within a single block/transaction), but stronger, since there is no averaging period at all rather than a shortened one.

### Finding Description
`checkBalanceForSwap` in [1](#0-0)  computes the `requiredAmountIn` for a submitted gasless `SwapTx` by calling `GaslessSwapRouter.GetAmountIn(token, minAmountOut)` through `kip247.NewGaslessSwapRouterCaller`. The bindings show this call maps to `getAmountIn(uint256 amountOut, uint256 reserveIn, uint256 reserveOut)`-style Uniswap V2 math derived from the pool's current `getReserves()` [2](#0-1)  and [3](#0-2) , i.e. a spot price with zero time-weighting — the exact bug class the external report flags (TWAP window too short/absent), except here the window is effectively zero instead of merely 600s vs 1800s.

Because this price is read live from the underlying DEX pool state at the moment the check runs (both at tx-pool admission time via `GetCheckBalance`/`checkBalanceForSwap`, and presumably again on-chain inside `swapForGas`), an attacker who controls the DEX pool's reserves in the same block (e.g., via a preceding swap in the same block, a flash swap, or by being the block proposer) can shift the spot price to make `getAmountIn` artificially low, satisfying `swapArgs.AmountIn.Cmp(requiredAmountIn) < 0` with much less collateral than a fair, time-averaged price would require. This lets a malicious gasless-swap sender post the gasless swap with fewer real tokens deposited than the true economic value of `minAmountOut`/`amountRepay`, while the transaction is still admitted per the check at [4](#0-3) .

### Impact Explanation
The gasless flow pre-funds the user's gas fee (`amountRepay` is lent in native KAIA, to be repaid from the swap's output per `minAmountOut >= amountRepay`, checked at [4](#0-3) ). If the price used to gate admission (and, if identically implemented on-chain, to price the swap) can be manipulated within the same block, the fee-delegation/gasless subsidy party (the node lending the gas) can end up under-collateralized relative to the token's true value, or the swap can settle at a manipulated rate that benefits the attacker at the expense of the counterparty repaying the loan. This is a fee-delegation/gasless settlement value-transfer risk reachable by any public/unprivileged sender of a `SwapTx`.

### Likelihood Explanation
Medium: this requires the sender to also control or have access to manipulate the target token's on-chain DEX liquidity pool within the same block (e.g., via a preceding trade in the same transaction bundle, a flash swap, or being/colluding with the block proposer to reorder transactions). This is a realistic capability for an unprivileged EOA that authors both the manipulating swap and the gasless `SwapTx`/`ApproveTx` pair, since Kaia's gasless bundle logic explicitly allows sequential `ApproveTx`+`SwapTx` bundles from the same sender (`isSwapTxReady`, `isApproveTxReady` at [5](#0-4) ), and nothing here prevents an additional attacker-controlled swap being included in the same block before the gasless `SwapTx` to shift the pool's reserves.

### Recommendation
Do not rely on the DEX pool's instantaneous `getReserves()`/spot price for gating gasless swap admission or for pricing the swap settlement. Use a time-weighted average price (TWAP) over a sufficiently long window (e.g., using Uniswap V3-style `observe`/oracle libraries with ≥ 30 minutes, or a similarly robust on-chain oracle) inside `GaslessSwapRouter.getAmountIn`, or otherwise bound the allowed price deviation between the recently observed TWAP and the current spot price before admitting/settling a gasless `SwapTx`.

### Proof of Concept
Not independently reproducible from the indexed content: the Solidity source for `GaslessSwapRouter.sol` (specifically its `getAmountIn`/`swapForGas` implementation) was not available in the indexed codebase — only its Go bindings in `contracts/bindings/kip247/GaslessSwapRouter.go` were found. Based on the binding signatures and the way `checkBalanceForSwap` consumes `GetAmountIn`, the pricing logic mirrors standard Uniswap-V2 spot-price math (`getAmountIn(amountOut, reserveIn, reserveOut)` from live `getReserves()`), which is inherently a zero-period, fully manipulable price source. Confirming whether `swapForGas` itself re-derives the same spot price on execution (making the exploit end-to-end) requires inspecting the actual `GaslessSwapRouter.sol` contract source, which could not be located in this index — a full Devin session with repository access would be needed to confirm the exact on-chain settlement logic and build a concrete PoC transaction sequence.

### Citations

**File:** kaiax/gasless/impl/tx_pool.go (L115-120)
```go
	// tx.minAmountOut >= tx.amountRepay
	minAmountOut := swapArgs.MinAmountOut
	amountRepay := swapArgs.AmountRepay
	if minAmountOut.Cmp(amountRepay) < 0 {
		return fmt.Errorf("insufficient minAmountOut: minAmountOut=%s, amountRepay=%s", minAmountOut.String(), amountRepay.String())
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

**File:** kaiax/gasless/impl/tx_pool.go (L251-290)
```go
// isApproveTxReady assumes that the caller checked `g.IsApproveTx(approveTx)`
func (g *GaslessModule) isApproveTxReady(approveTx, nextTx *types.Transaction) bool {
	addr, err := types.Sender(g.signer, approveTx)
	if err != nil {
		return false
	}
	nonce := g.getCurrentStateNonce(addr)

	if approveTx.Nonce() != nonce {
		return false
	}
	if nextTx == nil || !g.IsSwapTx(nextTx) {
		return false
	}

	return g.IsExecutable(approveTx, nextTx)
}

// isSwapTxReady assumes that the caller checked `g.IsSwapTx(swapTx)`
func (g *GaslessModule) isSwapTxReady(swapTx, prevTx *types.Transaction) bool {
	addr, err := types.Sender(g.signer, swapTx)
	if err != nil {
		return false
	}
	nonce := g.getCurrentStateNonce(addr)

	var approveTx *types.Transaction
	if swapTx.Nonce() == nonce {
		approveTx = nil
	} else if swapTx.Nonce() == nonce+1 {
		if prevTx == nil || !g.IsApproveTx(prevTx) {
			return false
		}
		approveTx = prevTx
	} else {
		return false
	}

	return g.IsExecutable(approveTx, swapTx)
}
```

**File:** contracts/bindings/uniswap/router/UniswapV2Router02.go (L5473-5493)
```go
// GetAmountIn is a free data retrieval call binding the contract method 0x85f8c259.
//
// Solidity: function getAmountIn(uint256 amountOut, uint256 reserveIn, uint256 reserveOut) pure returns(uint256 amountIn)
func (_UniswapV2Router02 *UniswapV2Router02Caller) GetAmountIn(opts *bind.CallOpts, amountOut *big.Int, reserveIn *big.Int, reserveOut *big.Int) (*big.Int, error) {
	var out []interface{}
	err := _UniswapV2Router02.contract.Call(opts, &out, "getAmountIn", amountOut, reserveIn, reserveOut)
	if err != nil {
		return *new(*big.Int), err
	}

	out0 := *abi.ConvertType(out[0], new(*big.Int)).(**big.Int)

	return out0, err
}

// GetAmountIn is a free data retrieval call binding the contract method 0x85f8c259.
//
// Solidity: function getAmountIn(uint256 amountOut, uint256 reserveIn, uint256 reserveOut) pure returns(uint256 amountIn)
func (_UniswapV2Router02 *UniswapV2Router02Session) GetAmountIn(amountOut *big.Int, reserveIn *big.Int, reserveOut *big.Int) (*big.Int, error) {
	return _UniswapV2Router02.Contract.GetAmountIn(&_UniswapV2Router02.CallOpts, amountOut, reserveIn, reserveOut)
}
```

**File:** contracts/bindings/uniswap/factory/UniswapV2Factory.go (L2665-2691)
```go
// GetReserves is a free data retrieval call binding the contract method 0x0902f1ac.
//
// Solidity: function getReserves() view returns(uint112 reserve0, uint112 reserve1, uint32 blockTimestampLast)
func (_IUniswapV2Pair *IUniswapV2PairCaller) GetReserves(opts *bind.CallOpts) (struct {
	Reserve0           *big.Int
	Reserve1           *big.Int
	BlockTimestampLast uint32
}, error,
) {
	var out []interface{}
	err := _IUniswapV2Pair.contract.Call(opts, &out, "getReserves")

	outstruct := new(struct {
		Reserve0           *big.Int
		Reserve1           *big.Int
		BlockTimestampLast uint32
	})
	if err != nil {
		return *outstruct, err
	}

	outstruct.Reserve0 = *abi.ConvertType(out[0], new(*big.Int)).(**big.Int)
	outstruct.Reserve1 = *abi.ConvertType(out[1], new(*big.Int)).(**big.Int)
	outstruct.BlockTimestampLast = *abi.ConvertType(out[2], new(uint32)).(*uint32)

	return *outstruct, err
}
```
