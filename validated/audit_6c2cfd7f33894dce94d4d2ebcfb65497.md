Confirmed: the default `BalanceCheckLevel` is `BalanceCheckLevelAll`, so the swap-amount check via `GetAmountIn` (an on-chain AMM price query) is active by default in the mempool admission path.

### Title
DOS of legitimate GaslessSwapTx admission via AMM spot-price manipulation in `checkBalanceForSwap` - (File: kaiax/gasless/impl/tx_pool.go)

### Summary
The gasless tx-pool admission check `checkBalanceForSwap` validates a submitted `GaslessSwapTx` by comparing its declared `AmountIn` against a "required" amount computed live from the `GaslessSwapRouter`'s `GetAmountIn(token, minAmountOut)` call, which reads the current on-chain AMM pool reserves/spot price. Because this spot price is trivially manipulable by any unprivileged party who can trade against the same pool, an attacker can transiently skew the reserves so that `GetAmountIn` returns an inflated `requiredAmountIn`, causing an honestly-constructed `GaslessSwapTx` to be rejected from the transaction pool. This mirrors the reported bug class: a critical function relying on a live, unauthenticated spot-price read for a hard "revert" check, letting anyone DOS the function by manipulating that price.

### Finding Description
`checkBalanceForSwap` enforces:
```
requiredAmountIn = routerContract.GetAmountIn(nil, token, minAmountOut)
if swapArgs.AmountIn < requiredAmountIn { return error("insufficient amountIn") }
``` [1](#0-0) 

`GetAmountIn` is a router call that (per the AMM design implied by `IsSwapTx`/`SwapForGas` and the bundled Uniswap-V2-style router/pair bindings) derives the amount from current pool reserves — i.e., a live spot price, exactly analogous to the Balancer `getPoolTokens`/`_calculateStableMathSpotPrice` values referenced in the external report. This check is invoked from `GetCheckBalance()`, which is called unconditionally inside `TxPool.validateTx` for every transaction the gasless module recognizes as a module tx (`IsModuleTx` → `IsSwapTx`), and this validation gates whether the transaction is admitted to the pool and promoted: [2](#0-1) [3](#0-2) 

The default configuration (`BalanceCheckLevelAll`) enables this swap-amount check by default, so it is on the standard admission path for gasless swaps: [4](#0-3) 

An attacker (any address able to trade in the pool referenced by `GaslessSwapRouter`/whitelisted token pair — an ordinary, unprivileged trader, i.e., a "public-RPC caller"/trader reachable persona) can submit a swap immediately before (or repeatedly, to persist the skew across the mempool re-check window) the victim's `GaslessSwapTx` propagates, moving reserves so `GetAmountIn(token, minAmountOut)` spikes above the victim's `AmountIn`. The victim's legitimate, correctly-priced-at-submission-time gasless swap is then rejected with "insufficient amountIn" and dropped from/never admitted to the pool, even though it would have succeeded under the true, unmanipulated market price.

### Impact Explanation
This denies gasless-swap service (a core KIP-247 feature) to legitimate users on demand, at the discretion of any party who can move the reference pool's reserves. Because the check is re-evaluated at pool insertion/promotion time using live state rather than the state at which the user's `minAmountOut`/`amountRepay` parameters were computed, it is trivially and cheaply defeated by transient reserve manipulation, causing repeated, targeted rejection of otherwise-valid GaslessSwapTx submissions. This is a persistent availability/DOS issue on the gasless transaction pipeline, directly analogous in root cause to the reported `reinvestReward` DOS (an unauthenticated, live spot-price read gating a hard revert on a function reachable by ordinary users).

### Likelihood Explanation
Likelihood is high: manipulating AMM reserves via a large adjacent swap is a well-known, cheap MEV technique, requires no special privilege, and can be repeated for as long as the attacker wishes to target a specific victim's pending gasless swap, since the check is re-run whenever `validateTx`/`checkBalanceForSwap` executes for that tx (e.g., on resubmission, re-broadcast, or promotion checks).

### Recommendation
Avoid gating hard rejection/removal from the pool on a live, manipulable spot-price read. Options include: (1) using a bounded/TWAP-style price or tolerating a slippage buffer instead of an exact live `GetAmountIn` comparison, (2) deferring the amountIn sufficiency check to block-execution time (where the swap's own `minAmountOut`/deadline already protect the user and proposer) rather than mempool admission, or (3) rate-limiting/caching the check so a single-block reserve manipulation cannot repeatedly evict a legitimately priced pending transaction.

### Proof of Concept
1. Attacker identifies a pending `GaslessSwapTx` (visible in the public mempool) with `token`, `minAmountOut`, and `AmountIn` computed against the pool's price at submission time.
2. Attacker submits a large swap against the same pool (via the router/pair used by `GaslessSwapRouter.GetAmountIn`) to shift reserves such that `GetAmountIn(token, minAmountOut)` now returns a value greater than the victim's `AmountIn`.
3. When the node (re-)runs `TxPool.validateTx` → `GaslessModule.GetCheckBalance()` → `checkBalanceForSwap` for the victim's tx [1](#0-0) , the check fails with `"insufficient amountIn"`, and the victim's transaction is rejected/never promoted, despite having been valid under true market conditions.
4. Attacker can repeat this before every retry, permanently denying that user's gasless swap.

### Citations

**File:** kaiax/gasless/impl/tx_pool.go (L62-72)
```go
func (g *GaslessModule) GetCheckBalance() func(tx *types.Transaction) error {
	return func(tx *types.Transaction) error {
		if approveArgs, ok := decodeApproveTx(tx, g.signer); ok {
			return g.checkBalanceForApprove(approveArgs)
		}
		if swapArgs, ok := decodeSwapTx(tx, g.signer); ok {
			return g.checkBalanceForSwap(swapArgs, tx.Nonce())
		}
		return errors.New("not a gasless transaction") // should not happen because IsModuleTx is called before GetCheckBalance
	}
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

**File:** blockchain/tx_pool.go (L918-932)
```go
	// If module recognizes the tx, run an alternative balance check and then skip the default balance check later.
	shouldSkipBalanceCheck := false
	for _, module := range pool.modules {
		if module.IsModuleTx(tx) {
			if checkBalance := module.GetCheckBalance(); checkBalance != nil {
				shouldSkipBalanceCheck = true
				err := checkBalance(tx)
				if err != nil {
					logger.Trace("[tx_pool] invalid funds of module transaction sender", "from", from, "txhash", tx.Hash().Hex())
					return err
				}
			}
			break
		}
	}
```

**File:** kaiax/gasless/config.go (L64-88)
```go
const (
	BalanceCheckLevelStatic                   = iota // relation between amounts and deadline
	BalanceCheckLevelTokenBalanceAndAllowance        // all above + token balance and allowance
	BalanceCheckLevelSwapAmount                      // all above +	amountIn calculated by dex
	BalanceCheckLevelAll                             // all above +	sender code check
)

type GaslessConfig struct {
	// all tokens are allowed if AllowedTokens is nil while all are disallowed if empty slice
	AllowedTokens         []common.Address `toml:",omitempty"`
	Disable               bool
	MaxBundleTxsInPending uint
	MaxBundleTxsInQueue   uint
	BalanceCheckLevel     int
}

func DefaultGaslessConfig() *GaslessConfig {
	return &GaslessConfig{
		AllowedTokens:         nil,
		Disable:               false,
		MaxBundleTxsInPending: 100,
		MaxBundleTxsInQueue:   200,
		BalanceCheckLevel:     BalanceCheckLevelAll,
	}
}
```
