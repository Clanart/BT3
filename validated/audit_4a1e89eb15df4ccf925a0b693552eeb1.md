### Title
Missing zero-check on `Kip71GasTarget` governance parameter causes division-by-zero panic in `NextMagmaBlockBaseFee` - (File: params/kip71_config.go)

### Summary
`kaiax/gov/param.go` explicitly guards `Kip71BaseFeeDenominator` against a value of `0` (`FormatChecker: func(cv any) bool { v, ok := cv.(uint64); return ok && v != 0 }`), because the base-fee formula divides by it and the code even documents this with a runtime fallback ("To avoid panic, set the fluctuation range small"). The sibling parameter `Kip71GasTarget`, which is divided by in the exact same formula, only uses `noopFormatChecker`, i.e. no validation at all, including no `!= 0` check. [1](#0-0) 

### Finding Description
`NextMagmaBlockBaseFee` computes the base fee using both `GasTarget` and `BaseFeeDenominator` as divisors: [2](#0-1) 

For `BaseFeeDenominator`, the code has an explicit guard (`if kc.BaseFeeDenominator == 0 { baseFeeDenominator = 64 }`) precisely to avoid a division-by-zero panic. [3](#0-2) 

No equivalent guard exists for `gasTarget := kc.GasTarget`. When `parentGasUsed != gasTarget`, the code executes `x.Div(x, new(big.Int).SetUint64(gasTarget))` (both in the "increase" and "decrease" branches), which panics if `gasTarget == 0`. [4](#0-3) [5](#0-4) 

The governance parameter registry that validates values submitted through governance votes/transactions applies exactly this asymmetric validation: `Kip71BaseFeeDenominator` rejects `0`, but `Kip71GasTarget` uses `noopFormatChecker`, accepting any `uint64` including `0`. [1](#0-0) 

This is the same bug class as the external report: a critical bound is enforced on one code path (the "setter"/validated path) but omitted for a value used in an identical, unguarded arithmetic operation elsewhere.

### Impact Explanation
`NextMagmaBlockBaseFee` is invoked during block header validation (`VerifyMagmaHeader`) and block assembly for every block once Magma is active, on every node in the network. [6](#0-5) 
If `GasTarget` is ever set to `0` via a governance parameter update (submitted as a governance vote transaction and later applied to the chain config through `ParamSet`/`ToKip71Config`), then as soon as a block's gas usage differs from `0` (virtually always true), every node computing or verifying the next base fee will panic on the division by zero. This is a deterministic, network-wide chain-halt: all honest nodes crash identically, since the parameter change is deterministic governance state applied uniformly. This qualifies as acceptance of an invalid/unsafe governance parameter leading to a consensus-critical crash of the block assembly / state-transition path.

### Likelihood Explanation
Setting a governance parameter typically requires governing-node/validator privileges, similar to the privilege level ("malicious admin") accepted in the original report. Given the explicit `!= 0` protection that was added for `BaseFeeDenominator`, it is clear the developers were aware of the division-by-zero risk for this formula but failed to apply the same protection to `GasTarget`, which is used identically. A single governance parameter update transaction with `GasTarget = 0` is sufficient to trigger the bug on the next block where gas usage differs from the target — which is essentially guaranteed.

### Recommendation
Add a `FormatChecker` for `Kip71GasTarget` in `kaiax/gov/param.go` that rejects `0`, mirroring the existing `Kip71BaseFeeDenominator` check:
```go
Kip71GasTarget: {
    Canonicalizer: uint64Canonicalizer,
    FormatChecker: func(cv any) bool {
        v, ok := cv.(uint64)
        return ok && v != 0
    },
    ...
},
```
Additionally, as defense-in-depth, add the same runtime fallback guard used for `BaseFeeDenominator` inside `NextMagmaBlockBaseFee` to protect against any other code path (e.g. genesis/CLI config, `homi` config) that could set `GasTarget` to `0` without going through the governance parameter validator.

### Proof of Concept
1. A governing node submits a governance vote transaction setting `kip71.gastarget` to `0`.
2. `kaiax/gov/param.go`'s `Kip71GasTarget.FormatChecker` is `noopFormatChecker`, so the vote is accepted and canonicalized without error.
3. Once the parameter takes effect, `ParamSet.ToKip71Config()` propagates `GasTarget: 0` into the active `KIP71Config`.
4. On the next block whose `parentHeaderGasUsed != 0` (i.e., almost any block), `NextMagmaBlockBaseFee` executes `parentGasUsed > gasTarget` (0), entering the "increase" branch, and calls `x.Div(x, new(big.Int).SetUint64(0))`, which panics — halting every node executing this code path (block builders and validators alike). [7](#0-6)

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

**File:** params/kip71_config.go (L45-56)
```go
func (kc *KIP71Config) VerifyMagmaHeader(headerBaseFee *big.Int, parentHeaderNumber *big.Int, parentHeaderBaseFee *big.Int, parentHeaderGasUsed uint64) error {
	if headerBaseFee == nil {
		return fmt.Errorf("header is missing baseFee")
	}
	// Verify the baseFee is correct based on the parent header.
	expectedBaseFee := kc.NextMagmaBlockBaseFee(parentHeaderNumber, parentHeaderBaseFee, parentHeaderGasUsed)
	if headerBaseFee.Cmp(expectedBaseFee) != 0 {
		return fmt.Errorf("invalid baseFee: have %s, want %s, parentBaseFee %s, parentGasUsed %d",
			headerBaseFee, expectedBaseFee, parentHeaderBaseFee, parentHeaderGasUsed)
	}
	return nil
}
```

**File:** params/kip71_config.go (L70-128)
```go
	var baseFeeDenominator *big.Int
	if kc.BaseFeeDenominator == 0 {
		// To avoid panic, set the fluctuation range small
		baseFeeDenominator = new(big.Int).SetUint64(64)
	} else {
		baseFeeDenominator = new(big.Int).SetUint64(kc.BaseFeeDenominator)
	}
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

		nextBaseFee := x.Sub(parentBaseFee, baseFeeDelta)
		if nextBaseFee.Cmp(lowerBoundBaseFee) < 0 {
			return makeEvenByFloor(lowerBoundBaseFee)
		}
		return makeEvenByFloor(nextBaseFee)
	}
```
