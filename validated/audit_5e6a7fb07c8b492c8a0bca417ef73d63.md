### Title
Gasless swap price manipulation causes uncompensated proposer fund loss - ([File: kaiax/gasless/impl/tx_pool.go])

### Summary
The `kaiax/gasless` module (KIP-247) lets a block proposer front native KAIA to a token-holding user via an unconditional `LendTx`, expecting the bundled `GaslessSwapTx` to repay the lent amount out of DEX swap proceeds. The swap-amount validation (`GetAmountIn` against current-block DEX reserves) is only performed once, at mempool admission time, and the bundle-building step never re-simulates the transactions for success before proposing the block [1](#0-0) . Because `LendTx`, `ApproveTx`, and `SwapTx` are three independent EVM transactions bundled only for ordering purposes (not atomically reverted as one unit) [2](#0-1) , an attacker can manipulate the DEX pool price between admission-time validation and block-inclusion-time execution so that the `SwapTx`'s on-chain slippage check fails and it reverts, while the `LendTx` unconditionally pays out KAIA to the attacker's account with no repayment ever occurring — a fund-loss vector directly analogous to the Moola Market price-manipulation exploit, where a manipulated exchange rate was exploited to extract value that the protocol never intended to release.

### Finding Description
`GaslessModule.GetLendTxGenerator` builds a plain value-transfer transaction (`LendTx`) that sends `lendAmount(approveTxOrNil, swapTx)` KAIA directly to the sender's address [3](#0-2) . This amount equals the sum of the approve/swap transactions' `Fee()`, i.e. gas the proposer is fronting so the sender can pay for its own transactions [4](#0-3) .

Repayment is expected to occur inside `GaslessSwapTx`, which calls `swapForGas(token, amountIn, minAmountOut, amountRepay, deadline)` on the `GaslessSwapRouter` contract. The mempool-admission check `checkBalanceForSwap` validates that `swapArgs.AmountIn >= gsr.GetAmountIn(token, minAmountOut)` using the DEX reserves *at the moment the check runs* [1](#0-0) . This is a spot-price check against a manipulable on-chain reserve (Uniswap-V2 style AMM), not a TWAP or any manipulation-resistant oracle — the same bug class as Moola Market's price-manipulation exploit.

`ExtractTxBundles` groups `[LendTxGenerator, ApproveTx?, SwapTx]` into a `builder.Bundle` purely to preserve execution order and only rejects bundles that structurally *conflict* with other bundles; it performs no re-validation of `GetAmountIn`/slippage against fresh state and no simulated dry-run to confirm the `SwapTx` will succeed before including the `LendTx` [2](#0-1) . Consequently:

1. The `LendTx` (a normal value transfer) unconditionally succeeds and moves proposer-funded KAIA to the sender.
2. If the pool price has moved (e.g., because the same attacker or anyone else executed a trade on the same DEX pair earlier in the same block, or across blocks between admission and inclusion) such that the actual swap output would fall below the user-declared `minAmountOut`, `swapForGas` will revert on its own slippage check.
3. A reverted `SwapTx` does not roll back the already-executed `LendTx` — Kaia (like all EVM chains) does not provide cross-transaction atomicity for independently-included transactions in the same block.
4. The proposer has now transferred value to the attacker's address with zero repayment, and no on-chain mechanism forces the attacker to return it.

### Impact Explanation
This allows any unprivileged gasless-transaction sender to extract native KAIA from the block proposer with no repayment, by exploiting the gap between spot-price validation (mempool time) and spot-price-dependent execution (block-inclusion time). Because the DEX price itself can be moved by the attacker's own transactions (or coincidental market activity) within the applicable window, this is squarely a price-manipulation-class vulnerability causing concrete unauthorized value movement from proposers to an unprivileged attacker, matching the "fee/fee-delegation abuse" and "gasless settlement theft" impact categories in scope.

### Likelihood Explanation
Exploitation only requires being an ordinary gasless-tx sender with a token balance and the ability to submit a second transaction (or coordinate with pool activity) that shifts the DEX reserves used by `GetAmountIn`/`swapForGas` between admission and inclusion. No validator, governance, or privileged role is required — it is reachable purely through the public transaction-submission and gasless swap paths, and the `ShouldCheckSwapAmount()` admission check is explicitly stated to only run once at pool-admission time with no re-check before block building.

### Recommendation
- Re-validate `GetAmountIn`/expected swap output against the state immediately preceding block inclusion (i.e., simulate the bundle) before including the `LendTxGenerator`, and drop bundles whose simulated `SwapTx` would revert.
- Consider making `LendTx` conditional on successful repayment within the same atomic bundle (e.g., via a system-level bundle-revert-together mechanism), or clawing back lent funds if the paired `SwapTx` fails.
- Use a manipulation-resistant price reference (e.g., TWAP or a bounded maximum price-impact check) for `GetAmountIn`, rather than raw current-block reserves.

### Proof of Concept
1. Attacker holds `token` and calls the gasless swap flow, submitting `GaslessApproveTx` + `GaslessSwapTx` with `amountIn`, `minAmountOut` computed to just satisfy `checkBalanceForSwap`'s admission-time `GetAmountIn` check [1](#0-0) .
2. Attacker (or an accomplice) executes a large swap against the same DEX pool used by `GaslessSwapRouter`, shifting the pool reserves so that the actual on-chain amount-out for the pending `GaslessSwapTx` at execution time falls below its declared `minAmountOut`.
3. The proposer's node builds a bundle `[LendTx, ApproveTx, SwapTx]` via `ExtractTxBundles` without re-simulating the swap outcome [2](#0-1) ; `LendTx` unconditionally transfers KAIA to the attacker.
4. `SwapTx` executes and reverts inside `swapForGas` due to the slippage check, so no repayment reaches the proposer, while the attacker retains the `LendTx`-funded KAIA.

Note: The `GaslessSwapRouter.sol` source (the exact `require`/slippage-revert logic and repayment recipient) was not available in the indexed codebase — only its compiled Go bindings [5](#0-4)  could be inspected, so the precise revert conditions inside `swapForGas` could not be fully confirmed from source. Confirming exact repayment/revert semantics would require reviewing the actual Solidity source, which is outside the indexed content available to this analysis.

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

**File:** kaiax/gasless/impl/builder.go (L28-71)
```go
func (g *GaslessModule) ExtractTxBundles(txs []*types.Transaction, prevBundles []*builder.Bundle) []*builder.Bundle {
	// there are only at most two gasless transactions in pending for a sender
	bundles := []*builder.Bundle{}
	approveTxs := map[common.Address]*types.Transaction{}
	targetTxHash := common.Hash{}
	for _, tx := range txs {
		addr, err := types.Sender(g.signer, tx)
		if err != nil {
			continue
		}
		if g.IsApproveTx(tx) {
			approveTxs[addr] = tx
		} else if g.IsSwapTx(tx) && g.IsExecutable(approveTxs[addr], tx) {
			bundleTxs := builder.NewTxOrGenList(g.GetLendTxGenerator(approveTxs[addr], tx))
			if approveTxs[addr] != nil {
				bundleTxs = append(bundleTxs, builder.NewTxOrGenFromTx(approveTxs[addr]))
			}
			bundleTxs = append(bundleTxs, builder.NewTxOrGenFromTx(tx))

			b := builder.NewBundle(
				bundleTxs,
				targetTxHash,
				false,
			)

			targetTxHash = tx.Hash()

			isConflict := false
			for _, prev := range append(prevBundles, bundles...) {
				if prev.IsConflict(b) {
					isConflict = true
					break
				}
			}
			if isConflict {
				// Gasless transactions will just fail even if they aren't bundled.
				continue
			}
			bundles = append(bundles, b)
		} else {
			targetTxHash = tx.Hash()
		}
	}
	return bundles
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

**File:** contracts/bindings/kip247/GaslessSwapRouter.go (L566-573)
```go
}

// SwapForGas is a paid mutator transaction binding the contract method 0x80426901.
//
// Solidity: function swapForGas(address token, uint256 amountIn, uint256 minAmountOut, uint256 amountRepay, uint256 deadline) returns()
func (_GaslessSwapRouter *GaslessSwapRouterTransactorSession) SwapForGas(token common.Address, amountIn *big.Int, minAmountOut *big.Int, amountRepay *big.Int, deadline *big.Int) (*types.Transaction, error) {
	return _GaslessSwapRouter.Contract.SwapForGas(&_GaslessSwapRouter.TransactOpts, token, amountIn, minAmountOut, amountRepay, deadline)
}
```
