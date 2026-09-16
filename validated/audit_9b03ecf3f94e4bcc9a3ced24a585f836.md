## Title
Governance-settable `Kip71GasTarget = 0` causes division-by-zero panic in `NextMagmaBlockBaseFee` (chain halt) - (File: `params/kip71_config.go`)

### Summary
The `NextMagmaBlockBaseFee` function, which computes the KIP-71 dynamic base fee for every block after the Magma hardfork, divides by the governance-controlled `GasTarget` parameter without validating that it is non-zero. Unlike the sibling parameter `Kip71BaseFeeDenominator`, which explicitly rejects zero values, `Kip71GasTarget` uses `noopFormatChecker`, so a governance vote can set `gastarget=0` and get accepted. Once such a value is active, every node computing or verifying the next block's base fee will panic with a division-by-zero, halting the chain.

### Finding Description
`Kip71GasTarget`'s parameter definition in `kaiax/gov/param.go` uses `FormatChecker: noopFormatChecker`, performing no validation on the submitted value: [1](#0-0) 

Contrast this with `Kip71BaseFeeDenominator`, defined a few lines above, which explicitly guards against zero: [2](#0-1) 

The value passes through `PartialParamSet.Add`, which only rejects a value if `FormatChecker` returns `false` — for `GasTarget` this never happens: [3](#0-2) 

The parameter is copied into `KIP71Config.GasTarget` via `ToKip71Config()`: [4](#0-3) 

`NextMagmaBlockBaseFee` then uses `gasTarget` as a divisor without any zero check (contrast with the guarded `baseFeeDenominator == 0` case just above it): [5](#0-4) 

If `gasTarget == 0` and the parent block used any gas (`parentGasUsed > 0`), execution takes the `parentGasUsed > gasTarget` branch and calls `x.Div(x, new(big.Int).SetUint64(gasTarget))` with a zero divisor. `big.Int.Div` panics on division by zero (unlike `big.Int` comparison operators, Go's `math/big` does not gracefully handle zero divisors).

This function is invoked from consensus-critical, block-processing paths — `VerifyMagmaHeader` (header validation performed by every node on every incoming block) and the gas price oracle (`node/cn/gasprice/gasprice.go`, `feehistory.go`) which call `NextMagmaBlockBaseFee`/`kip71Config.NextMagmaBlockBaseFee` on every block after Magma: [6](#0-5) 

### Impact Explanation
Once a governance vote sets `kip71.gastarget` (i.e., `governance.kip71.gastarget`) to `0` and it becomes effective, every full node and validator computing the expected base fee for the next block — via header validation (`VerifyMagmaHeader`) or fee estimation/oracle RPCs — will panic as soon as a block with nonzero `GasUsed` needs its next base fee computed. Because header verification is mandatory consensus logic executed identically by all honest nodes, this results in a network-wide chain halt / repeated node crash (DoS) rather than an isolated node crash, satisfying the "state divergence between honest nodes" / chain-halting class of impact.

### Likelihood Explanation
The only prerequisite is a single governance parameter-set transaction/vote setting `Kip71GasTarget = 0`; the `noopFormatChecker` never rejects it. Governance parameter changes are explicitly in scope as a reachable analog category, and unlike `Kip71BaseFeeDenominator` (which has a real zero-guard both in the format checker and defensively in `NextMagmaBlockBaseFee`), `GasTarget` has neither layer of protection — making this an oversight rather than a deliberately unprotected value.

### Recommendation
Add a `FormatChecker` for `Kip71GasTarget` in `kaiax/gov/param.go` that rejects zero (mirroring `Kip71BaseFeeDenominator`), and/or add a defensive zero-check inside `NextMagmaBlockBaseFee` in `params/kip71_config.go` (as already done for `BaseFeeDenominator`) that substitutes a safe default when `GasTarget == 0`.

### Proof of Concept
1. Submit/pass a governance vote setting `governance.kip71.gastarget = 0` (accepted because `Kip71GasTarget`'s `FormatChecker` is `noopFormatChecker`, always `true`).
2. Once the new parameter set becomes effective and a subsequent block has `GasUsed > 0`, any node calling `NextMagmaBlockBaseFee(parentHeaderNumber, parentHeaderBaseFee, parentHeaderGasUsed)` (from `VerifyMagmaHeader` during block/header validation, or from the gas price oracle) executes:
   ```go
   gasUsedDelta := new(big.Int).SetUint64(parentGasUsed - gasTarget) // gasTarget = 0
   x := new(big.Int).Mul(parentBaseFee, gasUsedDelta)
   y := x.Div(x, new(big.Int).SetUint64(gasTarget)) // panic: division by zero
   ``` [7](#0-6) 
3. The panic occurs deterministically for every node processing that block, halting the chain.

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

**File:** params/kip71_config.go (L70-121)
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
```
