This is a critical finding: `Kip71GasTarget` (the governance parameter controlling `KIP71Config.GasTarget`) uses `noopFormatChecker`, unlike `Kip71BaseFeeDenominator`, which explicitly requires `v != 0`. This means governance can set `GasTarget = 0` without any format-level rejection, while `NextMagmaBlockBaseFee` divides by `gasTarget` in the "used more than target" branch. [1](#0-0) 

### Title
Zero-value `Kip71GasTarget` governance parameter is unvalidated and causes division-by-zero panic in `NextMagmaBlockBaseFee` - (File: params/kip71_config.go)

### Summary
`KIP71Config.GasTarget` is used as a divisor in `NextMagmaBlockBaseFee`, but unlike its sibling parameter `Kip71BaseFeeDenominator` (which is guarded both by a nonzero `FormatChecker` and a runtime zero-check that falls back to `64`), the `Kip71GasTarget` governance parameter has no such protections in either the parameter registration or the consuming function.

### Finding Description
`NextMagmaBlockBaseFee` computes the next block's base fee using `gasTarget := kc.GasTarget` and, when the parent block's gas used differs from `gasTarget`, divides by `gasTarget`: [2](#0-1) [3](#0-2) 

Contrast this with `BaseFeeDenominator`, which is explicitly checked for zero at runtime with a safe fallback: [4](#0-3) 

But `GasTarget` has no equivalent `if kc.GasTarget == 0 { ... }` guard.

At the governance-parameter layer, `Kip71BaseFeeDenominator`'s `FormatChecker` explicitly rejects zero (`return ok && v != 0`), while `Kip71GasTarget` uses `noopFormatChecker`, which performs no validation at all: [1](#0-0) 

This means a governance vote/parameter update setting `kip71.gastarget` to `0` would be accepted by the parameter-set validation layer, propagate into `ParamSet.ToKip71Config()` [5](#0-4) , and eventually reach `NextMagmaBlockBaseFee`, where `parentGasUsed > gasTarget` (true whenever any gas is used and gasTarget=0) triggers `y := x.Div(x, new(big.Int).SetUint64(gasTarget))` — a division by zero that panics.

### Impact Explanation
`NextMagmaBlockBaseFee` is called on every block during header validation/verification (`VerifyMagmaHeader`) and by RPC (`eth_feeHistory`'s `processBlock`) to compute expected/next base fees. A panic here during block header verification would crash consensus/verification code paths on every node processing that block — effectively a chain-halting bug reachable via a single governance parameter misconfiguration (which could originate from a malicious or buggy governance vote, or operator error), not merely a resource-only or malicious-node issue, since it directly affects state transition (KIP-71 pricing) for all nodes uniformly.

### Likelihood Explanation
Reaching this bug requires the governance parameter `kip71.gastarget` (i.e. `Kip71GasTarget`) to be set to zero. Since `FormatChecker` for this parameter is a no-op and does not reject zero (unlike the analogous `BaseFeeDenominator` parameter which does), there is no check preventing a governance proposal from setting this to zero. If such a parameter update is accepted (via the standard governance-vote mechanism used to modify KIP-71 configs), any subsequent block with nonzero gas usage would panic when the node computes/verifies the next base fee.

### Recommendation
Add an explicit nonzero `FormatChecker` for `Kip71GasTarget` analogous to `Kip71BaseFeeDenominator`'s (`return ok && v != 0`), and/or add a defensive runtime guard in `NextMagmaBlockBaseFee` (similar to the existing `baseFeeDenominator == 0` fallback) that handles `gasTarget == 0` without dividing by it.

### Proof of Concept
1. Submit/accept a governance parameter update setting `kip71.gastarget = 0`. Because `Kip71GasTarget`'s `FormatChecker` is `noopFormatChecker` [6](#0-5) , this value passes `PartialParamSet.Add`'s validation [7](#0-6) .
2. Once active, `ParamSet.ToKip71Config()` produces a `KIP71Config{GasTarget: 0, ...}` [5](#0-4) .
3. On the next block where `parentHeaderGasUsed > 0` (i.e., `parentGasUsed > gasTarget` since `gasTarget=0`), `NextMagmaBlockBaseFee` executes `y := x.Div(x, new(big.Int).SetUint64(gasTarget))` with `gasTarget = 0` [2](#0-1) , causing a division-by-zero panic in `big.Int.Div`.

Note: I could not fully verify whether an additional layer of validation exists elsewhere (e.g. in the governance vote submission RPC or a separate consensus-level sanity check for `KIP71Config`) that might reject a zero `GasTarget` before it reaches `ParamSet`; my search of `kaiax/gov/param.go` and `paramset.go` found no such guard specific to this parameter, and the pattern strongly mirrors the analog bug class from the audit report (division using a value that can legitimately be zero, with no upstream buffer/guard).

### Citations

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
