### Title
Gasless swap funds can be drained when `BalanceCheckLevel` disables amount/balance validation - (File: kaiax/gasless/config.go, kaiax/gasless/impl/tx_pool.go)

### Summary
The `kaiax/gasless` module gates critical value-validation checks for `GaslessSwapTx` behind the operator-configurable `GaslessConfig.BalanceCheckLevel` parameter, in the same way the Splits `UniV3OracleImpl` gated pricing behind the admin-settable `defaultScaledOfferFactor`. If an operator runs with a `BalanceCheckLevel` below `BalanceCheckLevelSwapAmount` (or below `BalanceCheckLevelTokenBalanceAndAllowance`), the mempool never validates that the swap sender actually owns/approves the tokens or that `amountIn` matches the real DEX exchange rate before the block-proposer's `LendTx` unconditionally transfers real KAIA to the sender.

### Finding Description
`checkBalanceForSwap` performs the "tx.amountIn >= gsr.getAmountIn(minAmountOut)" (real exchange-rate) check only `if g.GaslessConfig.ShouldCheckSwapAmount()`, and the token balance/allowance checks only `if g.GaslessConfig.ShouldCheckToken()`: [1](#0-0) [2](#0-1) 

These predicates are derived purely from the operator-settable `BalanceCheckLevel` integer: [3](#0-2) 

Just as the original report's `_getQuoteAmount` fell back to an admin-controlled `defaultScaledOfferFactor` that, if zero, let a trader take all base-token funds while paying nothing, here the amount/balance validation that ensures a `GaslessSwapTx` sender actually possesses the funds to repay the proposer's lent gas is entirely skippable via a single config value. Once the tx-pool promotes a `GaslessSwapTx` without validating balance or exchange-rate-correct `amountIn`, the block-building path in `ExtractTxBundles` still prepends a `LendTxGenerator` that unconditionally sends the sender the gas-fee amount computed from `lendAmount()`: [4](#0-3) [5](#0-4) 

The README confirms that gasless's whole security model depends on the module's own balance checks, not on-chain enforcement, to guarantee repayment: "Sender balance check is omitted for gasless transactions (see `GetCheckBalance()`)" and the block-building bundle simply prepends `LendTxGenerator`: [6](#0-5) 

### Impact Explanation
If `BalanceCheckLevel` is configured below `BalanceCheckLevelSwapAmount`/`BalanceCheckLevelTokenBalanceAndAllowance` (a legitimate, node-operator-controlled configuration exposed via `--gasless.balance-check-level`), any unprivileged gasless-swap sender can craft a `GaslessSwapTx` whose declared `amountIn`/`minAmountOut` are not backed by any real token balance or correct DEX exchange rate. The transaction pool will still admit and bundle it with a real-value `LendTx` funded by the block proposer's node key, meaning value (proposer-funded KAIA) is transferred out based on unchecked, attacker-supplied numbers — a direct analog of "funds can be completely drained" from the report, where disabling a single scaling/validation admin parameter converts a priced trade into a free one.

### Likelihood Explanation
This requires an operator to run with a non-default `BalanceCheckLevel` (default is `BalanceCheckLevelAll`, the safest setting) — analogous to the original bug requiring the Swapper owner to set `defaultScaledOfferFactor = 0`. Per Sherlock's precedent cited in the source report, such admin-input issues are still valid when the resulting impact is severe (complete fund drain), since real deployments may intentionally lower the check level for performance and unknowingly expose this drain vector. I was not able to fully verify from the available code whether the `LendTx`+`SwapTx` bundle is atomically reverted together if the `SwapTx` later fails on-chain (this would require inspecting the bundle execution/atomicity logic in `work/builder.go` and `work/worker.go`, which I did not have iterations remaining to trace) — if bundle atomicity guarantees rollback of `LendTx` whenever `SwapTx` reverts, the practical drain is reduced to wasted proposer gas rather than direct fund loss, so this should be confirmed before treating it as a firm finding.

### Recommendation
Do not allow `BalanceCheckLevel` to fully disable the swap-amount and token-balance/allowance checks, or enforce a minimum safe floor (e.g., always require `ShouldCheckSwapAmount()` and `ShouldCheckToken()` regardless of configuration) so that the proposer's `LendTx` can never be generated for a swap that is not backed by real, correctly-priced token collateral. Additionally, verify/guarantee that `LendTx` and its paired `GaslessSwapTx` execute as an atomic, revert-together bundle at the block-building layer so a failing swap can never leave an unrepaid `LendTx` on-chain.

### Proof of Concept
1. Node operator starts a CN with `--gasless.balance-check-level=0` (or `1`), i.e., below `BalanceCheckLevelSwapAmount`, per `BalanceCheckLevelFlag`: [7](#0-6) 
2. Attacker crafts a `GaslessSwapTx` calling `swapForGas(token, amountIn, minAmountOut, amountRepay, deadline)` with `minAmountOut >= amountRepay` (the only always-enforced static check) but with `amountIn` far below the real amount the DEX would require, and without ever holding/approving `token`.
3. `checkBalanceForSwap` skips the `ShouldCheckSwapAmount()`/`ShouldCheckToken()` branches since they are config-gated: [1](#0-0) 
4. The transaction is promoted and bundled with an unconditional `LendTxGenerator` transfer of `lendAmount()` KAIA from the proposer's key to the attacker: [5](#0-4) 
5. Attacker receives real KAIA from the proposer while the paired swap either fails to repay correctly or is trivially satisfiable, resulting in a net drain from the proposer/system, mirroring the original report's zero-cost trade drain pattern.

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

**File:** kaiax/gasless/impl/tx_pool.go (L144-173)
```go
	if g.GaslessConfig.ShouldCheckToken() {

		tokenContract, err := sc_erc20.NewERC20(token, bc)
		if err != nil {
			return err
		}

		// If SwapTx.nonce is the sender's next nonce, then there is no room for ApproveTx proceeding SwapTx.
		senderNonce := g.getCurrentStateNonce(swapArgs.Sender)
		noApproveTxPreceeds := swapNonce == senderNonce
		if noApproveTxPreceeds {
			// tx.token.allowance(sender, router) >= tx.amountIn
			approval, err := tokenContract.Allowance(nil, swapArgs.Sender, swapRouter)
			if err != nil {
				return err
			}
			if approval.Cmp(swapArgs.AmountIn) < 0 {
				return fmt.Errorf("insufficient approval: approval=%s, want=%s", approval.String(), swapArgs.AmountIn.String())
			}
		}

		// tx.token.balanceOf(sender) >= tx.amountIn
		balance, err := tokenContract.BalanceOf(nil, swapArgs.Sender)
		if err != nil {
			return err
		}
		if balance.Cmp(swapArgs.AmountIn) < 0 {
			return fmt.Errorf("insufficient balance: balance=%s, want=%s", balance.String(), swapArgs.AmountIn.String())
		}
	}
```

**File:** kaiax/gasless/config.go (L55-61)
```go
	BalanceCheckLevelFlag = &cli.IntFlag{
		Name:     "gasless.balance-check-level",
		Usage:    "balance check level: 0=static checks, 1=token balance and allowance, 2=swap amount, 3=all",
		Value:    BalanceCheckLevelAll,
		Aliases:  []string{"kaiax.module.gasless.balance-check-level"},
		Category: "KAIAX",
	}
```

**File:** kaiax/gasless/config.go (L90-100)
```go
func (cfg *GaslessConfig) ShouldCheckToken() bool {
	return cfg.BalanceCheckLevel >= BalanceCheckLevelTokenBalanceAndAllowance
}

func (cfg *GaslessConfig) ShouldCheckSwapAmount() bool {
	return cfg.BalanceCheckLevel >= BalanceCheckLevelSwapAmount
}

func (cfg *GaslessConfig) ShouldCheckSenderCode() bool {
	return cfg.BalanceCheckLevel >= BalanceCheckLevelAll
}
```

**File:** kaiax/gasless/impl/builder.go (L28-51)
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

**File:** kaiax/gasless/README.md (L25-36)
```markdown
#### Balance check

Sender balance check is omitted for gasless transactions (see `GetCheckBalance()`).

### Block building rules

Upon detection of GaslessTxs, the following logics are executed:

- Per sender, if exists, GaslessApproveTx is relocated before GaslessSwapTx.
- LendTxGenerator is prepended before GaslessApproveTx.
- A new bundle is generated which contain either `[LendTxGenerator, GaslessApproveTx, GaslessSwapTx]` or `[LendTxGenerator, GaslessSwapTx]`
- If the bundle has conflict with any previous bundles, it is excluded from the returned bundle list.
```
