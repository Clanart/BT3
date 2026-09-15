### Title
Division-by-zero in `KIP71Config.NextMagmaBlockBaseFee` when governance-controlled `GasTarget` is 0 - ([File: params/kip71_config.go])

### Summary
`NextMagmaBlockBaseFee` divides by the governance parameter `GasTarget` without checking it for zero, unlike the sibling parameter `BaseFeeDenominator`, which explicitly guards against a zero value. Because the Kaia governance parameter registry allows `GasTarget` to be set to `0` (no format check enforcing `v != 0`), a governance vote setting `Kip71GasTarget = 0` causes every node computing the next Magma base fee to panic with a division-by-zero, halting block production/validation network-wide. This mirrors the analog CVE-2024-57922 pattern: a ceil/floor-style helper (`makeEvenByFloor`/`makeEvenByCeil` combined with the base-fee delta computation) divides by a "granularity"-like parameter that is not checked for zero.

### Finding Description
`NextMagmaBlockBaseFee` computes the next block's base fee using `gasTarget := kc.GasTarget` as a divisor in the "increase" and "decrease" branches: [1](#0-0) [2](#0-1) [3](#0-2) 

Note that `BaseFeeDenominator` has an explicit zero-guard ("To avoid panic, set the fluctuation range small") right before this code, but `GasTarget` has no equivalent check: [1](#0-0) 

If `parentGasUsed != gasTarget` (the early-return equal case is skipped) and `gasTarget == 0`, then `parentGasUsed > gasTarget` is always true (unless `parentGasUsed` is also 0, which almost never happens for real blocks), and the code executes `y := x.Div(x, new(big.Int).SetUint64(gasTarget))` with a zero divisor, which is a panic in Go's `math/big.Int.Div`.

The `GasTarget` governance parameter's format checker does not reject zero, unlike `BaseFeeDenominator`'s checker which explicitly requires `v != 0`: [4](#0-3) 

This confirms the parameter can legitimately be voted to `0` through the governance mechanism and passed into `KIP71Config.GasTarget`, which then flows into `ToKip71Config()` and `NextMagmaBlockBaseFee`.

### Impact Explanation
`NextMagmaBlockBaseFee` / `VerifyMagmaHeader` is invoked in core block-production and block-validation paths (`blockchain/block_validator.go`, `blockchain/chain_makers.go`, `blockchain/tx_pool.go`, `work/worker.go`, `node/cn/gasprice/*`), all of which are executed by every full node processing new blocks. A panic here on all Magma-enabled nodes as soon as `GasTarget == 0` is applied causes a network-wide chain halt (denial of service), matching the availability-only impact of the upstream CVE (`C:N/I:N/A:H`).

### Likelihood Explanation
Exploitation requires a successful governance vote to change `Kip71GasTarget` to `0`. This is a governance-level action rather than a plain unprivileged transaction, but the parameter's validation logic (`FormatChecker: noopFormatChecker`) fails to reject the unsafe value — the same class of bug as the CVE (missing "granularity != 0" check before use as a divisor). Given governance parameters are within the explicitly allowed analog scope, and no defense-in-depth check exists at the point of use (unlike the sibling `BaseFeeDenominator`), the likelihood of triggering a chain-halting panic once such a vote passes is effectively certain.

### Recommendation
Add a zero-check for `GasTarget` symmetric to the existing `BaseFeeDenominator` safeguard, e.g., default to a safe non-zero value when `kc.GasTarget == 0` inside `NextMagmaBlockBaseFee`, and/or add a `FormatChecker` for `Kip71GasTarget` in `kaiax/gov/param.go` that rejects `0`, consistent with `Kip71BaseFeeDenominator`'s checker.

### Proof of Concept
1. Governance council votes to set `Kip71GasTarget = 0` (allowed since its `FormatChecker` is `noopFormatChecker`, unlike `Kip71BaseFeeDenominator`'s `v != 0` check): [5](#0-4) 
2. Once the vote takes effect, any block with `parentGasUsed > 0` (virtually all real blocks) triggers the "increase" branch in `NextMagmaBlockBaseFee`: [2](#0-1) 
3. `x.Div(x, new(big.Int).SetUint64(0))` panics with division by zero inside `math/big`, crashing every node calling `NextMagmaBlockBaseFee` during header verification or block assembly, halting the chain.

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

**File:** params/kip71_config.go (L97-103)
```go
		// If the parent block used more gas than its target,
		// the baseFee of the next block should increase.
		// baseFeeDelta = max(1, parentBaseFee * (parentGasUsed - gasTarget) / gasTarget / baseFeeDenominator)
		gasUsedDelta := new(big.Int).SetUint64(parentGasUsed - gasTarget)
		x := new(big.Int).Mul(parentBaseFee, gasUsedDelta)
		y := x.Div(x, new(big.Int).SetUint64(gasTarget))
		baseFeeDelta := math.BigMax(x.Div(y, baseFeeDenominator), common.Big1)
```

**File:** params/kip71_config.go (L115-122)
```go
		// Otherwise if the parent block used less gas than its target,
		// the baseFee of the next block should decrease.
		// baseFeeDelta = parentBaseFee * (gasTarget - parentGasUsed) / gasTarget / baseFeeDenominator
		gasUsedDelta := new(big.Int).SetUint64(gasTarget - parentGasUsed)
		x := new(big.Int).Mul(parentBaseFee, gasUsedDelta)
		y := x.Div(x, new(big.Int).SetUint64(gasTarget))
		baseFeeDelta := x.Div(y, baseFeeDenominator)

```

**File:** kaiax/gov/param.go (L310-334)
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
