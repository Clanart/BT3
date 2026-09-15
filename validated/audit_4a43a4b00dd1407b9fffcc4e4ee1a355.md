### Title
Division by Zero in KIP-71 Base Fee Calculation via Unvalidated `GasTarget` Governance Parameter - (File: params/kip71_config.go)

### Summary
`KIP71Config.NextMagmaBlockBaseFee` divides by `gasTarget` when computing the next block's base fee, but unlike the sibling parameter `BaseFeeDenominator` (which is defended against a zero value), the `GasTarget` governance parameter has no non-zero validation, allowing a governance-set `GasTarget = 0` to trigger a division-by-zero panic in a consensus-critical path.

### Finding Description
In `params/kip71_config.go`, `NextMagmaBlockBaseFee` computes the base fee delta using `gasTarget` as a divisor in both the "gas used above target" and "gas used below target" branches: [1](#0-0) [2](#0-1) 

Note that the function explicitly guards against `BaseFeeDenominator == 0` by falling back to a hardcoded value: [3](#0-2) 

But it performs no equivalent guard for `gasTarget`. If `gasTarget == 0` and `parentGasUsed != 0` (i.e., `parentGasUsed > gasTarget`, the common case for any block with gas usage), the code executes `x.Div(x, new(big.Int).SetUint64(gasTarget))` — a division by zero, which in Go's `math/big` package panics rather than returning an error.

Critically, the governance parameter registry validates `Kip71BaseFeeDenominator` with an explicit non-zero check but validates `Kip71GasTarget` with only a `noopFormatChecker`, i.e., no validation at all: [4](#0-3) [5](#0-4) 

This asymmetry means a governance vote setting `governance.kip71.gastarget` to `0` is accepted by the parameter format checker and will be committed as the effective `GasTarget` for subsequent blocks (governance parameter change is a reachable, in-scope attack surface per the analog scope).

### Impact Explanation
`NextMagmaBlockBaseFee` is invoked from multiple consensus/production paths, including block header verification (`VerifyMagmaHeader` in `params/kip71_config.go`, used by `blockchain/block_validator.go`), block assembly (`work/worker.go`), transaction pool base-fee bookkeeping (`blockchain/tx_pool.go`), and the public RPC `eth_feeHistory` endpoint (`node/cn/gasprice/feehistory.go`). A panic in this function during header verification or block building would crash the node process handling that call, rather than just returning a graceful error. Since this function runs identically on every node computing/verifying the same header, a governance-driven `GasTarget = 0` would cause a synchronized crash (denial of service) across all consensus nodes processing Magma-fork blocks with nonzero gas usage — effectively halting the chain until the parameter is fixed, which satisfies "state divergence between honest nodes" / chain-halting impact allowed by the analog scope.

### Likelihood Explanation
Likelihood depends on governance being willing/able to set `GasTarget = 0`. This requires a successful governance parameter vote (or misconfiguration by the governing node), which is a higher bar than an ordinary unprivileged transaction, but it is squarely within the explicitly allowed analog class "governance parameters" in the scan rules. Given that no format-level or code-level safeguard exists (unlike the neighboring `BaseFeeDenominator` field which is explicitly protected), this is a latent code-quality/robustness gap that would manifest as an immediate, reproducible panic the moment the parameter takes effect and any block has nonzero gas usage (a near-certainty in practice).

### Recommendation
Add an explicit non-zero format checker for `Kip71GasTarget` in `kaiax/gov/param.go`, analogous to the one used for `Kip71BaseFeeDenominator`:
```go
Kip71GasTarget: {
    Canonicalizer: uint64Canonicalizer,
    FormatChecker: func(cv any) bool {
        v, ok := cv.(uint64)
        return ok && v != 0
    },
    ...
}
```
Additionally, as defense in depth, add a zero-guard fallback in `NextMagmaBlockBaseFee` in `params/kip71_config.go` mirroring the existing `BaseFeeDenominator == 0` handling, so that even a mis-set `GasTarget` cannot cause a panic in a consensus-critical function.

### Proof of Concept
1. Governance votes to set `governance.kip71.gastarget` to `0`. The vote passes the `Kip71GasTarget` `FormatChecker` (`noopFormatChecker` accepts any value), so it is accepted as a valid governance parameter change.
2. Once the parameter takes effect at the next epoch/hardfork evaluation, any subsequent block with `parentHeaderGasUsed > 0` causes `NextMagmaBlockBaseFee` to enter the `parentGasUsed > gasTarget` branch:
   ```go
   gasUsedDelta := new(big.Int).SetUint64(parentGasUsed - gasTarget) // parentGasUsed - 0
   x := new(big.Int).Mul(parentBaseFee, gasUsedDelta)
   y := x.Div(x, new(big.Int).SetUint64(gasTarget)) // Div(x, 0) -> panic
   ```
3. This function is called during block header verification (`VerifyMagmaHeader`) and block building (`work/worker.go`), causing any node validating or building the block to panic/crash, and is also reachable by any public RPC caller via `eth_feeHistory` (`node/cn/gasprice/feehistory.go`), which calls `kip71Config.NextMagmaBlockBaseFee` for the next block's projected base fee.

### Citations

**File:** params/kip71_config.go (L70-76)
```go
	var baseFeeDenominator *big.Int
	if kc.BaseFeeDenominator == 0 {
		// To avoid panic, set the fluctuation range small
		baseFeeDenominator = new(big.Int).SetUint64(64)
	} else {
		baseFeeDenominator = new(big.Int).SetUint64(kc.BaseFeeDenominator)
	}
```

**File:** params/kip71_config.go (L99-103)
```go
		// baseFeeDelta = max(1, parentBaseFee * (parentGasUsed - gasTarget) / gasTarget / baseFeeDenominator)
		gasUsedDelta := new(big.Int).SetUint64(parentGasUsed - gasTarget)
		x := new(big.Int).Mul(parentBaseFee, gasUsedDelta)
		y := x.Div(x, new(big.Int).SetUint64(gasTarget))
		baseFeeDelta := math.BigMax(x.Div(y, baseFeeDenominator), common.Big1)
```

**File:** params/kip71_config.go (L117-121)
```go
		// baseFeeDelta = parentBaseFee * (gasTarget - parentGasUsed) / gasTarget / baseFeeDenominator
		gasUsedDelta := new(big.Int).SetUint64(gasTarget - parentGasUsed)
		x := new(big.Int).Mul(parentBaseFee, gasUsedDelta)
		y := x.Div(x, new(big.Int).SetUint64(gasTarget))
		baseFeeDelta := x.Div(y, baseFeeDenominator)
```

**File:** kaiax/gov/param.go (L310-323)
```go
	Kip71BaseFeeDenominator: {
		Canonicalizer: uint64Canonicalizer,
		FormatChecker: func(cv any) bool {
			v, ok := cv.(uint64)
			return ok && v != 0
		},
		ChainConfigValue: func(c *params.ChainConfig) (any, error) {
			if c.Governance == nil || c.Governance.KIP71 == nil {
				return nil, errors.New("kip71 is not set")
			}
			return c.Governance.KIP71.BaseFeeDenominator, nil
		},
		DefaultValue: uint64(20),
	},
```

**File:** kaiax/gov/param.go (L324-334)
```go
	Kip71GasTarget: {
		Canonicalizer: uint64Canonicalizer,
		FormatChecker: noopFormatChecker,
		ChainConfigValue: func(c *params.ChainConfig) (any, error) {
			if c.Governance == nil || c.Governance.KIP71 == nil {
				return nil, errors.New("kip71 is not set")
			}
			return c.Governance.KIP71.GasTarget, nil
		},
		DefaultValue: uint64(30000000),
	},
```
