### Title
Missing lower-bound validation on `kip71.gastarget` allows setting `GasTarget = 0`, causing a division-by-zero panic in the KIP-71 base fee formula - (File: `params/kip71_config.go`, `kaiax/gov/param.go`)

### Summary
The KIP-71 governance parameter `Kip71GasTarget` is registered with a `noopFormatChecker`, meaning any `uint64` value—including `0`—passes validation when set via governance vote or the `GovParam` contract. This value is later used as a divisor in `KIP71Config.NextMagmaBlockBaseFee`, so a value of `0` triggers a division-by-zero panic during base fee computation, which is executed by every node for every block.

### Finding Description
`Kip71GasTarget` is declared in the governance parameter table with no meaningful format check: [1](#0-0) 

Compare this to a sibling KIP-71 parameter, `Kip71BaseFeeDenominator`, which explicitly rejects zero: [2](#0-1) 

`GasTarget` receives no analogous `v != 0` (or any other threshold) check, and `Params[name].FormatChecker` is the only gate applied before a vote value is accepted into the governance parameter set: [3](#0-2) 

The resulting `GasTarget` value flows into `KIP71Config.GasTarget` and is used unconditionally as a divisor inside `NextMagmaBlockBaseFee`: [4](#0-3) 

Specifically, when `parentGasUsed > gasTarget` (true whenever `gasTarget == 0` and any gas was used), the code computes:
```
y := x.Div(x, new(big.Int).SetUint64(gasTarget))
```
`big.Int.Div` panics on division by zero, so this branch panics whenever `GasTarget == 0` and the block used any gas. This is the same root-cause pattern as the reported analog (`setMaxReferralEarningTime`/`isValidForEarning`): a governance-set numeric parameter is consumed in an arithmetic formula without the setter enforcing a threshold that keeps the downstream computation well-defined, allowing the formula's implicit precondition to be silently bypassed.

### Impact Explanation
`NextMagmaBlockBaseFee` (via `VerifyMagmaHeader`) is on the block validation and block-building path, invoked by every full/consensus node when creating or validating any block once Magma is active. A `GasTarget` of `0` would cause every node processing a block with nonzero gas usage to panic identically, halting the chain network-wide—a High-impact, no-impact-only-if-silent condition, but here it is a definite consensus-wide crash/DoS once the parameter is applied at the next epoch boundary. This directly falls under the allowed category "pool admission and KIP-71 pricing" and can be classified as a state-transition-breaking, chain-halting defect.

### Likelihood Explanation
Setting KIP-71 parameters is a governance-vote action (via header governance vote or the `GovParam` contract's `setParamIn`), so it requires governance-level access rather than an arbitrary unprivileged sender. However, unlike `IstanbulPolicy`, `IstanbulCommitteeSize`, `GovernanceDeriveShaImpl`, and `Kip71BaseFeeDenominator` — all of which have explicit range/nonzero `FormatChecker`s — `Kip71GasTarget`, `Kip71LowerBoundBaseFee`, `Kip71UpperBoundBaseFee`, and `Kip71MaxBlockGasUsedForBaseFee` all use `noopFormatChecker`, meaning the omission of a nonzero check for `GasTarget` appears to be an oversight rather than an intentional design choice, exactly mirroring the reported bug class where the setter fails to bound a numeric parameter consumed downstream in a formula.

### Recommendation
Add a `FormatChecker` for `Kip71GasTarget` (and consider bounds for `Kip71MaxBlockGasUsedForBaseFee`/`Kip71LowerBoundBaseFee`/`Kip71UpperBoundBaseFee`) analogous to `Kip71BaseFeeDenominator`'s `v != 0` check, e.g.:
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
Additionally, defensively guard `NextMagmaBlockBaseFee` against a zero `gasTarget` (similar to the existing `BaseFeeDenominator == 0` fallback) to avoid a panic even if an invalid value is ever persisted from a legacy/misconfigured chain config.

### Proof of Concept
1. A governance vote (or `GovParam.setParamIn`) sets `kip71.gastarget = 0`. Because `Kip71GasTarget.FormatChecker` is `noopFormatChecker`, this value passes `PartialParamSet.Add`/`ParamSet.Set` validation and is committed as an epoch governance parameter. [3](#0-2) 
2. At the next epoch, `ParamSet.ToKip71Config()` propagates `GasTarget = 0` into the active `KIP71Config`. [5](#0-4) 
3. When the next block with `gasUsed > 0` is proposed/validated, `NextMagmaBlockBaseFee` computes `parentGasUsed - gasTarget` (nonzero) and then divides by `gasTarget` (`0`), causing a `big.Int` division-by-zero panic. [6](#0-5) 
4. Because every consensus/full node executes this same code path for the same block, the panic occurs uniformly across the network, halting block production/validation chain-wide.

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

**File:** params/kip71_config.go (L77-121)
```go
	gasTarget := kc.GasTarget
	upperGasLimit := kc.MaxBlockGasUsedForBaseFee

	// check the case of upper/lowerBoundBaseFee is updated by governance mechanism
	parentBaseFee := parentHeaderBaseFee
	if parentBaseFee.Cmp(upperBoundBaseFee) >= 0 {
		parentBaseFee = upperBoundBaseFee
	} else if parentBaseFee.Cmp(lowerBoundBaseFee) <= 0 {
		parentBaseFee = lowerBoundBaseFee
	}

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
