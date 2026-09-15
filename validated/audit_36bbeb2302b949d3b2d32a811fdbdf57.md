Based on the investigation, the closest reachable analog to the "check enforced only during addition but bypassable through another action" bug class in this codebase is in the `kaiax/gasless` module, where the gas-lending guarantee (`AmountRepay` correctness) is validated once, off-chain, at bundle-construction time, but the value-transferring `LendTx` is not atomically tied to that guarantee holding at actual execution time.

### Title
Non-atomic Gasless Bundle Allows Unrecoverable Gas-Fee Lending (Value Leakage) - ([File: kaiax/gasless/impl/builder.go])

### Summary
The gasless module (`kaiax/gasless`) lends a user KAIA (via `LendTx`) so that the user's `GaslessApproveTx`/`GaslessSwapTx` can be included without the user holding native KAIA, expecting the swap to automatically repay the lent amount. The correctness invariant `SwapTx.amountRepay == RepayAmount(approveTx, swapTx)` and related balance/allowance checks are verified only once, at bundle-construction/tx-pool-admission time in `VerifyExecutable`/`checkBalanceForSwap`, similar to how the audited contract enforced its 80% float ratio only in `addFloat()`. Just as that ratio could later be violated through an unchecked path (`remove()`), the gasless repayment guarantee can be violated through paths not covered by the initial check, because the bundle is built with `atomic=false`.

### Finding Description
`checkBalanceForSwap` verifies `minAmountOut >= amountRepay`, token allowance, and token balance at the moment the transaction enters the pool: [1](#0-0) . `VerifyExecutable` similarly re-derives the expected repay amount and checks it against the declared `AmountRepay` [2](#0-1) . The `LendTx` unconditionally transfers `lendAmount(approveTxOrNil, swapTx)` KAIA from the proposer to the sender before the swap itself is executed [3](#0-2) . The three transactions (`LendTxGenerator`, `ApproveTx`, `SwapTx`) are packaged into a `Bundle` via `builder.NewBundle(bundleTxs, targetTxHash, false)` [4](#0-3) , where the third argument (constructed as `false`) determines all-or-nothing execution. Because the correctness checks (repay ratio, allowance, balance, swap price) are only evaluated at pool-admission/bundle-construction time against state that can still change before block inclusion (e.g., the sender revoking token allowance, draining token balance via another transaction, or on-chain price movement making the swap revert), a sender could cause the `SwapTx` to fail on execution while the `LendTx` — which carries real KAIA value and has no dependency on swap success — still executes and credits the sender. This mirrors the reported bug class: an invariant (float ≤ 80%, or here, "lend amount is recoverable via swap repayment") is enforced only on one path (`addFloat`/pool admission) but is not re-enforced or made atomic against a second path (`remove`/non-atomic bundle execution), letting an unprivileged actor drive the system into an invariant-violating state.

### Impact Explanation
If the bundle is not executed atomically, or if the checked invariant does not hold by the time of block inclusion, a gasless user can obtain the lent KAIA (`R1+R2+R3`, i.e., real fee funds paid by the block proposer) without providing the promised token repayment, since the `SwapTx`/`ApproveTx` failing does not claw back the already-sent `LendTx` value. This is a direct value-movement issue draining proposer-funded gas subsidies (fee/gasless settlement theft), which the report's scope explicitly calls out as impactful (gasless module abuse).

### Likelihood Explanation
Any public RPC caller / gasless user can submit a `GaslessApproveTx`+`GaslessSwapTx` pair that passes admission-time checks, and can then independently submit or trigger a state change (e.g., revoke approval, transfer away the token, or manipulate DEX price within the router's tolerance) before the proposer includes the bundle, causing the swap leg to fail while still receiving the lent KAIA — no validator/node compromise required, only transaction timing that any unprivileged sender controls.

### Recommendation
Verify that bundle execution set by `ExtractTxBundles`/`builder.NewBundle` is strictly atomic (all constituent transactions succeed or the whole bundle, including `LendTx`, is dropped), and additionally re-validate the repayment/balance/allowance invariants immediately before executing `LendTx` within the same state transition as the swap, not merely at pool-admission time — analogous to the audited fix that added `checkUtilization()` at every state-mutating entry point rather than only at `addFloat()`.

### Proof of Concept
Not independently reproducible from static analysis alone: I could not confirm from the available index whether the third parameter to `builder.NewBundle` (`false`) denotes non-atomic execution, nor could I inspect `work/builder`'s `Bundle`/`IsConflict` semantics or the on-chain `GaslessSwapRouter` contract to confirm there is no additional on-chain safeguard that reverts `LendTx` if `SwapTx` fails in the same block. This is a **potential** analog pending confirmation of `Bundle` atomicity semantics — flagging as uncertain rather than a confirmed exploit.

### Citations

**File:** kaiax/gasless/impl/tx_pool.go (L107-120)
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
```

**File:** kaiax/gasless/impl/getter.go (L260-263)
```go
	// SP4.
	if swapArgs.AmountRepay.Cmp(repayAmount(approveTxOrNil, swapTx)) != 0 {
		return fmt.Errorf("%w: got %s, expected %s", ErrIncorrectRepayAmount, swapArgs.AmountRepay.String(), repayAmount(approveTxOrNil, swapTx).String())
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

**File:** kaiax/gasless/impl/builder.go (L47-51)
```go
			b := builder.NewBundle(
				bundleTxs,
				targetTxHash,
				false,
			)
```
