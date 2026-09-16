### Title
Spot-price (non-TWAP) AMM read in Gasless SwapTx admission check enables flash-loan/sandwich manipulation of KIP-247 gasless settlement - ([File: kaiax/gasless/impl/tx_pool.go])

### Summary
The Gasless module (KIP-247) validates a `SwapTx` before admitting it to the tx pool by calling `GaslessSwapRouter.GetAmountIn(token, minAmountOut)`, which — like the vulnerable `CurveOracle.getPrice` — derives its value from the **current, unmitigated spot reserves** of an AMM pool rather than a time-weighted average. This makes the admission/settlement check manipulable within the attacker's own block, mirroring the reported flash-loan pricing vulnerability.

### Finding Description
`checkBalanceForSwap` computes the "required" token input for a declared `minAmountOut` purely from the router's live view function: [1](#0-0) 

`GetAmountIn` is a `view` call into `GaslessSwapRouter`, which internally queries the underlying Uniswap-V2-style pool for `token`: [2](#0-1) 

This spot-price pattern is architecturally identical to the reported `CurveOracle.getPrice` bug: both compute an economically significant value (LP USD price / required swap input) from **instantaneous pool reserves with no TWAP**, making it trivially skewable by any actor who can move the pool's reserves in the same block (via a large swap or a flash loan), exactly the class of vulnerability the external report and its recommendation ("use TWAP") describe.

The `SwapForGas` transaction itself is decoded and its `AmountIn`/`MinAmountOut`/`AmountRepay` fields are taken directly from user-supplied calldata, i.e., an attacker/searcher fully controls the inputs being checked against the manipulable spot price: [3](#0-2) 

Because the gasless flow already supports an `ApproveTx` immediately followed by a `SwapTx` from the same sender within the same block (and is checked with the state of that same block when computing readiness/promotion), a searcher can:
1. Submit (or bundle via the auction module) a large swap against the same pool referenced by `GaslessSwapRouter` to skew reserves.
2. Submit their `SwapTx` with a `minAmountOut`/`amountIn` pair calibrated to the skewed price, which `GetAmountIn` will validate as sufficient even though it is not representative of the pool's fair price.
3. Have the target reversed/manipulated transaction and the `SwapTx` land in the same block, so the manipulated reserves are still in effect at execution time as well as at admission time.

### Impact Explanation
This allows an attacker to have the Gasless module accept and admit an economically invalid `SwapTx` — one whose stated `amountIn`/`minAmountOut` only "clears" the balance check because of an artificially manipulated spot price rather than a fair market price. Since the Gasless subsidy mechanism repays the block proposer/fee-delegator (`amountRepay`) out of swap proceeds, a manipulated price can let an attacker extract more value from the swap router/liquidity than they are entitled to (fee-delegation and gasless-settlement abuse), or cause acceptance of a transaction into the pool/block that should have been rejected under fair pricing — both are explicitly in-scope categories (fee-delegation abuse, acceptance of an invalid transaction).

### Likelihood Explanation
Any unprivileged transaction sender who can also move the referenced AMM pool's reserves (a normal DEX swap, no special privileges needed) can trigger this. The check uses no staleness/TWAP protection and is fully reachable from a public RPC submission of a `SwapForGas` transaction, matching the required unprivileged-transaction-sender/RPC-caller reachability constraint.

### Recommendation
Do not rely on the instantaneous `GetAmountIn`/pool spot price for admission and settlement validation of gasless swaps. Use a TWAP-based or otherwise manipulation-resistant price source for the balance/amount checks in `checkBalanceForSwap` (kaiax/gasless/impl/tx_pool.go), or bound the allowed slippage/deviation between the admission-time spot check and a longer-window reference price before permitting the router to execute `swapForGas`.

### Proof of Concept
1. Attacker holds inventory of `token` and/or the pool's paired asset for the pool used by `GaslessSwapRouter`.
2. In the target block, attacker (or their searcher/auction bid) executes a large swap against that pool to skew reserves so that `GetAmountIn(token, minAmountOut)` returns an artificially low required `amountIn`.
3. Attacker submits an `ApproveTx` + `SwapForGas` `SwapTx` pair (`kaiax/gasless/impl/getter.go` `decodeSwapTx`) whose `AmountIn` satisfies the skewed `GetAmountIn` value from `checkBalanceForSwap` (`kaiax/gasless/impl/tx_pool.go:128-141`), even though it would fail against the pool's fair, unmanipulated price.
4. If both the manipulation trade and the `SwapTx` land within the same block (feasible since Gasless approve+swap pairs execute in sequence in the same block and the auction module (KIP-249) permits bundling a follow-up transaction after a target transaction), the swap executes under the manipulated reserves, letting the attacker obtain fee-delegated gas sponsorship while contributing less real economic value than an honest actor would need to provide.

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

**File:** contracts/bindings/kip247/GaslessSwapRouter.go (L301-316)
```go
// GetAmountIn is a free data retrieval call binding the contract method 0x632db21c.
//
// Solidity: function getAmountIn(address token, uint256 amountOut) view returns(uint256 amountIn)
func (_GaslessSwapRouter *GaslessSwapRouterCaller) GetAmountIn(opts *bind.CallOpts, token common.Address, amountOut *big.Int) (*big.Int, error) {
	var out []interface{}
	err := _GaslessSwapRouter.contract.Call(opts, &out, "getAmountIn", token, amountOut)

	if err != nil {
		return *new(*big.Int), err
	}

	out0 := *abi.ConvertType(out[0], new(*big.Int)).(**big.Int)

	return out0, err

}
```

**File:** kaiax/gasless/impl/getter.go (L142-180)
```go
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
