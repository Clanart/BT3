## Analog Found: Division-by-zero in KIP-71 base fee calculation when `GasTarget` governance parameter is zero

### Title
Governance-settable `Kip71GasTarget = 0` causes a division-by-zero panic in `NextMagmaBlockBaseFee` - (File: `params/kip71_config.go`)

### Summary
The `Kip71GasTarget` governance parameter (used in the KIP-71/Magma dynamic base-fee formula) has no non-zero validation, unlike its sibling parameter `Kip71BaseFeeDenominator` which explicitly rejects zero. If governance sets `reward.gastarget` (internally `Kip71GasTarget`) to `0`, every node computing the next block's base fee divides by this zero value and panics, exactly mirroring the reported bug class where a divisor derived from a governance/user-controlled total can become zero and is never guarded against.

### Finding Description
`KIP71Config.NextMagmaBlockBaseFee` uses `kc.GasTarget` as a divisor in both the "gas used above target" and "gas used below target" branches: [1](#0-0) 

Specifically:
```go
gasTarget := kc.GasTarget
...
parentGasUsed := min(parentHeaderGasUsed, upperGasLimit)
if parentGasUsed == gasTarget {
    return makeEvenByFloor(parentBaseFee)
} else if parentGasUsed > gasTarget {
    ...
    y := x.Div(x, new(big.Int).SetUint64(gasTarget))   // divides by gasTarget
    ...
} else {
    ...
    y := x.Div(x, new(big.Int).SetUint64(gasTarget))   // divides by gasTarget
    ...
}
``` [2](#0-1) 

Unlike `BaseFeeDenominator`, which is defensively defaulted to `64` when zero to "avoid panic" [3](#0-2) , `GasTarget` has no such guard. If `gasTarget == 0` and any block has nonzero gas usage (`parentGasUsed > gasTarget`, i.e. `parentGasUsed > 0`), the code proceeds to `x.Div(x, big.NewInt(0))`, which panics with "division by zero" in Go's `math/big`.

Crucially, the governance format checker for this parameter is a no-op, permitting `0` as a valid value:
```go
Kip71GasTarget: {
    Canonicalizer: uint64Canonicalizer,
    FormatChecker: noopFormatChecker,
    ...
},
``` [4](#0-3) 

Compare this to `Kip71BaseFeeDenominator`, whose checker explicitly requires `v != 0`:
```go
Kip71BaseFeeDenominator: {
    ...
    FormatChecker: func(cv any) bool {
        v, ok := cv.(uint64)
        return ok && v != 0
    },
    ...
},
``` [5](#0-4) 

This asymmetry indicates the `GasTarget == 0` case was overlooked, exactly analogous to the reported finding where a total that could legitimately reach zero (or in this case, be set to zero) was not checked before being used as a divisor.

### Impact Explanation
`NextMagmaBlockBaseFee` is invoked on the header-verification/preparation path for every block once Magma is active, via `VerifyMagmaHeader` [6](#0-5)  and via header preparation/fee-history RPC processing [7](#0-6) . If governance sets `Kip71GasTarget` to `0`, then as soon as any block is produced with gas used greater than zero, all full nodes computing/verifying the next base fee will panic, resulting in a chain-wide halt / crash (a state-transition and gas-accounting invariant failure affecting all honest nodes identically, not just one peer). This is a consensus-critical Denial-of-Service and falls squarely in scope (KIP-71 pricing / state transition and gas accounting / governance parameters).

### Likelihood Explanation
Setting `Kip71GasTarget` requires an on-chain governance vote/parameter change accepted by the `kaiax/gov` module, which enforces no format restriction (`noopFormatChecker`) on this value, so `0` will be silently accepted and applied. Once effective, the very next block with any gas usage triggers the panic — no attacker-controlled transaction content is even needed beyond the governance change itself.

### Recommendation
Add a `FormatChecker` for `Kip71GasTarget` (and defensively for `Kip71MaxBlockGasUsedForBaseFee`) that rejects `0`, consistent with the existing guard on `Kip71BaseFeeDenominator`. Additionally, add a defensive zero-check inside `NextMagmaBlockBaseFee` (mirroring the existing `BaseFeeDenominator` zero fallback) before using `gasTarget` as a divisor, to prevent panics even if an invalid config slips through another code path (e.g. genesis configuration).

### Proof of Concept
1. Governance sets `reward.gastarget` (Kip71GasTarget) to `0` via a passing governance vote (accepted because `FormatChecker` is a no-op) [4](#0-3) .
2. Once the new parameter set becomes effective, any block with `parentHeaderGasUsed > 0` is proposed.
3. On header verification/preparation, `KIP71Config.NextMagmaBlockBaseFee` computes `parentGasUsed := min(parentHeaderGasUsed, upperGasLimit)`, which is `> 0 == gasTarget`, entering the `parentGasUsed > gasTarget` branch.
4. `y := x.Div(x, new(big.Int).SetUint64(gasTarget))` executes `big.Int.Div(x, 0)`, causing a runtime panic on every node processing this header [8](#0-7) .

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

**File:** kaiax/gov/param.go (L310-315)
```go
	Kip71BaseFeeDenominator: {
		Canonicalizer: uint64Canonicalizer,
		FormatChecker: func(cv any) bool {
			v, ok := cv.(uint64)
			return ok && v != 0
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

**File:** node/cn/gasprice/feehistory.go (L111-115)
```go
	if isNextBlockMagma {
		bf.results.nextBaseFee = kip71Config.NextMagmaBlockBaseFee(bf.header.Number, bf.header.BaseFee, bf.header.GasUsed)
	} else {
		bf.results.nextBaseFee = new(big.Int).SetUint64(params.ZeroBaseFee)
	}
```
