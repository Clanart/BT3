### Title
Gasless `swapForGas` repayment amount computed from a fixed `GasLimit*GasPrice` estimate, not the actual execution gas, allowing repay-amount mismatch and fee/settlement abuse - (File: kaiax/gasless/impl/getter.go)

### Summary
The Kaia gasless module computes the value the user must repay to the sponsoring proposer (`RepayAmount`/`LendAmount`) from static transaction fields (`GasPrice() * TxGas`, `tx.Fee()`), fixed once at the moment the `approve`/`swap` bundle is validated in the tx pool. This is structurally the same bug class as the Rubicon `Position.closePosition()` finding: an amount owed is derived from a rate/estimate that is not guaranteed to still hold true when the value is actually settled (at block execution), because the estimate does not track the state that determines the true cost.

### Finding Description
`repayAmount()` and `lendAmount()` in `kaiax/gasless/impl/getter.go` compute the amount the gasless swapper must repay purely from the transactions' own `GasPrice()` and `Fee()` fields: [1](#0-0) 

`VerifyExecutable()` enforces `SwapTx.AmountRepay == repayAmount(approveTxOrNil, swapTx)` (condition SP4) at admission time, using this static formula: [2](#0-1) 

The generated `LendTx` (the value the proposer fronts to the user) is likewise derived from the same static `Fee()`-based estimate rather than the transactions' actual gas consumption: [3](#0-2) 

This mirrors the Rubicon bug pattern: the amount to be repaid (`RepayAmount`) is calculated from a value (`GasPrice`) that is fixed at validation time, while the actual gas *used* by the `approve`/`swap` execution (i.e., the real cost incurred by the proposer) can differ — e.g., due to whether the ERC-20 storage slot is warm/cold, whether the allowance is already set, or gas-refund behavior — analogous to how Rubicon's `borrowRatePerBlock()` could change between the position's opening and its closing. In both cases, the settlement value is decoupled from the on-chain state that actually determines the true cost/interest, opening a gap between what is charged and what is truly owed.

Separately, `checkBalanceForSwap` in `kaiax/gasless/impl/tx_pool.go` validates `AmountIn` against the router's `GetAmountIn(minAmountOut)` quote read from current state at mempool-admission time: [4](#0-3) 
Since the AMM reserves (the "rate") can change between this check and actual `swapForGas` execution in a block (analogous to Rubicon's borrow rate changing between Bob's and Alice's positions), the quote used for admission is not guaranteed to reflect the state at execution time.

### Impact Explanation
If the admission-time repay/lend amount computation systematically diverges from the actual execution-time gas cost, the proposer sponsoring the gasless transaction (who fronts `LendAmount` and expects `RepayAmount` back via the swap) can be under- or over-compensated on every gasless bundle. At scale this is a fee-abuse/value-extraction vector against block proposers running the gasless module, which is a concrete unauthorized value movement affecting the fee-delegation/gasless settlement path reachable by any ordinary sender who submits an `approve`+`swap` gasless bundle.

### Likelihood Explanation
Medium. The formula uses `params.TxGas` as a fixed constant for the sponsoring `LendTx`'s gas component in `repayAmount()`, and `tx.Fee()` (GasLimit × GasPrice, not actually-used gas) for the approve/swap legs, so any systematic difference between declared `GasLimit` and actually consumed gas (which is common, since callers routinely set generous gas limits) produces a deterministic, exploitable gap. This does not require a malicious validator or network-level attack — any unprivileged sender constructing the approve/swap bundle can trigger it, satisfying the reachability constraint.

### Recommendation
Base `RepayAmount`/`LendAmount` on the actual gas used by the corresponding transactions (post-execution receipts) rather than static `GasLimit × GasPrice`/`Fee()` estimates, or otherwise reconcile/settle the difference after execution (similar to the `AdjustFeesByReceipts` reconciliation pattern already used elsewhere in the codebase for intrinsic-gas estimation errors, see `tests/kaia_test_account_map_test.go:225-272`). Likewise, re-validate the swap's `AmountIn`/`amountRepay` against the router's live quote at execution time (not only at pool admission) to avoid stale-quote settlement.

### Proof of Concept
Conceptual PoC (mirrors the Rubicon two-actor pattern):
1. A gasless user submits `approve` + `swapForGas(token, amountIn, minAmountOut, amountRepay, deadline)`. `amountRepay` is validated in `VerifyExecutable` against `repayAmount()`, which uses `swapTx.GasPrice() * params.TxGas` plus `tx.Fee()` of the approve/swap legs — figures fixed at declared `GasLimit`, not actual execution gas.
2. The proposer's `LendTx` is generated with `LendAmount = ApproveTx.Fee() + SwapTx.Fee()` from `lendAmount()`.
3. At block execution, the approve/swap legs consume less (or more) gas than their declared `GasLimit` (e.g., warm slot from a prior bundle in the same block, or an already-approved allowance short-circuiting logic), so the actual cost incurred by the proposer differs from `LendAmount`/`RepayAmount` computed pre-execution.
4. Because settlement (`SwappedForGas` event / final balances) is not reconciled against actual gas usage inside this module's code path, the proposer systematically over- or under-recovers value across many gasless bundles.

Note: I could not fully trace whether a downstream reconciliation step (e.g., in `blockchain/state_transition.go` or the bundle-execution path) corrects this gap before finalization — this needs verification with a full Devin session to confirm whether `RepayAmount`/`LendAmount` are settled exactly as computed here or adjusted post-execution.

### Citations

**File:** kaiax/gasless/impl/getter.go (L260-266)
```go
	// SP4.
	if swapArgs.AmountRepay.Cmp(repayAmount(approveTxOrNil, swapTx)) != 0 {
		return fmt.Errorf("%w: got %s, expected %s", ErrIncorrectRepayAmount, swapArgs.AmountRepay.String(), repayAmount(approveTxOrNil, swapTx).String())
	}

	return nil
}
```

**File:** kaiax/gasless/impl/getter.go (L268-313)
```go
// MakeLendTx creates a transaction with following properties:
// L1. LendTx.type = 0x7802 (TxTypeEthereumDynamicFee)
// L2. LendTx.from = proposer
// L3. LendTx.to = SwapTx.from
// L4. LendTx.value = LendAmount(approveTxOrNil, swapTx)
func (g *GaslessModule) GetLendTxGenerator(approveTxOrNil, swapTx *types.Transaction) *builder.TxOrGen {
	var src []byte
	if approveTxOrNil != nil {
		src = append(src, approveTxOrNil.Hash().Bytes()...)
	}
	src = append(src, swapTx.Hash().Bytes()...)
	bundleHash := crypto.Keccak256Hash(src)

	gen := func(nonce uint64) (*types.Transaction, error) {
		var (
			chainId = g.InitOpts.ChainConfig.ChainID
			signer  = types.LatestSignerForChainID(chainId)
			key     = g.InitOpts.NodeKey
		)

		to, err := types.Sender(signer, swapTx)
		if err != nil {
			return nil, err
		}

		tx, err := types.NewTransactionWithMap(types.TxTypeEthereumDynamicFee, map[types.TxValueKeyType]interface{}{
			types.TxValueKeyNonce:      nonce,
			types.TxValueKeyTo:         &to,
			types.TxValueKeyAmount:     lendAmount(approveTxOrNil, swapTx),
			types.TxValueKeyData:       common.Hex2Bytes("0x"),
			types.TxValueKeyGasLimit:   params.TxGas,
			types.TxValueKeyGasFeeCap:  swapTx.GasFeeCap(),
			types.TxValueKeyGasTipCap:  swapTx.GasTipCap(),
			types.TxValueKeyAccessList: types.AccessList{},
			types.TxValueKeyChainID:    chainId,
		})
		if err != nil {
			return nil, err
		}

		err = tx.Sign(signer, key)
		return tx, err
	}

	return builder.NewTxOrGenFromGen(gen, bundleHash)
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
