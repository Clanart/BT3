### Title
Gasless swap repayment amount is computed from the swap tx's static gas-price fields instead of the LendTx's actual `EffectiveGasPrice`, allowing systematic over/under-charging as the KIP-71 base fee changes - (File: `kaiax/gasless/impl/getter.go`)

### Summary
The KIP-247 gasless module computes the amount a gasless user must repay to the block proposer (`AmountRepay`) using the swap transaction's nominal, signed-in gas price (`swapTx.GasPrice()`), not the actual effective gas price that will be charged to the auto-generated `LendTx` at settlement time. Because Kaia's fee accounting (`EffectiveGasPrice`, driven by the KIP-71 dynamic base fee) can diverge from a transaction's nominal price field, the repay amount baked into the swap transaction is a stale/nominal snapshot rather than "the price actually paid" — precisely the bug class described in the external report, applied here to the gasless fee-delegation/lending mechanism instead of a Solidity lock-price mechanism.

### Finding Description
`repayAmount()` computes the portion of repayment meant to cover the LendTx's own fee (`R1`) as: [1](#0-0) 

This uses `swapTx.GasPrice()` — the transaction's nominal price/fee-cap field fixed by the user at signing time — as a proxy for "the fee the LendTx will actually cost the proposer."

The `LendTx` itself, however, is built as a dynamic-fee transaction whose actual cost is governed by the standard Kaia/EIP-1559-style effective gas price mechanism, using `GasFeeCap`/`GasTipCap` copied from the swap tx: [2](#0-1) 

The amount lent to the user is also computed from the two transactions' nominal `Fee()` values: [3](#0-2) 

Kaia's fee accounting, however, does not necessarily charge `GasFeeCap` (nor the constant `GasPrice()`); it charges an `EffectiveGasPrice` that is derived from the current block's KIP-71 base fee and can be materially lower than the transaction's nominal price fields. The `TestEffectiveGasPrice` test demonstrates this divergence for a dynamic-fee tx with `GasFeeCap=4000`/`GasTipCap=1000` — once a non-zero `BaseFee` header exists, the effective price returned is driven by `header.BaseFee`, not the nominal fee-cap: [4](#0-3) 

At settlement, `TransitionDb`/`returnGas` charge and refund balances using `st.gasPrice` (the effective price actually applied to that transaction in that block), not the nominal price used by `repayAmount()`/`lendAmount()`: [5](#0-4) [6](#0-5) 

The verification check that gates whether a swap tx is accepted as a valid gasless tx (`SP4`) enforces that `swapArgs.AmountRepay` exactly equals this nominally-computed `repayAmount()`: [7](#0-6) 

Since the KIP-71 base fee moves block-to-block based on network gas usage (governed by `NextMagmaBlockBaseFee`), and is entirely outside the gasless user's and even the proposer's control at the moment the swap tx is signed, the `R1` component baked into `AmountRepay` will diverge from what the LendTx actually costs whenever `EffectiveGasPrice(LendTx) != swapTx.GasPrice()`: [8](#0-7) 

### Impact Explanation
`AmountRepay` is a value hard-coded into the user-signed `GaslessSwapTx` calldata and is exactly what `swapForGas` pulls from the user's swapped token proceeds to repay the proposer (see `checkBalanceForSwap`'s `minAmountOut >= amountRepay` invariant): [9](#0-8) 

Because `R1` is derived from a fixed nominal price rather than the block's actual effective gas price for the LendTx, gasless users systematically overpay (when the effective price is lower than the nominal price used in `R1`) or the proposer's lent gas is under-recovered (when the effective price is higher), constituting value leakage/fee-abuse in the gasless settlement flow — reachable by any unprivileged sender of a `GaslessApproveTx`/`GaslessSwapTx` pair, with no special privileges required.

### Likelihood Explanation
This triggers on essentially every gasless swap once base fee deviates from the flat rate assumed at signing time, since KIP-71's base fee adjusts continuously with network gas usage; the divergence is not an edge case but an expected steady-state condition of the dynamic fee mechanism. Any wallet/dApp integrator following the documented `swapForGas` flow is affected without needing to craft anything malicious.

### Recommendation
Compute `R1` (and hence `AmountRepay`/`lendAmount`) using the `LendTx`'s actual `EffectiveGasPrice` for the target block (i.e., `min(GasFeeCap, baseFee + GasTipCap)` evaluated against the block's expected base fee) rather than the swap tx's static `GasPrice()`/`Fee()` fields, or reconcile the repay amount post-hoc (similar in spirit to `AdjustFeesByReceipts`) using the LendTx's actual charged fee from the receipt before settlement is finalized.

### Proof of Concept
1. User signs a `GaslessSwapTx` with `GasFeeCap = X`, `GasTipCap = Y`, and `AmountRepay` computed off-chain to match `repayAmount()` = `swapTx.GasPrice() * TxGas + lendAmount(...)`, where `swapTx.GasPrice()` resolves to `X` (the fee cap) per [1](#0-0) .
2. The block proposer generates a `LendTx` copying `GasFeeCap=X`, `GasTipCap=Y` per [10](#0-9) .
3. At inclusion time the block's KIP-71 base fee is below `X - Y`, so the `LendTx`'s actual `EffectiveGasPrice` (charged via `st.gasPrice` in `returnGas`/fee distribution, [6](#0-5) ) is lower than `X`.
4. The user's swap tx still repays `R1 = X * TxGas` (the nominal amount encoded via `SP4` verification, [7](#0-6) ) even though the proposer's actual `LendTx` cost was `EffectiveGasPrice * TxGas < X * TxGas`, resulting in the user overpaying the difference to the proposer/miner.

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

**File:** kaiax/gasless/impl/getter.go (L281-310)
```go
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
```

**File:** kaiax/gasless/impl/getter.go (L346-359)
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
```

**File:** kaiax/gasless/impl/getter.go (L361-367)
```go
func repayAmount(approveTxOrNil, swapTx *types.Transaction) *big.Int {
	// R1 = LendTx.Fee() = SwapTx.GasPrice() * TxGas
	r1 := new(big.Int).Mul(swapTx.GasPrice(), new(big.Int).SetUint64(params.TxGas))

	// RepayAmount = R1 + R2 + R3
	return new(big.Int).Add(r1, lendAmount(approveTxOrNil, swapTx))
}
```

**File:** blockchain/types/transaction_test.go (L375-383)
```go
	header.BaseFee = big.NewInt(2000)
	have = legacyTx.EffectiveGasPrice(header, testKaiaChainConfig)
	want = header.BaseFee
	assert.Equal(t, want, have)

	have = dynamicTx.EffectiveGasPrice(header, testKaiaChainConfig)
	want = header.BaseFee
	assert.Equal(t, want, have)

```

**File:** blockchain/state_transition.go (L646-658)
```go
	// Defer transferring Tx fee when DeferredTxFee is true
	// DeferredTxFee has never been voted, so it's ok to use the genesis value instead of the latest value from governance.
	if st.evm.ChainConfig().Governance == nil || !st.evm.ChainConfig().Governance.DeferredTxFee() {
		if rules.IsMagma {
			// Since Magma, half the fee is burned and the other half goes to the proposer (Rewardbase).
			fee := new(big.Int).Mul(new(big.Int).SetUint64(st.gasUsed()), st.gasPrice)
			burnt := new(big.Int).Div(fee, big.NewInt(2)) // fee / 2
			distributed := new(big.Int).Sub(fee, burnt)
			st.state.AddBalance(st.evm.Context.Rewardbase, distributed)
		} else {
			st.state.AddBalance(st.evm.Context.Coinbase, new(big.Int).Mul(new(big.Int).SetUint64(st.gasUsed()), st.gasPrice))
		}
	}
```

**File:** blockchain/state_transition.go (L812-828)
```go
// returnGas returns KAIA for remaining gas, exchanged at the original rate.
func (st *StateTransition) returnGas() {
	remaining := new(big.Int).Mul(new(big.Int).SetUint64(st.gas), st.gasPrice)

	validatedFeePayer := st.msg.ValidatedFeePayer()
	validatedSender := st.msg.ValidatedSender()
	feeRatio, isRatioTx := st.msg.FeeRatio()
	if isRatioTx {
		feePayer, feeSender := types.CalcFeeWithRatio(feeRatio, remaining)

		st.state.AddBalance(validatedFeePayer, feePayer)
		st.state.AddBalance(validatedSender, feeSender)
	} else {
		// To make a short circuit, the below routine processes when feeRatio == 100.
		st.state.AddBalance(validatedFeePayer, remaining)
	}
}
```

**File:** params/kip71_config.go (L58-68)
```go
func (kc *KIP71Config) NextMagmaBlockBaseFee(parentHeaderNumber *big.Int, parentHeaderBaseFee *big.Int, parentHeaderGasUsed uint64) *big.Int {
	// governance parameters
	lowerBoundBaseFee := new(big.Int).SetUint64(kc.LowerBoundBaseFee)
	upperBoundBaseFee := new(big.Int).SetUint64(kc.UpperBoundBaseFee)
	makeEvenByCeil(lowerBoundBaseFee)
	makeEvenByFloor(upperBoundBaseFee)

	// If the parent is the magma disabled block or genesis, then return the lowerBoundBaseFee (default 25ston)
	if parentHeaderNumber.Cmp(new(big.Int).SetUint64(0)) == 0 || parentHeaderBaseFee == nil {
		return makeEvenByFloor(lowerBoundBaseFee)
	}
```

**File:** kaiax/gasless/impl/tx_pool.go (L115-120)
```go
	// tx.minAmountOut >= tx.amountRepay
	minAmountOut := swapArgs.MinAmountOut
	amountRepay := swapArgs.AmountRepay
	if minAmountOut.Cmp(amountRepay) < 0 {
		return fmt.Errorf("insufficient minAmountOut: minAmountOut=%s, amountRepay=%s", minAmountOut.String(), amountRepay.String())
	}
```
