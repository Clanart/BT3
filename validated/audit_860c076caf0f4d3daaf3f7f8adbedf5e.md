### Title
Node-operator configurable `BalanceCheckLevel` allows admission of gasless swap bundles without on-chain exchange-rate/repayment validation - (File: kaiax/gasless/impl/tx_pool.go)

### Summary
The reported Masset bug is a "governor-trust" pattern: a critical economic invariant (collateral ratio ≥ 100%) is not enforced in code, but only assumed to be maintained correctly by a trusted administrator, and if the administrator misconfigures it, disproportionate value can be extracted. The Kaia `kaiax/gasless` module has an analogous pattern: whether a gasless swap transaction's declared `amountIn`/`minAmountOut` is actually consistent with the live DEX exchange rate is gated entirely behind an operator-configurable `BalanceCheckLevel` parameter, not by any protocol-level or execution-time invariant enforced for all nodes.

### Finding Description
`GaslessConfig.BalanceCheckLevel` is a per-node CLI/toml setting (`gasless.balance-check-level`, default `BalanceCheckLevelAll`) that gates which admission checks are performed on `GaslessSwapTx`: [1](#0-0) 

In `checkBalanceForSwap`, the check that ties the declared `amountIn`/`minAmountOut` to the router's actual quoted exchange rate (`gsr.getAmountIn(minAmountOut)`) and the checks that the sender actually has the token balance/allowance to perform the swap are conditionally executed only `if g.GaslessConfig.ShouldCheckSwapAmount()` / `ShouldCheckToken()`: [2](#0-1) 

If a node operator (analogous to the "governor" in the report) sets `BalanceCheckLevel` below `BalanceCheckLevelSwapAmount` (e.g. `BalanceCheckLevelStatic`, which is exercised in `TestPromoteGaslessTxsWithMultiSenders`), the module will promote/bundle `GaslessSwapTx` transactions into blocks with only the static `minAmountOut >= amountRepay` and deadline checks performed—`gsr.getAmountIn`, token balance and allowance are never checked before promotion: [3](#0-2) 

Just as `Masset` relies entirely on the governor to keep `colRatio` at/above 100% (with no explicit code-level floor), the gasless module relies entirely on the node/proposer's local configuration to keep meaningful economic checks enabled, with no protocol-level lower bound and no consensus-level re-validation of these specific checks at execution time in this module's admission path.

### Impact Explanation
When `BalanceCheckLevel` is lowered by an operator/proposer, the mempool/bundle-building logic (`GetCheckBalance`, `IsReady`, block-building bundling described in `kaiax/gasless/README.md`) will admit and bundle `[LendTxGenerator, GaslessApproveTx, GaslessSwapTx]` triples where the token amountIn is not verified against the DEX's real exchange rate or against the sender's actual balance/allowance before the proposer's `LendTxGenerator` fronts the gas fee (`R1+R2+R3`) on the sender's behalf. Because the CN lender fee-advance happens unconditionally as part of the bundle regardless of whether the eventual on-chain swap succeeds, a sender can craft a bundle that is optimistically admitted, causing the lending proposer to advance real KAIA gas funds that are not reliably recoverable if the swap subsequently reverts or under-repays, since the guarantee that `amountIn` is sufficient given real market rates was the very check disabled. This is a fee-delegation-abuse / unauthorized value movement vector reachable by any unprivileged transaction sender against any proposer running with a relaxed configuration.

### Likelihood Explanation
Likelihood is moderate: it requires a proposer/CN operator to run with `BalanceCheckLevel` below the default (`BalanceCheckLevelSwapAmount`/`BalanceCheckLevelAll`), which is a deliberate configuration choice rather than a common default. However, unlike the Masset case (where the governor is presumed to be careful because a bad value causes obvious insolvency), there is nothing in the code that flags or bounds an unsafe `BalanceCheckLevel`, and the flag is fully exposed via standard CLI configuration (`cmd/utils/config.go`, `cmd/homi/setup/cmd.go`), making misconfiguration by a well-intentioned but uninformed operator plausible, and then trivially and repeatedly exploitable by any sender submitting a swap bundle once discovered.

### Recommendation
Do not allow `ShouldCheckSwapAmount()`/`ShouldCheckToken()`-equivalent economic validation to be fully disabled by local configuration for `GaslessSwapTx` promotion; enforce the exchange-rate and balance/allowance checks unconditionally (or floor `BalanceCheckLevel` at `BalanceCheckLevelSwapAmount`) regardless of operator configuration, mirroring the recommendation to explicitly enforce the ≥100% collateralisation invariant in code rather than relying on trusted-party configuration.

### Proof of Concept
1. Start (or point to) a Kaia CN/proposer node configured with `--gasless.balance-check-level=0` (`BalanceCheckLevelStatic`), as demonstrated in `TestPromoteGaslessTxsWithMultiSenders` (`gaslessConfig.BalanceCheckLevel = gasless.BalanceCheckLevelStatic`) [4](#0-3) .
2. As an unprivileged sender, submit a `GaslessApproveTx` + `GaslessSwapTx` pair where `amountIn` is deliberately insufficient relative to the router's real `getAmountIn(minAmountOut)` quote, and/or the sender lacks sufficient token balance/allowance.
3. Because `ShouldCheckSwapAmount()` and `ShouldCheckToken()` return `false` at this configuration level, `checkBalanceForSwap` in `kaiax/gasless/impl/tx_pool.go` skips lines 128-173 and only validates `minAmountOut >= amountRepay` and the deadline.
4. The transaction pair is admitted to the pool and bundled with a `LendTxGenerator` fee-advance per the flow in `kaiax/gasless/README.md`; the proposer advances gas funds for a swap whose profitability/repayment was never validated against real market conditions or sender solvency, exposing the proposer to fee-delegation loss when the on-chain swap fails to yield sufficient repayable output.

### Citations

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

**File:** kaiax/gasless/impl/tx_pool.go (L107-173)
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

**File:** kaiax/gasless/impl/tx_pool_test.go (L443-452)
```go
func TestPromoteGaslessTxsWithMultiSenders(t *testing.T) {
	t.Parallel()

	testTxPoolConfig := blockchain.DefaultTxPoolConfig
	testTxPoolConfig.Journal = ""

	// Skip balance check for this test
	gaslessConfig := *gasless.DefaultGaslessConfig()
	gaslessConfig.BalanceCheckLevel = gasless.BalanceCheckLevelStatic

```
