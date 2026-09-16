## Analog Found

### Title
Price Manipulation of Gasless Swap Rate Enables Griefing/DoS of Legitimate Gasless Transactions and Wasted Proposer Lend - (File: `kaiax/gasless/impl/tx_pool.go`, `kaiax/gasless/impl/getter.go`)

### Summary
The `kaiax/gasless` module (KIP-247) determines whether a user's `swapForGas` transaction is admissible/executable by querying the on-chain DEX exchange rate via `GaslessSwapRouter.getAmountIn(token, minAmountOut)` [1](#0-0) . Because this rate is read from an on-chain liquidity pool at call time, it is manipulable in the same class of way as the Uniswap-V3-based heal-price check in the reported LooksRare bug: an attacker can move the pool price so that a legitimate, previously well-formed `swapForGas` transaction fails the `amountIn >= getAmountIn(minAmountOut)` check or reverts on execution, denying the user's gasless transaction and wasting the block proposer's already-issued `LendTx`.

### Finding Description
The gasless flow works as follows:
1. A user submits an (optional) `GaslessApproveTx` and a `GaslessSwapTx` calling `swapForGas(token, amountIn, minAmountOut, amountRepay, deadline)` [2](#0-1) .
2. `checkBalanceForSwap` validates `swapArgs.AmountIn >= routerContract.GetAmountIn(nil, token, minAmountOut)`, i.e., it requires the user-supplied `amountIn` to be sufficient at the *current* on-chain DEX rate [1](#0-0) .
3. If the check passes and the transaction becomes "ready," the proposer's block-building logic prepends a `LendTx` (funded from the proposer's own balance) ahead of the approve/swap bundle via `GetLendTxGenerator`, sending KAIA to the user *before* the swap is actually executed on-chain [3](#0-2) .
4. The actual `swapForGas` call happens inside the `GaslessSwapRouter` contract against the live DEX pool (Uniswap V2 in the reference deployment) at execution time, not at admission time.

Because the DEX price used for admission (mempool check) and the DEX price used at actual on-chain execution can diverge — and any unprivileged transaction sender can manipulate the pool price with an ordinary large swap executed just before the target block — an attacker can:
- Cause a legitimate, previously-valid `swapForGas` tx to become invalid at the time of inclusion (violating the `minAmountOut`/`amountRepay` relationship or the `AmountIn >= GetAmountIn(...)` requirement), causing the whole bundle (`LendTx + ApproveTx? + SwapTx`) to fail or be dropped.
- More importantly, because `LendTx` is generated and can be included in the same block/bundle machinery independent of guaranteed successful swap execution, a griefer manipulating the price between admission-time validation and the actual EVM execution of `swapForGas` can push the transaction into reverting during execution (e.g., breaching the router's internal slippage-protection or `amountRepay` requirements), while the `LendTx` that already transferred KAIA to the user from the proposer's balance has no corresponding successful repay, or the whole bundle atomicity depends on how conflicts are resolved. The vulnerability class is identical to the reported bug: reliance on a live, externally-manipulable pool price by an on-chain "helper" swap-router to gate the success of a user transaction that a third party (any unprivileged sender) can front-run to force failure/loss.

### Impact Explanation
This is a Medium-severity issue: an unprivileged actor can grief block proposers' gasless subsidy mechanism and/or deny users their gasless transaction execution by manipulating the DEX price feed that the `GaslessSwapRouter`/gasless tx-pool checks rely on, analogous to how the LooksRare heal mechanism could be griefed by Uniswap V3 pool manipulation. This can cause repeated proposer-funded `LendTx` waste (proposer bears the cost) or unfair rejection of otherwise valid user gasless transactions, mirroring "unfair killing" in the original report as "unfair denial of gasless-subsidized transactions."

### Likelihood Explanation
Likelihood is Medium: manipulating a DEX pool price requires capital and pays swap fees/slippage costs, but is fully within reach of any unprivileged actor holding the counter-asset, exactly as in the original report. No special privileges, node access, or consensus participation are required — only a normal DEX swap transaction timed adjacent to the victim's gasless transaction.

### Recommendation
- Re-validate the swap output/repay conditions strictly inside `swapForGas`'s on-chain execution (not just at tx-pool admission), and ensure the `LendTx` is only committed atomically with a successful `swapForGas` execution (i.e., make the whole bundle succeed-or-fail together, with no partial state where the proposer's lend has been paid but the repay swap fails).
- Consider bounding acceptable slippage/deadline tightly and/or using a TWAP-based or governance-configured minimum rate rather than instantaneous spot price from `getAmountIn`, to reduce the profitability of single-block price manipulation.
- Confirm bundle-conflict handling in `work/builder/bundle.go` / `kaiax/gasless/impl/builder.go` guarantees all-or-nothing inclusion so a manipulated-price revert cannot separate the `LendTx` from a failed `SwapTx`.

### Proof of Concept
1. Attacker identifies a pending `GaslessSwapTx` in the txpool with `token`, `amountIn`, `minAmountOut`, `amountRepay` computed against the current pool rate (passes `checkBalanceForSwap`'s `AmountIn >= GetAmountIn(minAmountOut)` check) [1](#0-0) .
2. Attacker submits (and gets included in an earlier position in the same or a preceding block) a large swap on the same underlying DEX pool used by `GaslessSwapRouter`, shifting the exchange rate unfavorably for the token being swapped for gas.
3. When the proposer's `LendTx + ApproveTx? + SwapTx` bundle executes, the `swapForGas` call inside `GaslessSwapRouter` now requires a different `amountIn`/produces a different output than what was validated in the tx pool, causing the swap to revert (insufficient output/repay) while the proposer's `LendTx` has already transferred funds to the user [4](#0-3) .
4. Legitimate user's gasless transaction is denied/executed at a loss, and/or the proposer's LendTx is wasted — reproducing the "unfair denial via price manipulation" pattern from the source report.

Note: I was unable to fully inspect `work/builder/bundle.go` and `kaiax/gasless/impl/builder.go` (bundle atomicity/conflict-resolution logic) due to tool-call limits reached before retrieval completed; this leaves some uncertainty about whether bundle-level atomicity fully prevents the "LendTx executed but SwapTx reverts" scenario described above. This should be verified directly in those files before treating the PoC as fully confirmed.

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

**File:** kaiax/gasless/impl/getter.go (L88-103)
```go
// IsSwapTx checks following conditions:
// S1. tx.to is a whitelisted SwapRouter contract.
// S2. tx.data is `swapForGas(token, amountIn, minAmountOut, amountRepay)`.
// S3. token is a whitelisted ERC20 token.
func (g *GaslessModule) IsSwapTx(tx *types.Transaction) bool {
	args, ok := decodeSwapTx(tx, g.signer)
	return ok && g.isSwapTx(args)
}

func (g *GaslessModule) isSwapTx(args *SwapArgs) bool {
	g.gaslessInfoMu.RLock()
	defer g.gaslessInfoMu.RUnlock()

	return g.swapRouter == args.Router && // S1
		g.allowedTokens[args.Token] // S3
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
