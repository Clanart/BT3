### Title
Gasless swap admission relies on manipulable spot-price `GetAmountIn` from DEX reserves, enabling price-manipulation bypass of the `amountIn` sufficiency check - (File: kaiax/gasless/impl/tx_pool.go)

### Summary
The Sherlock finding highlights that using `slot0`'s instantaneous tick as a security-critical price reference is unsafe because it reflects only the last executed trade and can be trivially manipulated within a single block (large trade or flash loan) before being read. The same bug class is reachable in Kaia's `kaiax/gasless` module: `checkBalanceForSwap` derives the required `amountIn` for a gasless swap directly from the DEX pool's *current* reserves via `GaslessSwapRouter.GetAmountIn`, which is state that any unprivileged actor can move within the same block as the swap they are trying to get admitted.

### Finding Description
`checkBalanceForSwap` in `kaiax/gasless/impl/tx_pool.go` computes the amount of input token required for a proposed gasless `swapForGas` transaction using a live on-chain price read: [1](#0-0) 

```
if g.GaslessConfig.ShouldCheckSwapAmount() {
    routerContract, err := kip247.NewGaslessSwapRouterCaller(swapRouter, bc)
    ...
    requiredAmountIn, err := routerContract.GetAmountIn(nil, token, minAmountOut)
    ...
    if swapArgs.AmountIn.Cmp(requiredAmountIn) < 0 {
        return fmt.Errorf("insufficient amountIn: ...")
    }
}
```

`GetAmountIn` is a `view` call into `GaslessSwapRouter`, which in turn resolves the DEX pair for the token via `getDEXInfo`/`dexAddress` and reads the pool's current reserves (the same pattern used by `UniswapV2Router02.GetAmountIn(amountOut, reserveIn, reserveOut)` bound in `contracts/bindings/uniswap/router/UniswapV2Router02.go`). [2](#0-1) [3](#0-2) 

This is functionally identical to the `slot0`/tick read pattern described in the report: it is a spot value derived from the *current* AMM reserves at the moment of evaluation, with no time-weighted averaging (no TWAP/observe-equivalent mechanism exists in this codebase for either Uniswap V2 pools or the `GaslessSwapRouter`). An unprivileged sender who also controls (or flash-loans into) the underlying DEX pool for the target token can execute a large swap in the same block immediately prior to submitting/propagating their gasless `swapForGas` transaction, temporarily depressing `requiredAmountIn` for a given `minAmountOut`. This lets a swap transaction pass `checkBalanceForSwap`'s admission check (`PreAddTx`/`VerifyExecutable` path in `kaiax/gasless/impl/getter.go`) with an `amountIn` that would not have been sufficient at the pool's normal/undisturbed price. [4](#0-3) 

### Impact Explanation
Because the admission check is the node's only current line of defense before a gasless bundle (`approveTx`+`swapTx`) is queued and eventually executed by a block-producing validator, an attacker who manipulates the DEX reserves to slip an under-collateralized swap past `checkBalanceForSwap` can cause the actual on-chain `swapForGas` execution to behave unexpectedly relative to what the node believed it verified: either the swap proceeds at a real (unmanipulated) price and produces less repayment than the network assumed when admitting/prioritizing the bundle, or the transaction reverts at execution time after having consumed the validator's/proposer's processing effort and, depending on how repayment is structured in `GaslessSwapRouter`, could result in gas costs not being recouped from the swap output as intended by the gasless fee model. This directly undermines the fee/repayment guarantee that the gasless mechanism is built on, i.e., fee-delegation/gasless settlement abuse from a single unprivileged transaction sender.

### Likelihood Explanation
The path is reachable purely by a public, unprivileged transaction sender: submit (or cause to be submitted) a large swap against the same DEX pool referenced by `GaslessSwapRouter.dexAddress(token)` in the same block as their gasless `swapForGas` transaction, then rely on `checkBalanceForSwap`'s single-block spot read of `GetAmountIn`. No governance, validator, or operator privilege is required — only capital or flash-loan access to move the pool's reserves, which is explicitly the attack primitive called out in the source report.

### Recommendation
Do not rely on a single, unmanipulated-assumption spot read of DEX reserves for the `amountIn` sufficiency check in `checkBalanceForSwap`. Use a manipulation-resistant reference, e.g., a cumulative/time-weighted price feed (if/when Uniswap V2-style `price0CumulativeLast`/`price1CumulativeLast` accumulators or an external TWAP oracle become available), or bound the acceptable slippage between the declared `minAmountOut`/`amountRepay` and the live quote, and additionally re-verify the swap's actual output against `amountRepay` at execution time inside `GaslessSwapRouter` (which the caller cannot directly modify), rather than trusting the pre-admission quote as authoritative.

### Proof of Concept
1. Attacker deploys/holds a whitelisted ERC-20 `token` with an existing DEX pool referenced by `GaslessSwapRouter.dexAddress(token)`.
2. Attacker crafts a `swapForGas(token, amountIn, minAmountOut, amountRepay, deadline)` transaction where `amountIn` is only sufficient at a manipulated (depressed) exchange rate.
3. In the same block, attacker (or a colluding party) executes a large swap against the token's DEX pair to shift reserves so that `GaslessSwapRouter.GetAmountIn(token, minAmountOut)` returns a lower `requiredAmountIn`.
4. The node's `checkBalanceForSwap` [1](#0-0)  reads the manipulated reserves and accepts the transaction (`swapArgs.AmountIn.Cmp(requiredAmountIn) >= 0`).
5. By the time the bundle is executed on-chain, the reserves may have reverted (attacker unwinds their manipulating trade), and the real swap output/repayment diverges from what was verified at admission time, breaking the gasless repayment guarantee.

Note: I was unable to locate the Solidity source for `GaslessSwapRouter` (only compiled bytecode/ABI bindings in `contracts/bindings/kip247/GaslessSwapRouter.go` are indexed), so the exact on-chain enforcement (if any) of `amountRepay` at execution time inside `swapForGas` could not be fully verified from the indexed code. If the full repository is needed to confirm this execution-time behavior, a Devin session with full filesystem access would be required to inspect the actual contract source.

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

**File:** contracts/bindings/uniswap/router/UniswapV2Router02.go (L5476-5486)
```go
func (_UniswapV2Router02 *UniswapV2Router02Caller) GetAmountIn(opts *bind.CallOpts, amountOut *big.Int, reserveIn *big.Int, reserveOut *big.Int) (*big.Int, error) {
	var out []interface{}
	err := _UniswapV2Router02.contract.Call(opts, &out, "getAmountIn", amountOut, reserveIn, reserveOut)
	if err != nil {
		return *new(*big.Int), err
	}

	out0 := *abi.ConvertType(out[0], new(*big.Int)).(**big.Int)

	return out0, err
}
```

**File:** kaiax/gasless/impl/getter.go (L211-266)
```go
// VerifyExecutable checks if the given transactions form a valid gasless transaction
// It returns an error explaining why the transaction is not executable if it's not,
// and a boolean indicating whether the transaction is executable
func (g *GaslessModule) VerifyExecutable(approveTxOrNil, swapTx *types.Transaction) error {
	// Sx.
	swapArgs, ok := decodeSwapTx(swapTx, g.signer)
	if !ok {
		return ErrDecodeSwapTx
	}
	if !g.isSwapTx(swapArgs) {
		return ErrSwapTxInvalid
	}

	// Conditions involving ApproveTx
	if approveTxOrNil != nil {
		// Ax.
		approveArgs, ok := decodeApproveTx(approveTxOrNil, g.signer)
		if !ok {
			return ErrDecodeApproveTx
		}
		if !g.isApproveTx(approveArgs) {
			return ErrApproveTxInvalid
		}
		// AP1.
		if approveArgs.Sender != swapArgs.Sender {
			return ErrDifferentSenders
		}
		// SP1.
		if approveArgs.Token != swapArgs.Token {
			return fmt.Errorf("%w: approve token %s, swap token %s", ErrDifferentTokens, approveArgs.Token.Hex(), swapArgs.Token.Hex())
		}
		// SP2.
		if approveArgs.Amount.Cmp(swapArgs.AmountIn) < 0 {
			return fmt.Errorf("%w: approve amount %s, required amount %s", ErrInsufficientApproveAmount, approveArgs.Amount.String(), swapArgs.AmountIn.String())
		}
		// SP3.
		if approveTxOrNil.Nonce()+1 != swapTx.Nonce() {
			return fmt.Errorf("%w: approve nonce %d, swap nonce %d (expected %d)", ErrNonSequentialNonce, approveTxOrNil.Nonce(), swapTx.Nonce(), approveTxOrNil.Nonce()+1)
		}
		if nonce := g.getCurrentStateNonce(approveArgs.Sender); nonce != approveTxOrNil.Nonce() {
			return fmt.Errorf("%w: approve nonce %d, current nonce %d", ErrApproveNonceNotCurrent, approveTxOrNil.Nonce(), nonce)
		}
	} else {
		// SP3.
		if nonce := g.getCurrentStateNonce(swapArgs.Sender); nonce != swapTx.Nonce() {
			return fmt.Errorf("%w: swap nonce %d, current nonce %d", ErrSwapNonceNotCurrent, swapTx.Nonce(), nonce)
		}
	}

	// SP4.
	if swapArgs.AmountRepay.Cmp(repayAmount(approveTxOrNil, swapTx)) != 0 {
		return fmt.Errorf("%w: got %s, expected %s", ErrIncorrectRepayAmount, swapArgs.AmountRepay.String(), repayAmount(approveTxOrNil, swapTx).String())
	}

	return nil
}
```
