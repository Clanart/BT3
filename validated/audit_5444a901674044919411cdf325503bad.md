### Title
Missing zero-validation on `Kip71GasTarget` governance parameter allows division-by-zero panic in KIP-71 base fee calculation - ([File: params/kip71_config.go])

### Summary
The `CollateralSettlerERC20` report describes a division-by-zero caused by a governance-set ratio parameter (`proportionalRatioGovLP`/`proportionalRatioGovUser`) never being validated as non-zero before it is used as a divisor in a later, unstoppable process (`triggerSettlement()`/`_treatClaim()`). The analogous pattern exists in Kaia's KIP-71 dynamic base fee logic: the governance parameter `GasTarget` is canonicalized but never checked for being non-zero, and it is later used unconditionally as a divisor when computing the next block's base fee.

### Finding Description
`kaiax/gov/param.go` defines the `Kip71GasTarget` parameter with `FormatChecker: noopFormatChecker`, i.e. no validation at all is performed on the value: [1](#0-0) 

This is inconsistent with the sibling parameter `Kip71BaseFeeDenominator`, which explicitly rejects zero: [2](#0-1) 

The value flows unchecked into `params.KIP71Config.GasTarget`, which is then used directly as a divisor in `NextMagmaBlockBaseFee`: [3](#0-2) 

Specifically, `y := x.Div(x, new(big.Int).SetUint64(gasTarget))` is executed unconditionally in both the "gas used > target" and "gas used < target" branches whenever `parentGasUsed != gasTarget`. If `GasTarget == 0` (which any Governor-approved vote can set, since there is no format check preventing it), and `parentGasUsed` is anything other than exactly `0` in the exact scenario matching `gasTarget`, this leads to `big.Int.Div` with a zero divisor, which panics in Go (`division by zero`), unlike Solidity where a `require` would simply revert the offending transaction.

`NextMagmaBlockBaseFee`/`VerifyMagmaHeader` is invoked from block validation code (`blockchain/block_validator.go` imports and calls it as part of consensus header verification), meaning the panic would occur for every node validating any block once such a parameter takes effect — this is a consensus-critical code path, not an isolated per-transaction revert.

This mirrors the reported bug class precisely: a governance-controlled parameter that is a divisor in downstream logic is never validated as non-zero at the point of being set, and by the time the value is used, it can no longer be changed in time to avoid the divide-by-zero (in the original report, `triggerSettlement()` locks the ratios; here, the base fee for the next block is computed deterministically from the currently active governance parameter set with no fallback such as the one already implemented for `BaseFeeDenominator`).

### Impact Explanation
If `Kip71GasTarget` is ever voted/set to `0` via governance (single mode governing node, or governance council majority in ballot mode), every full node computing/verifying the next Magma-era block header would attempt to divide by zero inside `NextMagmaBlockBaseFee`. In Go, an integer division by zero via `math/big` triggers a runtime panic, which is not the same as a graceful error return — it can crash the node process or, if recovered, produce inconsistent validation behavior across different node versions/recover-handling, leading to a chain halt or state divergence between honest nodes that behave differently around the panic. This is a Critical/High-severity finding because it affects core block validation for the entire network, not a single user transaction.

### Likelihood Explanation
Likelihood is Medium: it requires a governance-parameter change (single governing node authorization, or GC majority vote depending on `GovernanceMode`), not an arbitrary unprivileged transaction. However, unlike most other numeric governance parameters (`BaseFeeDenominator`, `Kip82Ratio`, `RewardRatio`), which all have explicit non-zero/format validations, `GasTarget` has none — this is clearly an oversight rather than an intentional design choice, making it plausible to occur by accident (e.g., a governance proposal typo setting `GasTarget` to `0`) with no error until the very next Magma block is proposed.

### Recommendation
Add an explicit `FormatChecker` for `Kip71GasTarget` (and audit other KIP-71 numeric parameters like `MaxBlockGasUsedForBaseFee`) that rejects `0`, consistent with the existing check on `Kip71BaseFeeDenominator`:
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
Additionally, as defense-in-depth, `NextMagmaBlockBaseFee` in `params/kip71_config.go` should apply the same zero-fallback pattern already used for `BaseFeeDenominator` (lines 71-76) to `gasTarget` before it is used as a divisor, so that a misconfigured/zero value cannot panic the block-validation path even if the governance-layer check is bypassed or misses a code path.

### Proof of Concept
1. Via governance (single-governance mode governing node, or a GC majority vote in ballot mode), submit a governance vote to change `kip71.gastarget` (`gov.Kip71GasTarget`) to `0`. Since `FormatChecker` is `noopFormatChecker`, this value passes validation and gets applied to the chain config at the next epoch/governance-effective block. [1](#0-0) 
2. At the next block after Magma fork with the new parameter active, any block whose `parentHeaderGasUsed != 0` (the common case) triggers `NextMagmaBlockBaseFee`'s divisor branch: [4](#0-3) 
3. `x.Div(x, new(big.Int).SetUint64(gasTarget))` with `gasTarget == 0` causes an immediate Go runtime panic inside `math/big`, crashing (or corrupting the execution state of) every node performing header verification for that block via `blockchain/block_validator.go`'s consensus checks, effectively halting the chain until the software is patched or governance reverts the parameter (which itself now cannot happen because block processing cannot proceed past the offending block).

### Citations

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

**File:** params/kip71_config.go (L88-121)
```go
	// upper gas limit cut off the impulse of used gas to upper bound
	parentGasUsed := min(parentHeaderGasUsed, upperGasLimit)
	if parentGasUsed == gasTarget {
		return makeEvenByFloor(parentBaseFee)
	} else if parentGasUsed > gasTarget {
		// shortcut. If parentBaseFee is already reached upperbound, do not calculate.
		if parentBaseFee.Cmp(upperBoundBaseFee) == 0 {
			return makeEvenByFloor(upperBoundBaseFee)
		}
		// If the parent block used more gas than its target,
		// the baseFee of the next block should increase.
		// baseFeeDelta = max(1, parentBaseFee * (parentGasUsed - gasTarget) / gasTarget / baseFeeDenominator)
		gasUsedDelta := new(big.Int).SetUint64(parentGasUsed - gasTarget)
		x := new(big.Int).Mul(parentBaseFee, gasUsedDelta)
		y := x.Div(x, new(big.Int).SetUint64(gasTarget))
		baseFeeDelta := math.BigMax(x.Div(y, baseFeeDenominator), common.Big1)

		nextBaseFee := x.Add(parentBaseFee, baseFeeDelta)
		if nextBaseFee.Cmp(upperBoundBaseFee) > 0 {
			return makeEvenByFloor(upperBoundBaseFee)
		}
		return makeEvenByFloor(nextBaseFee)
	} else {
		// shortcut. If parentBaseFee is already reached lower bound, do not calculate.
		if parentBaseFee.Cmp(lowerBoundBaseFee) == 0 {
			return makeEvenByFloor(lowerBoundBaseFee)
		}
		// Otherwise if the parent block used less gas than its target,
		// the baseFee of the next block should decrease.
		// baseFeeDelta = parentBaseFee * (gasTarget - parentGasUsed) / gasTarget / baseFeeDenominator
		gasUsedDelta := new(big.Int).SetUint64(gasTarget - parentGasUsed)
		x := new(big.Int).Mul(parentBaseFee, gasUsedDelta)
		y := x.Div(x, new(big.Int).SetUint64(gasTarget))
		baseFeeDelta := x.Div(y, baseFeeDenominator)
```
