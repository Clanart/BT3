### Title
Owner-Controlled `GaslessSwapRouter` Fee/Token Parameters Can Be Mutated Between TxPool Admission and Block Execution, Invalidating Gasless Bundle Settlement - (File: kaiax/gasless/impl/getter.go, kaiax/gasless/impl/tx_pool.go)

### Summary
The `kaiax/gasless` module validates a user's `GaslessSwapTx` (and its paired `GaslessApproveTx`) against **live, owner-mutable** state of the `GaslessSwapRouter` contract (`commissionRate`, allowed token list, exchange rate via `getAmountIn`) at tx-pool admission time, then bundles it with a `LendTxGenerator` whose repay amount is computed independently from fixed tx-fee fields. Because the router owner can call `updateCommissionRate`/`addToken`/`removeToken` with no lock, delay, or session-scoping tied to any specific pending gasless bundle, the parameters read during admission can differ from those in effect at actual execution — mirroring the reported bug class where privileged, unlocked global parameters read at two different points of a "session" (initiation vs settlement) can be changed mid-flight to break settlement integrity.

### Finding Description
The gasless flow works as follows:
- `kaiax/gasless/impl/tx_pool.go` `checkBalanceForSwap` reads the router's current on-chain state (via `bind.CallOpts` at the latest block) to validate `tx.amountIn >= gsr.getAmountIn(minAmountOut)` and to check the token/router allow-list, all governed by owner-controlled contract parameters such as `commissionRate` (`UpdateCommissionRate`, event `CommissionRateUpdated`) and the token list (`AddToken`/`removeToken`). [1](#0-0) 
- The allowed token/router set used for `IsApproveTx`/`IsSwapTx` checks and bundling is refreshed once per block from the live `GaslessSwapRouter`/Registry state, with no coupling to the specific lifetime of a given user's gasless session. [2](#0-1) [3](#0-2) 
- The `LendTxGenerator`'s lend/repay amounts are computed purely from the transactions' fixed gas-fee fields (`lendAmount`, `repayAmount`), independent of the router's current commission rate or exchange rate. [4](#0-3) 
- The router's `commissionRate` is an owner-mutable parameter with no timelock or block-height/epoch-gated activation (unlike `kaiax/gov` header-governance parameters, which only take effect at the next epoch boundary specifically to avoid this exact class of mid-flight manipulation). [5](#0-4) 

Because the bundling/admission-time check (`checkBalanceForSwap`) and the actual on-chain `swapForGas` execution are two temporally separate reads of the same owner-controlled parameter, an owner can call `updateCommissionRate` (or `addToken`/`removeToken`) after a user's `GaslessApproveTx`+`GaslessSwapTx` bundle has already been validated/bundled by the node but before it is included in a block. This is directly analogous to the reported Megapot issue: a privileged party can freely mutate a fee/exchange parameter that is read at two different times of the same logical "session" (admission vs settlement), with no lock preventing it, so the outcome computed at validation time no longer matches the outcome at settlement time.

### Impact Explanation
If the commission rate is raised (or a token is removed) between admission-time validation and execution:
- The `swapForGas` call executed on-chain may yield less native KAIA than required to cover `amountRepay` (the fixed sum of `LendTx.Fee() + ApproveTx.Fee() + SwapTx.Fee()`), causing the swap to revert at execution while the `LendTxGenerator`-advanced value has already been transferred to the sender ahead of the swap in the bundle ordering (`[LendTxGenerator, GaslessApproveTx, GaslessSwapTx]`). [6](#0-5) 
- This can cause a mismatch between what the lending CN advanced and what it can recoup, or cause otherwise-valid gasless bundles to be rejected/discarded mid-flight (denial of settlement for the gasless user), even though the bundle was admitted and built under different (now-stale) fee/allow-list parameters.
- This is a fee-delegation/gasless settlement integrity issue reachable purely by a public gasless-swap-transaction sender combined with the router owner's ordinary parameter-change transaction — no consensus-level or node-level privilege is required to trigger the divergence.

### Likelihood Explanation
The `GaslessSwapRouter` owner is expected to occasionally tune `commissionRate` and the token allow-list as normal operational behavior (there are dedicated `addToken`/`removeToken`/`updateCommissionRate` methods with events), so parameter changes are not a rare, adversarial-only event — they are a designed capability that lacks any mechanism to protect in-flight gasless sessions. Given Kaia's typical short block times, the window between tx-pool admission and inclusion is small but non-zero and is exactly the mechanism the original Megapot report flags: unlocked mutation of settlement-relevant parameters during an "active" user session.

### Recommendation
- Snapshot the router parameters (`commissionRate`, `getAmountIn` result, token allow-list membership) used at admission time inside the `GaslessSwapTx`/`GaslessApproveTx` bundle, and re-validate at inclusion time that the parameters have not changed; reject/re-bundle if they have, rather than allowing silent divergence.
- Alternatively, apply a governance-style delay to `updateCommissionRate`/`addToken`/`removeToken` (e.g., effective from the next block/epoch, similar to `kaiax/gov` header-governance parameter activation) so that any bundle admitted under the old parameters is guaranteed to execute under the same parameters.
- Ensure `LendTxGenerator` ordering and repayment logic can gracefully unwind (or is conditioned on-chain) if the swap fails due to a parameter change, so the lender is not exposed to value already advanced to the sender.

### Proof of Concept
Conceptual PoC (Go, mirroring the pattern of the reported PoCs against admin-mutable settlement parameters):
1. Deploy `GaslessSwapRouter` with `commissionRate = 0`; add `tokenAddr` as allowed with `factory`/`router`.
2. User signs `GaslessApproveTx` + `GaslessSwapTx(token, amountIn, minAmountOut, amountRepay, deadline)` sized so that at `commissionRate = 0`, `amountIn >= gsr.getAmountIn(minAmountOut)` holds — the bundle passes `checkBalanceForSwap` in `kaiax/gasless/impl/tx_pool.go` and is included by `ExtractTxBundles` in `kaiax/gasless/impl/builder.go`.
3. Before the block containing this bundle is sealed, the router owner submits `updateCommissionRate(higherRate)`.
4. At execution, the effective exchange rate is worse: the on-chain `swapForGas` call now yields less than `amountRepay`, causing the `SwapTx` (and dependent repay flow) to fail on-chain even though the `LendTxGenerator` had already advanced funds to the sender per the bundle ordering `[LendTxGenerator, GaslessApproveTx, GaslessSwapTx]`, demonstrating that the owner-controlled parameter change invalidates the previously-validated settlement outcome.

Note: I was unable to locate the raw `GaslessSwapRouter.sol` source in the indexed codebase (only the generated Go bindings in `contracts/bindings/kip247/GaslessSwapRouter.go` were available), so the exact on-chain revert/repay-enforcement behavior of `swapForGas` in the face of a mid-flight `commissionRate` change could not be fully verified from source and is inferred from the ABI/bindings and the `kaiax/gasless` module logic. If a full source review confirms `swapForGas` fully enforces `minAmountOut`/`amountRepay` atomically on-chain with no side effect prior to that check, the impact may be limited to bundle failure/DoS rather than fund loss; a Devin session with full repo access would be needed to confirm the exact Solidity implementation.

### Citations

**File:** kaiax/gasless/impl/tx_pool.go (L128-142)
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
	}
```

**File:** kaiax/gasless/impl/execution.go (L24-33)
```go
var _ kaiax.ExecutionModule = (*GaslessModule)(nil)

func (g *GaslessModule) PostInsertBlock(block *types.Block) error {
	currentState, err := g.Chain.StateAt(block.Header().Root)
	if err != nil {
		return err
	}
	g.setCurrentState(currentState)
	return g.updateAddresses(block.Header())
}
```

**File:** kaiax/gasless/impl/getter.go (L315-344)
```go
func (g *GaslessModule) updateAddresses(header *types.Header) error {
	g.gaslessInfoMu.Lock()
	defer g.gaslessInfoMu.Unlock()

	swapRouter, tokens, err := getGaslessInfo(g.Chain, header)
	// proceed even if there is something wrong with multicall contract
	if err != nil {
		g.swapRouter = common.Address{}
		g.allowedTokens = map[common.Address]bool{}
		logger.Warn("there is something wrong with multicall contract", "err", err.Error())
		return nil
	}

	g.swapRouter = swapRouter

	g.allowedTokens = map[common.Address]bool{}
	for _, addr := range tokens {
		// all tokens are allowed if nil
		if g.GaslessConfig.AllowedTokens == nil {
			g.allowedTokens[addr] = true
		}
		for _, allowed := range g.GaslessConfig.AllowedTokens {
			if addr == allowed {
				g.allowedTokens[addr] = true
			}
		}
	}

	return nil
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

**File:** contracts/bindings/kip247/GaslessSwapRouter.go (L596-608)
```go
// UpdateCommissionRate is a paid mutator transaction binding the contract method 0x00fa3d50.
//
// Solidity: function updateCommissionRate(uint256 _commissionRate) returns()
func (_GaslessSwapRouter *GaslessSwapRouterTransactor) UpdateCommissionRate(opts *bind.TransactOpts, _commissionRate *big.Int) (*types.Transaction, error) {
	return _GaslessSwapRouter.contract.Transact(opts, "updateCommissionRate", _commissionRate)
}

// UpdateCommissionRate is a paid mutator transaction binding the contract method 0x00fa3d50.
//
// Solidity: function updateCommissionRate(uint256 _commissionRate) returns()
func (_GaslessSwapRouter *GaslessSwapRouterSession) UpdateCommissionRate(_commissionRate *big.Int) (*types.Transaction, error) {
	return _GaslessSwapRouter.Contract.UpdateCommissionRate(&_GaslessSwapRouter.TransactOpts, _commissionRate)
}
```

**File:** kaiax/gasless/impl/builder.go (L38-52)
```go
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

```
