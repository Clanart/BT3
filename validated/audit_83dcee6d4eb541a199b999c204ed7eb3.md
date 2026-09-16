### Title
Missing lower-bound validation for KIP-71 `GasTarget` allows a division-by-zero panic that halts every node on the chain - ([File: kaiax/gov/param.go], [File: params/kip71_config.go])

### Summary
The KIP-71 (Magma dynamic base fee) governance parameters `GasTarget`, `LowerBoundBaseFee`, `UpperBoundBaseFee`, and `MaxBlockGasUsedForBaseFee` are registered with `FormatChecker: noopFormatChecker`, meaning **no runtime validation is performed** when these values are set through governance. By contrast, the sibling parameter `Kip71BaseFeeDenominator` correctly rejects zero: [1](#0-0) 

but `Kip71GasTarget` accepts anything, including zero: [2](#0-1) 

This is the same bug class as the external report: a runtime configuration value that governs a downstream fee-calculation formula is never validated at the point it is written, so an invalid value silently propagates into the chain state until it is exercised.

### Finding Description
`KIP71Config.NextMagmaBlockBaseFee` computes the next block's base fee using `GasTarget` as a divisor in the `big.Int` division operation: [3](#0-2) 

Both the "gas used above target" branch (`y := x.Div(x, new(big.Int).SetUint64(gasTarget))`, line ~102) and the "gas used below target" branch (line ~120) divide by `gasTarget` with **no zero-check**, unlike `BaseFeeDenominator`, which explicitly falls back to `64` when it is zero (lines 71-76 of the same file). `big.Int.Div` by zero causes a runtime panic in Go.

Because `Kip71GasTarget`'s `FormatChecker` is `noopFormatChecker`, a governance vote/parameter update that sets `governance.gastarget` to `0` (or any other malformed value that reaches zero through the `uint64Canonicalizer`) will be accepted and persisted into the chain's `ParamSet`/`ChainConfig` without rejection: [4](#0-3) 

Once this governance value takes effect at the target epoch, **every node in the network** computing the next block's base fee (during block validation, block sealing/mining, and gas price oracle queries) will hit the same division-by-zero panic and crash simultaneously, since the calculation is deterministic and identical for every full/consensus node.

### Impact Explanation
`NextMagmaBlockBaseFee`/`VerifyMagmaHeader` is invoked from core block-processing paths shared by all nodes: block validation, block assembly (mining), transaction pool base-fee checks, and gas price oracle/fee history RPC handlers. A single bad governance parameter value (reachable through the standard governance-vote mechanism) that is not validated at write-time will, once activated, cause an identical panic on every node processing blocks past that point — producing a synchronized chain-wide crash/halt rather than a localized DoS on one operator. This is a consensus-liveness-breaking condition triggered by ordinary block production, not by any privileged/off-chain or peer-message action, matching the "state transition and gas/burn accounting"/"KIP-71 pricing"/"governance parameters" categories that are in scope.

### Likelihood Explanation
Likelihood is Medium: it requires a governance parameter change to be enacted (as in the original report, an admin/governing-node configuration mistake), but unlike the excluded "governance is trusted, no validation needed" assumption, the code base demonstrably enforces `v != 0` for the analogous `BaseFeeDenominator` parameter while leaving `GasTarget` (and the bound parameters) unchecked — showing the validation gap is unintentional/inconsistent rather than a deliberate trust boundary. A single mistaken or malicious governance proposal setting `governance.gastarget = 0` is sufficient; no attacker-controlled transaction is needed beyond the normal governance vote flow, and the resulting panic affects the entire network deterministically on the next block.

### Recommendation
Add a `FormatChecker` for `Kip71GasTarget` (and ideally `Kip71LowerBoundBaseFee`, `Kip71UpperBoundBaseFee`, `Kip71MaxBlockGasUsedForBaseFee`) analogous to `Kip71BaseFeeDenominator`'s `v != 0` check, rejecting zero or otherwise invalid values before they are accepted into the `ParamSet`. Additionally, as defense-in-depth, `NextMagmaBlockBaseFee` should guard against `gasTarget == 0` (similar to the existing zero-fallback for `baseFeeDenominator`) so that even a value that slips through governance validation cannot cause a network-wide panic.

### Proof of Concept
1. Governing node casts (or a malformed vote accidentally sets) `governance.gastarget = 0` via the governance vote mechanism; `PartialParamSet.Add`/`ParamSet.Set` accepts it because `Kip71GasTarget.FormatChecker` is `noopFormatChecker`. [4](#0-3) 
2. At the epoch/block where this governance change activates, `ChainConfigValue`/`ToKip71Config()` propagates `GasTarget = 0` into the active `KIP71Config` used by block validation and gas price computation. [5](#0-4) 
3. On the next block, any node (validator, endpoint node, RPC gas price oracle) calls `NextMagmaBlockBaseFee`, which executes `x.Div(x, new(big.Int).SetUint64(gasTarget))` with `gasTarget = 0`, causing a `big.Int` division-by-zero panic in the Go runtime. [6](#0-5) 
4. Because this function is called by every node processing/verifying/producing the block, the panic occurs simultaneously across the network, halting block production/verification chain-wide.

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

**File:** params/kip71_config.go (L88-128)
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

		nextBaseFee := x.Sub(parentBaseFee, baseFeeDelta)
		if nextBaseFee.Cmp(lowerBoundBaseFee) < 0 {
			return makeEvenByFloor(lowerBoundBaseFee)
		}
		return makeEvenByFloor(nextBaseFee)
	}
```

**File:** kaiax/gov/paramset.go (L199-207)
```go
func (p *ParamSet) ToKip71Config() *params.KIP71Config {
	return &params.KIP71Config{
		LowerBoundBaseFee:         p.LowerBoundBaseFee,
		UpperBoundBaseFee:         p.UpperBoundBaseFee,
		GasTarget:                 p.GasTarget,
		MaxBlockGasUsedForBaseFee: p.MaxBlockGasUsedForBaseFee,
		BaseFeeDenominator:        p.BaseFeeDenominator,
	}
}
```

**File:** kaiax/gov/paramset.go (L209-226)
```go
func (p PartialParamSet) Add(name string, value any) error {
	param, ok := Params[ParamName(name)]
	if !ok {
		return ErrInvalidParamName
	}

	cv, err := param.Canonicalizer(value)
	if err != nil {
		return err
	}

	if !param.FormatChecker(cv) {
		return ErrInvalidParamValue
	}

	p[ParamName(name)] = cv
	return nil
}
```
