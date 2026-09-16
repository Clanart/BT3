### Title
Missing zero-value validation on `Kip71GasTarget` governance parameter causes a division-by-zero panic in `NextMagmaBlockBaseFee` - (File: `params/kip71_config.go`)

### Summary
The `Kip71GasTarget` governance parameter has no zero-value validation, unlike its sibling parameter `BaseFeeDenominator`. If `GasTarget` is set to `0` via a governance vote, the base-fee computation performed on every block after the Magma fork divides by `gasTarget`, causing a panic (division by zero) in the standard `math/big` package. This crashes block header verification / state transition on every full node processing the block, resulting in a chain-wide halt.

### Finding Description
`params/kip71_config.go`'s `NextMagmaBlockBaseFee` explicitly guards against a zero `BaseFeeDenominator`: [1](#0-0) 

but performs no equivalent guard for `GasTarget`. The value is used unchecked in both the "gas used above target" and "below target" branches to divide the intermediate result: [2](#0-1) 

Specifically, `y := x.Div(x, new(big.Int).SetUint64(gasTarget))` will panic with "division by zero" (Go `math/big` behavior) whenever `gasTarget == 0` and `parentGasUsed != 0` (which drives execution into the `parentGasUsed > gasTarget` branch and performs the division).

The governance parameter registry that controls this value performs no format check preventing zero, in contrast to the adjacent `Kip71BaseFeeDenominator` entry which explicitly requires `v != 0`: [3](#0-2) 

`NextMagmaBlockBaseFee` (and its header-verification counterpart `VerifyMagmaHeader`) is invoked during block header validation/state transition for every Magma-enabled block, so this is on the hot path reached by any submitted block/transaction once such a governance value is active: [4](#0-3) 

This is directly analogous to the reported Index Protocol bug: a numeric governance/config parameter that is not checked for zero before being used as a divisor, which is validated for one sibling field (`BaseFeeDenominator`/`normalizedTargetUnit`-style guard) but omitted for another (`GasTarget`).

### Impact Explanation
Once `GasTarget` is governed to `0`, every subsequent block whose gas usage is nonzero triggers a panic during `NextMagmaBlockBaseFee`, which is called from base-fee verification and computation paths used by all consensus nodes (`VerifyMagmaHeader`) as well as RPC-facing fee estimation code (`node/cn/gasprice/feehistory.go` calls `kip71Config.NextMagmaBlockBaseFee`). A panic in header verification during block processing halts the node — since this happens deterministically for every honest node evaluating the same header/governance state, it results in a full network halt (denial of service), not merely a single node crash. This qualifies as High severity given it fully blocks block production/verification network-wide.

### Likelihood Explanation
Reaching this path requires the `Kip71GasTarget` governance parameter to be set to `0`. Governance parameter changes are made through the standard `kaiax/gov` parameter-voting mechanism, which validates parameter format via `FormatChecker` before acceptance; because `Kip71GasTarget` uses `noopFormatChecker` (always returns true), a vote/set setting this value to `0` is accepted by the format checker layer without any additional guard, unlike other numeric governance fields such as `Kip71BaseFeeDenominator`, `IstanbulCommitteeSize`, or `GovernanceDeriveShaImpl`, which all impose explicit bounds. This makes the bug directly triggerable through the ordinary governance-parameter-setting flow that is explicitly in-scope per the acceptable analog domains (governance parameters, KIP-71 pricing, state transition and gas accounting).

### Recommendation
Add an explicit `FormatChecker` for `Kip71GasTarget` (and any other KIP-71 numeric field consumed as a divisor) rejecting `0`, mirroring `Kip71BaseFeeDenominator`:
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
Additionally, as defense-in-depth, `NextMagmaBlockBaseFee` in `params/kip71_config.go` should apply the same zero-fallback pattern used for `BaseFeeDenominator` to `GasTarget` before performing division, to avoid a panic even if an invalid zero value is ever present in a loaded `ChainConfig`.

### Proof of Concept
1. Governance sets `kip71.gastarget` to `0` (accepted because `Kip71GasTarget`'s `FormatChecker` is `noopFormatChecker`, which always returns `true`).
2. This value propagates into `KIP71Config.GasTarget` used by `NextMagmaBlockBaseFee`.
3. On the next Magma-enabled block with nonzero gas usage, execution enters the `parentGasUsed > gasTarget` branch: [5](#0-4) 
4. `y := x.Div(x, new(big.Int).SetUint64(gasTarget))` executes `big.Int.Div(x, 0)`, which panics with "division by zero" in the Go standard library.
5. This panic occurs inside header verification/base-fee computation reached by every node processing the block, halting block processing network-wide.

### Citations

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

**File:** params/kip71_config.go (L92-121)
```go
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
