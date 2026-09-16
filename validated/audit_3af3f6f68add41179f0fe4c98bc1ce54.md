### Title
Gasless swap tx-pool admission check compares raw token amounts across mismatched decimal bases without normalization - (File: kaiax/gasless/impl/tx_pool.go)

### Summary
The Kipseli incident stems from a router blindly trusting an external quoter's raw integer output and treating it as a same-decimal-scale transfer amount without validating that the quoted amount corresponds to the actual output token's decimals. The Kaia gasless module's tx-pool admission logic performs an analogous unvalidated raw-integer comparison: it compares a caller-supplied `AmountIn` against a router-reported `requiredAmountIn` and a token's `balanceOf`/`allowance` values with no accounting for the fact that different ERC20 tokens registered via `--gasless.allowed-tokens` can have arbitrary, differing decimals.

### Finding Description
`checkBalanceForSwap` decodes a `swapForGas` transaction submitted by any unprivileged gasless-swap sender and validates it purely via raw integer comparisons: [1](#0-0) 

It calls the on-chain `GaslessSwapRouter.getAmountIn(token, minAmountOut)` (a view function on a token whitelisted only by address, `g.allowedTokens[args.Token]`) and directly compares the result to the sender-supplied `swapArgs.AmountIn` without any decimals normalization step: [2](#0-1) 

The `SwapArgs.Token` whitelist check (`isSwapTx`) validates only that the token address is on the allow-list — it performs no validation of the token's `decimals()`, nor any check that the router's internal quoting for that specific token matches the token's own decimal precision: [3](#0-2) 

This mirrors the Kipseli root cause precisely: a caller-controlled parameter (`token`, analogous to the arbitrary `tokenOut` path) is used to query an external pricing function (`getAmountIn`), and the raw returned integer is compared/used directly (`AmountIn`, `MinAmountOut`, `AmountRepay` — analogous to the mis-scaled transfer amount) without normalizing for the actual token's decimals. Because `GaslessSwapRouter`'s Solidity source is not present in this repository (only Go bindings exist), the actual on-chain quoting/decimals-handling logic cannot be verified from this codebase, but the reachable off-chain admission path in `kaiax/gasless/impl/tx_pool.go` performs no decimals-aware validation itself, and this check is only a tx-pool admission gate (best-effort, bypassable/optional depending on `BalanceCheckLevel` in `kaiax/gasless/config.go`) rather than a strict protocol invariant enforced at execution: [4](#0-3) 

### Impact Explanation
If an operator whitelists a non-18-decimal or otherwise decimal-inconsistent ERC20 token (e.g., a 6-decimal stablecoin) in `--gasless.allowed-tokens`, and the router's `getAmountIn`/quoting logic has any decimal-scale assumption mismatch with that token (as happened in the Kipseli exploit for an unsupported quoter path), a gasless swap sender could submit a `swapForGas` transaction with an `AmountIn` that passes the tx-pool's raw-integer sufficiency checks while being wildly under/over the economically correct amount. Because the gasless flow ultimately triggers a lending/repay transaction funded by the node/validator (see `GetLendTxGenerator` lending KAIA to the sender based on `amountRepay`), a decimal-mismatch-driven quote error could result in the sender draining more value from the swap/lending flow than intended, or the network paying out excess KAIA relative to the token actually swapped in. This is a fee/gasless-settlement value-movement risk, medium severity, since it depends on an operator whitelisting a token whose router pricing has decimal-handling defects (a configuration/deployment precondition), and the primary enforcement should be on-chain in `GaslessSwapRouter`, which this repo does not contain.

### Likelihood Explanation
Reachability is straightforward: any unprivileged account can submit a `swapForGas` (and optional `approve`) transaction pair through the public tx-pool; `IsModuleTx`/`GetCheckBalance` in `kaiax/gasless/impl/tx_pool.go` is invoked automatically for such transactions with no special privilege required. However, likelihood is capped by two factors this repo's context confirms: (1) the actual value-transfer/decimal logic lives in the (out-of-repo) `GaslessSwapRouter.sol` contract's `getAmountIn`/`swapForGas` — its correctness cannot be confirmed or refuted here — and (2) any given deployment's `allowedTokens` list is operator-controlled, so exposure only manifests if a decimals-inconsistent or exploit-prone token/quoter pairing is whitelisted, and only if `BalanceCheckLevelSwapAmount`/`ShouldCheckSwapAmount` gating is misconfigured to trust a flawed on-chain quote.

### Recommendation
Since `GaslessSwapRouter.sol` is not present in this repository, the concrete remediation must occur in that contract to ensure `getAmountIn`/`swapForGas` normalize amounts by each token's actual `decimals()` and validate that any external quoting mechanism used per-token returns values scaled to that token's decimals before using them as transfer/repay amounts. On the Kaia-node side, `checkBalanceForSwap` in `kaiax/gasless/impl/tx_pool.go` should additionally fetch and cross-check `token.decimals()` against the expected scale implied by `getAmountIn`'s result (e.g., sanity-bound the implied exchange rate) rather than trusting the router's raw integer output unconditionally, and the `allowedTokens` whitelisting process (`kaiax/gasless/config.go`) should require/verify per-token decimals metadata as part of onboarding a token for gasless swaps.

### Proof of Concept
Not fully constructible from this repository alone because `GaslessSwapRouter`'s Solidity implementation (where the actual decimal-scaling defect would need to exist, per the Kipseli pattern) is not included — only its Go bindings are present. Conceptually: an operator whitelists TokenX (6 decimals) in `--gasless.allowed-tokens`; a malicious/careless router deployment's `getAmountIn(TokenX, minAmountOut)` returns a value scaled as if TokenX had 18 decimals; the sender submits `swapForGas(TokenX, amountIn, minAmountOut, amountRepay, deadline)` with an `amountIn` that satisfies `checkBalanceForSwap`'s raw comparisons in `kaiax/gasless/impl/tx_pool.go` lines 128-141 despite being off by 12 orders of magnitude relative to the true required amount, then the gasless module's `GetLendTxGenerator` proceeds to lend KAIA against a mis-priced swap.

### Citations

**File:** kaiax/gasless/impl/tx_pool.go (L107-142)
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

	if g.GaslessConfig.ShouldCheckSenderCode() {
		if g.getCurrentHasCode(swapArgs.Sender) {
			return errors.New("sender with code is not allowed")
		}
	}

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

**File:** kaiax/gasless/config.go (L64-100)
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
