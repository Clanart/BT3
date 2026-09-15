### Title
Stale exchange-rate check in gasless swap admission allows the LendTx (proposer-fronted KAIA) to become under-collateralized when the token/KAIA price moves before block inclusion - (File: kaiax/gasless/impl/tx_pool.go)

### Summary
The Surge report describes a lending protocol where the collateral valuation used to admit new debt is checked at one point in time, but the debt is only actually repaid at a later, unsynchronized point, so a price move in between leaves the "lender" holding bad debt. The Gasless module in this repo has the same two-step, time-gapped structure: a proposer (CN) "lends" KAIA gas to a user by paying for the `SwapTx`, expecting to be repaid `AmountRepay` KAIA from the proceeds of swapping the user's ERC-20 `token` on a DEX via `GaslessSwapRouter.swapForGas`. The admission-time check only verifies that the *currently declared* `minAmountOut`/`amountIn` satisfy the router's *current* exchange rate — not the rate at actual execution time.

### Finding Description
`checkBalanceForSwap` validates a pending `SwapTx` using a live on-chain call to `GetAmountIn` at the moment the transaction is being considered for the tx pool: [1](#0-0) . This is the sole "collateral valuation" check protecting the CN's (proposer's) advanced KAIA (`AmountRepay = LendTx.Fee() ≈ approveTx.Fee() + swapTx.Fee()`), computed in `lendAmount`/`repayAmount`: [2](#0-1) .

The transaction only becomes "ready" for promotion/inclusion through `isSwapTxReady` → `IsExecutable`/`VerifyExecutable`, which re-checks nonce/token/amount consistency but does **not** re-verify the DEX exchange rate at promotion time or at actual block-building time: [3](#0-2) . The user-declared `MinAmountOut` and `AmountRepay` are fixed at signing time; the only enforcement is `minAmountOut >= amountRepay`, i.e., the user promises enough output to cover the CN's fee-advance, but whether the *actual* swap succeeds and returns at least `AmountRepay` in KAIA depends entirely on the DEX pool state, which will be re-evaluated on-chain by the `GaslessSwapRouter` contract itself at execution.

The core exposure mirrors the audit report's root cause: a check computed against a mutable market price (`GetAmountIn`/`GetAmountsOut` on a constant-product AMM) is performed well before the state that ultimately determines solvency is finalized — the CN's outlay (KAIA balance advanced for gas) is separated in time from the actual swap settlement that is supposed to make the CN whole. If the token price moves (large sell pressure, another swap front-running this one, or normal price drift while the tx sits in `queue`/`pending` for up to `QueueTimeout`/`PendingTimeout` = 10s each): [4](#0-3) , the contract-level `swapForGas` should revert on insufficient output rather than allow theft, but the CN has *already advanced KAIA gas* for the paired `LendTx`/`ApproveTx` via `GetLendTxGenerator` before the swap's success is known, since lending and swapping are two separate transactions/executions, not one atomic call from the CN's perspective. If the paired swap subsequently reverts or under-delivers, the CN's advanced KAIA is not recovered from that block, and the `AmountRepay` amount promised by a stale off-chain admission check does not correspond to guaranteed on-chain settlement.

### Impact Explanation
If the DEX price for the whitelisted token moves between admission-time checking and execution, and the block proposer has already advanced KAIA to cover `LendTx.Fee()`, the CN absorbs the loss of gas fees advanced for a swap that fails or returns less value than needed to repay it — a direct fee/fee-delegation abuse / unauthorized value movement pattern analogous to depositors bearing losses in the Surge pool when collateral value drops between "borrow" and "liquidate" checkpoints. This affects any Kaia CN/proposer that participates in the gasless fee-delegation flow and could be exploited by a malicious sender submitting swap transactions timed against price volatility of the whitelisted token, causing the proposer to under-collect its fee advance across a range of transactions network-wide (a systemic value-extraction vector, not a single isolated user loss), which is why I assess it as Medium/High rather than a resource-only issue.

### Likelihood Explanation
Likelihood is moderate: the gap between `checkBalanceForSwap`'s live-quote check and actual `swapForGas` execution is normally very short in a single block-proposal cycle, but `QueueTimeout`/`PendingTimeout` allow the transaction to sit up to 10 seconds, and cross-block delay is possible if the transaction is not immediately promoted. A user (or colluding third party) controlling the DEX pool for the whitelisted token (e.g., a low-liquidity token pair) can manipulate the price between the `checkBalanceForSwap` quote and inclusion to make the swap barely pass minAmountOut checks while still leaving insufficient proceeds to cover the CN's already-advanced gas, since `AmountRepay` is fixed by the user/attacker's own submitted values, not re-derived from final settlement.

### Recommendation
Re-validate the swap's expected output against the router's live exchange rate immediately before block inclusion (in the block-builder/execution path, not only at tx-pool admission), and/or make the KAIA advance (LendTx) conditional/atomic with the swap's success within the same block-building step so that a reverted or under-delivering swap cannot leave the CN's advance unresolved. Consider bounding allowed price staleness/slippage more strictly (e.g., re-quoting at the `IsReady`/promotion step, not just at `PreAddTx`), and monitor per-token/pool liquidity depth for the gasless whitelist to reduce manipulability of `GetAmountIn`.

### Proof of Concept
1. A CN whitelists a low-liquidity ERC-20 `token` in the Gasless module's `allowedTokens`.
2. A user submits `ApproveTx` + `SwapTx` (`swapForGas(token, amountIn, minAmountOut, amountRepay, deadline)`) where `minAmountOut` just barely satisfies `checkBalanceForSwap`'s live quote from `GetAmountIn`: [5](#0-4) .
3. Before the pair is promoted/executed (within the `QueueTimeout`/`PendingTimeout` window), the user (or a colluding actor) executes another swap on the same DEX pool that moves the token price against the pending swap.
4. When `swapForGas` finally executes on-chain, either it reverts (griefing the CN's advanced `LendTx.Fee()`) or returns exactly at the edge of `AmountRepay`, exploiting the staleness of the admission-time exchange-rate check versus true execution-time settlement — the analog of the Surge report's "exchange rate check disconnected from actual liquidation/repayment time" root cause.

Note: I was unable to fully trace the exact block-builder code path that finally sequences `LendTx` and `SwapTx` execution and whether `swapForGas`'s on-chain revert fully protects the CN's advance in all timing scenarios (e.g., `work/builder` package), since that code was not returned by my searches; this should be verified directly for a conclusive severity determination.

### Citations

**File:** kaiax/gasless/impl/tx_pool.go (L32-36)
```go
var (
	QueueTimeout   = 10 * time.Second // bundle tx is removed from txpool.queue after QueueTimeout
	PendingTimeout = 10 * time.Second // bundle tx is removed from txpool.pending after PendingTimeout
	KnownTxTimeout = 30 * time.Second // bundle tx is removed from knownTxs after KnownTxTimeout
)
```

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
