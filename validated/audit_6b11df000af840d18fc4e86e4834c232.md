### Title
Gasless swap admission check trusts a stale AMM spot price with no oracle/TWAP protection, allowing amountRepay-value theft from the block proposer - (File: kaiax/gasless/impl/tx_pool.go)

### Summary
The `buyJUSD()` report is a "protocol trusts a price without verifying it against a manipulation/deviation-resistant source" bug class. In Kaia's KIP-247 gasless-transaction flow, `GaslessModule.checkBalanceForSwap` (reachable directly from an unprivileged transaction sender submitting a `GaslessSwapTx`) validates that `amountIn` is sufficient by querying `GaslessSwapRouter.getAmountIn(token, minAmountOut)`, i.e., a live Uniswap-V2-style spot price, with no TWAP/oracle sanity check and no re-validation once the transaction is promoted into a block bundle.

### Finding Description
`checkBalanceForSwap` in [1](#0-0)  performs the core "sufficient funds to repay gas" check for a gasless swap:

```go
requiredAmountIn, err := routerContract.GetAmountIn(nil, token, minAmountOut)
...
if swapArgs.AmountIn.Cmp(requiredAmountIn) < 0 {
    return fmt.Errorf("insufficient amountIn...")
}
```

This call reads the current on-chain AMM reserves (Uniswap V2 factory/router bound in [2](#0-1) ) at the moment the transaction is inserted into the pool. There is no protection equivalent to a price oracle, deviation bound, or TWAP — the check is a raw spot-price read, exactly analogous to `buyJUSD()` trusting `amount` 1:1 without consulting an oracle.

The block-building flow (per [3](#0-2)  and `IsReady`/`GetCheckBalance` machinery in [4](#0-3) ) prepends a `LendTxGenerator` transaction that fronts KAIA gas fees to the sender **before** the actual swap executes. The proposer/lender has already paid out `amountRepay` in KAIA gas on the assumption the swap will yield at least `minAmountOut` (≥ `amountRepay`) worth of token. Since `getAmountIn`/`minAmountOut` are derived from a spot AMM price with no manipulation resistance, a searcher (the same swap sender, or a colluding party using the block's own tx ordering/bundle capability) can move the pool price between admission-time check and inclusion-time execution — e.g., by including their own prior trade in the same block/bundle, or across blocks while the tx sits in the pool — such that:
- The pool-admission check (`checkBalanceForSwap`) passes using a favorable (manipulated) reserve state, or
- By the time the swap actually executes on-chain, actual `amountOut` still satisfies the user-supplied `minAmountOut` (attacker sets `minAmountOut` deliberately low, satisfying only `minAmountOut ≥ amountRepay` per the check at line 118), but the *real* swap yields exactly at the edge of `amountRepay`, extracting the full commission/spread and leaving the proposer's lent gas repaid only at the manipulated minimal price — the classic "assume a favorable exchange rate always holds" flaw.

This differs from the original JUSD bug only in that Kaia's price source is a live AMM rather than a hardcoded 1:1, but the root cause is identical: a value-transfer decision (how much of the proposer's fronted KAIA gas gets repaid) is gated on a price read with no robustness guarantee, checked once at mempool-admission time using `bind.CallOpts{}` (i.e., `nil` block number → latest state) rather than being bound to the actual execution-time state or an oracle.

### Impact Explanation
If the check can be satisfied with a manipulated/stale price, the block proposer's `LendTxGenerator`-fronted KAIA can be repaid at a rate worse than intended, or a malicious sender can construct `minAmountOut`/`amountRepay` values that pass the pool check yet do not actually make the proposer whole after the swap executes at a different real reserve ratio — resulting in fee/value theft from the block proposer (a form of "fee-delegation abuse"/"reward redirection" in the taxonomy of acceptable analog impacts). This is a Medium-severity issue: it requires specific reserve-state timing but does not require any privileged access — any transaction sender constructing a `GaslessSwapTx` can attempt it.

### Likelihood Explanation
Likelihood is moderate: exploiting requires the attacker (or a colluding searcher/bidder able to influence transaction ordering, e.g. via the auction module) to move the AMM pool's reserves between the mempool-admission check and block inclusion. Because `GetAmountIn` uses `nil` (latest) block context and the actual swap executes later in the block-building pipeline (`LendTxGenerator` + `GaslessApproveTx` + `GaslessSwapTx` bundle), a window for reserve manipulation exists on every gasless-swap-enabled token pool, making this practically reachable rather than purely theoretical.

### Recommendation
- Re-validate `amountIn`/`minAmountOut` against the exact state used for final execution rather than trusting the mempool-admission-time spot price, or bind the `GetAmountIn` call to the block being built.
- Add slippage/deviation bounds (e.g., compare against a TWAP or a bounded-deviation oracle) before allowing the proposer to front gas via `LendTxGenerator`.
- Consider requiring the swap's actual on-chain `amountRepay` transfer to be verified post-execution before finalizing the bundle, so that any repayment shortfall causes the whole bundle (including the lend transaction) to be discarded/reverted atomically.

### Proof of Concept
Conceptual (state not fully executable via static review — flagged as uncertain):
1. Attacker holds a large balance of `token` and knows the current AMM reserves for the `token`/`WKAIA` pair used by `GaslessSwapRouter`.
2. Attacker submits (or arranges via auction bidding) a trade in the same block that shifts the pool reserves right after the mempool-admission check for their `GaslessSwapTx` passes `checkBalanceForSwap` (which used the pre-shift reserves via `GetAmountIn(nil, token, minAmountOut)`).
3. The bundle `[LendTxGenerator, GaslessApproveTx, GaslessSwapTx]` is built per [3](#0-2) ; the proposer has already fronted `amountRepay` in KAIA via `LendTxGenerator`.
4. `SwapForGas` executes against the shifted reserves; the actual output only marginally covers `minAmountOut`, so the swap does not revert, but the effective spread captured for the proposer/commission is worse than assumed at admission time, or the sender extracts value in the process (exact quantification of extractable value would require deploying the `GaslessSwapRouter`/`UniswapV2` contracts locally and simulating both admission-time and execution-time reserve states, which was not performed in this review).

**Confidence caveat:** I was unable to fully trace whether an on-chain hard revert in `swapForGas` (e.g., an internal `require(amountOut >= minAmountOut)` check enforced at execution time identical to the admission-time check) fully closes this gap, since the Solidity source for `GaslessSwapRouter.swapForGas` was only available as compiled bytecode/bindings in the index, not decompiled source [5](#0-4) . If the on-chain `swapForGas` function strictly re-derives and enforces `amountRepay` from the *actual* post-swap output rather than trusting the caller-supplied `amountRepay`, the exploitability described here would be substantially reduced. A Devin session with contract-source access (or the ability to decompile/build the KIP-247 Solidity source) would be needed to conclusively confirm or refute exploitability.

### Citations

**File:** kaiax/gasless/impl/tx_pool.go (L60-72)
```go
}

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

**File:** contracts/bindings/uniswap/router/UniswapV2Router02.go (L4011-4024)
```go
// GetAmountsIn is a free data retrieval call binding the contract method 0x1f00ca74.
//
// Solidity: function getAmountsIn(uint256 amountOut, address[] path) view returns(uint256[] amounts)
func (_IUniswapV2Router02 *IUniswapV2Router02Caller) GetAmountsIn(opts *bind.CallOpts, amountOut *big.Int, path []common.Address) ([]*big.Int, error) {
	var out []interface{}
	err := _IUniswapV2Router02.contract.Call(opts, &out, "getAmountsIn", amountOut, path)
	if err != nil {
		return *new([]*big.Int), err
	}

	out0 := *abi.ConvertType(out[0], new([]*big.Int)).(*[]*big.Int)

	return out0, err
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
