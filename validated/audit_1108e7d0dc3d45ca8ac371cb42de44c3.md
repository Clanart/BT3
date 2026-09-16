### Title
Front-running the GaslessSwapRouter AMM pool causes `SwapForGas` to revert after `LendTx` unconditionally transfers KAIA, allowing proposer fee-delegation funds to be stolen without repayment - (File: `kaiax/gasless/impl/tx_pool.go`, `kaiax/gasless/impl/getter.go`)

### Summary
The `kaiax/gasless` module (KIP-247) checks a `GaslessSwapTx`'s `amountIn`/`minAmountOut` against the *current* on-chain reserves of the (permissionless, unprivileged) AMM pool used by `GaslessSwapRouter` at pool-admission time, but the actual gas repayment only happens later when `SwapForGas` executes on-chain, and the `LendTx` that fronts the user's gas is an unconditional, independent value transfer that is not atomically tied to the swap succeeding. Any unprivileged actor can move the AMM pool's reserves (a normal, public swap - conceptually identical to draining/"borrowing" all the liquidity as in the AAVE report) between admission-time validation and block-inclusion time, causing `SwapForGas` to revert on execution while the preceding `LendTx` in the same bundle has already paid out real KAIA to the gasless sender.

### Finding Description
`checkBalanceForSwap` validates `swapArgs.AmountIn >= router.GetAmountIn(token, minAmountOut)` using the AMM reserves as read at the time the check runs: [1](#0-0) 

This check gates transaction-pool promotion/readiness only; it is not re-validated atomically with the actual swap execution inside the same EVM call that repays the proposer.

`GetLendTxGenerator`/`lendAmount` construct an unconditional native-KAIA transfer (`LendTx`) to the gasless sender for the full lent amount (`ApproveTx.Fee() + SwapTx.Fee()`), independent from whether the subsequent `SwapForGas` call ultimately succeeds: [2](#0-1) [3](#0-2) 

Per the module's own documentation, the block-building logic assembles a bundle of `[LendTxGenerator, GaslessApproveTx, GaslessSwapTx]` (or without approve) and only checks for *conflicts with other bundles* before inclusion - there is no described re-simulation/atomic all-or-nothing rollback of the `LendTx` if the `SwapTx` later reverts: [4](#0-3) 

`SwapForGas` on the router (`kip247.GaslessSwapRouter`) performs the actual AMM swap at execution time and is expected to revert (slippage protection) if actual output falls below `minAmountOut`/`amountRepay`: [5](#0-4) 

Because the AMM pool backing `GaslessSwapRouter` is a standard, permissionless Uniswap-V2-style pool (as used in the gasless test harness), any unprivileged actor can submit an ordinary transaction that shifts reserves between the moment `checkBalanceForSwap`/`GetAmountIn` was evaluated and the moment the `LendTx + SwapTx` bundle is actually executed in a block, exactly analogous to the original report's technique of draining/borrowing reserves from a shared external liquidity venue to break an operation that depends on it succeeding: [6](#0-5) 

### Impact Explanation
If the `LendTx` (a real, unconditional native-token transfer from the block proposer's lending mechanism) lands on-chain and the paired `SwapForGas` transaction reverts due to manipulated pool state, the sender keeps the lent KAIA while the proposer receives no repayment, since repayment is only performed inside the (now-reverted) swap call. This is a direct, unauthorized value movement/fee-delegation abuse: an unprivileged actor can cause the proposer to lose the lent gas amount for every gasless transaction they can get included this way, and can repeat the attack as long as gasless transactions are processed, at the cost of only ordinary swap fees on the AMM pool.

### Likelihood Explanation
The precondition (manipulating a public AMM pool's reserves) requires no special privilege - any transaction sender can submit ordinary swaps against the same pool that `GaslessSwapRouter` relies on for pricing, and can time it around a known pending gasless bundle (visible in the mempool) exactly as the original AAVE report describes for a lending pool. The severity depends on how large/liquid the specific AMM pool is and on whether any additional re-check happens immediately before block inclusion; I could not fully confirm from `work/builder/builder.go` whether the block-builder re-simulates the whole bundle atomically (which would mitigate this) due to index/tool limitations, so this should be verified against the live implementation.

### Recommendation
- Perform the `checkBalanceForSwap`/`GetAmountIn` re-validation strictly inside the same atomic execution context (immediately before inclusion, with no gap), or better, have the router revert the whole bundle (including the value already lent) if the swap fails, e.g. by making `LendTx`'s payout conditional/refundable, or by using a single atomic multicall (lend+swap+repay) instead of two independent top-level transactions.
- Consider giving `SwapForGas` protection against reserve manipulation within the same block (e.g., re-checking pool state right before final swap, TWAP-based bounds, or a per-block cap on price impact) rather than relying on a stale snapshot taken at tx-pool admission time.
- If block building already re-simulates the whole `[LendTx, ApproveTx, SwapTx]` bundle atomically and discards it wholesale on any failure (this needs verification in `work/builder/builder.go`), the risk is limited to reduced throughput/wasted proposer compute rather than fund loss; this should be explicitly confirmed and documented.

### Proof of Concept
1. Attacker (or colluding party) observes a valid `GaslessApproveTx`/`GaslessSwapTx` pair in the mempool for token `T` on `GaslessSwapRouter`, with `amountIn` computed to satisfy `GetAmountIn(minAmountOut)` at time `t0` (per `checkBalanceForSwap`, `kaiax/gasless/impl/tx_pool.go:128-141`).
2. Before the proposer includes the `LendTxGenerator` output + the `GaslessSwapTx` bundle in a block, attacker submits an ordinary, permissionless swap against the same underlying Uniswap-V2 pool used by the router (as set up in `tests/gasless_test.go` `setupLiquidity`), shifting reserves so the real swap output for the pending `minAmountOut` would now be insufficient.
3. Proposer includes `LendTx` (unconditional native-KAIA transfer to the gasless sender, per `getGaslessInfo`/`GetLendTxGenerator`, `kaiax/gasless/impl/getter.go:268-312`) followed by `GaslessSwapTx` calling `SwapForGas` (`contracts/bindings/kip247/GaslessSwapRouter.go:554-559`), which now reverts due to the manipulated reserves/slippage.
4. Result: `LendTx` succeeds and transfers real KAIA to the sender; `SwapForGas` reverts, meaning no repayment to the proposer occurs; the proposer is out the lent amount.

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

**File:** kaiax/gasless/impl/getter.go (L268-312)
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

**File:** kaiax/gasless/README.md (L29-36)
```markdown
### Block building rules

Upon detection of GaslessTxs, the following logics are executed:

- Per sender, if exists, GaslessApproveTx is relocated before GaslessSwapTx.
- LendTxGenerator is prepended before GaslessApproveTx.
- A new bundle is generated which contain either `[LendTxGenerator, GaslessApproveTx, GaslessSwapTx]` or `[LendTxGenerator, GaslessSwapTx]`
- If the bundle has conflict with any previous bundles, it is excluded from the returned bundle list.
```

**File:** contracts/bindings/kip247/GaslessSwapRouter.go (L554-559)
```go
// SwapForGas is a paid mutator transaction binding the contract method 0x80426901.
//
// Solidity: function swapForGas(address token, uint256 amountIn, uint256 minAmountOut, uint256 amountRepay, uint256 deadline) returns()
func (_GaslessSwapRouter *GaslessSwapRouterTransactor) SwapForGas(opts *bind.TransactOpts, token common.Address, amountIn *big.Int, minAmountOut *big.Int, amountRepay *big.Int, deadline *big.Int) (*types.Transaction, error) {
	return _GaslessSwapRouter.contract.Transact(opts, "swapForGas", token, amountIn, minAmountOut, amountRepay, deadline)
}
```

**File:** tests/gasless_test.go (L140-163)
```go
	setupLiquidity(t, owner, contracts, chain)

	/* ------------------------------------ Main test process ------------------------------------- */
	// In the test below, we swap 1 Token -> `amountsOut` WKAIA.
	swapAmmount := new(big.Int).Mul(big.NewInt(1), bigKaia)
	amountsOut, err := routerContract.GetAmountsOut(&bind.CallOpts{}, swapAmmount, []common.Address{testTokenAddr, wkaiaAddr})
	if err != nil {
		t.Fatal(err)
	}
	t.Logf("amountsOut: %s", amountsOut)

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
