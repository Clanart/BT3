### Title
Division-by-zero panic in `NextMagmaBlockBaseFee` when the `governance.kip71.gastarget` parameter is zero - (File: `params/kip71_config.go`)

### Summary
`KIP71Config.NextMagmaBlockBaseFee` computes the next block's base fee by dividing by `kc.GasTarget` without any zero-guard, unlike the adjacent `BaseFeeDenominator` value which is explicitly defended against being zero.

### Finding Description
In `NextMagmaBlockBaseFee`, the code protects against a zero `BaseFeeDenominator`: [1](#0-0) 

but performs no equivalent check on `gasTarget` before using it as a divisor in both the "above target" and "below target" branches: [2](#0-1) [3](#0-2) 

`gasTarget := kc.GasTarget` is a plain `uint64` taken directly from governance configuration: [4](#0-3) [5](#0-4) 

If `gasTarget == 0` and `parentGasUsed != 0` (i.e., not the special-case equality branch at line 90), the code reaches `x.Div(x, new(big.Int).SetUint64(gasTarget))`, which is a division by zero. Go's `math/big.Int.Div` panics on a zero divisor, rather than returning an error. This function is called from consensus-critical header verification (`VerifyMagmaHeader`) as well as fee suggestion/base-fee derivation paths, meaning every full node processing a block after such a parameter takes effect would panic.

This value is set via the governance parameter `governance.kip71.gastarget` / `Kip71GasTarget`, which is exposed through the `kaiax/gov` parameter framework: [6](#0-5) 

I was unable to fully confirm within this session whether the `FormatChecker` registered for `Kip71GasTarget` in `kaiax/gov/param.go` explicitly rejects a value of `0`; this should be verified with direct access to the `kaiax/gov/param.go` parameter table (the definitions for `Kip71GasTarget` were only partially inspected).

### Impact Explanation
If the `GasTarget` governance parameter can be set to `0` (whether via a legitimate/malicious governance vote, misconfiguration, or an insufficiently validated genesis/param update), every subsequent call to `NextMagmaBlockBaseFee`/`VerifyMagmaHeader` on any block where `parentGasUsed != gasTarget` (i.e., `parentGasUsed != 0`) will panic. Because this function is invoked during header verification and gas price suggestion in the normal block-processing path, a panic here would crash full nodes trying to process/verify the next block, causing a network-wide liveness/consensus halt rather than an "incorrect value" issue — a much more severe consequence than the plain rounding-loss described in the source report.

### Likelihood Explanation
Likelihood depends entirely on whether the governance parameter validation (`FormatChecker`) for `Kip71GasTarget` permits `0`. If it does not enforce `> 0`, then a single governance vote (or a bug in initial chain configuration/genesis) reaching `gasTarget = 0` is sufficient to trigger the panic on the very next non-empty block, affecting all nodes identically (deterministic crash, not a divergence). Given the asymmetric treatment versus `BaseFeeDenominator` (which is defensively coded against 0), this looks like an overlooked edge case rather than an intentionally-guarded parameter.

### Recommendation
Add an explicit zero-check/fallback for `gasTarget` symmetric to the existing `baseFeeDenominator` handling, e.g., treat `gasTarget == 0` as an error or substitute a safe default before it is used as a divisor in `NextMagmaBlockBaseFee`. Additionally, ensure the governance parameter `FormatChecker` for `Kip71GasTarget` rejects `0` at the point of governance vote validation, so an invalid value can never reach `ChainConfig`/`ParamSet`.

### Proof of Concept
1. Via governance vote (or genesis misconfiguration), set `governance.kip71.gastarget` (`Kip71GasTarget`) to `0`.
2. Once the vote/param takes effect at a block boundary, mine/process any block whose `parentHeaderGasUsed != 0`.
3. `NextMagmaBlockBaseFee` computes `gasUsedDelta` and then calls `x.Div(x, new(big.Int).SetUint64(0))`, which panics per Go's `math/big` semantics.
4. Because this function is invoked from header verification (`VerifyMagmaHeader`) in the normal block-import path, the panic crashes every node attempting to import/verify that block, halting the chain. [7](#0-6)

### Citations

**File:** params/kip71_config.go (L27-33)
```go
type KIP71Config struct {
	LowerBoundBaseFee         uint64 `json:"lowerboundbasefee"`         // Minimum base fee for dynamic gas price
	UpperBoundBaseFee         uint64 `json:"upperboundbasefee"`         // Maximum base fee for dynamic gas price
	GasTarget                 uint64 `json:"gastarget"`                 // Gauge parameter increasing or decreasing gas price
	MaxBlockGasUsedForBaseFee uint64 `json:"maxblockgasusedforbasefee"` // Maximum network and process capacity to allow in a block
	BaseFeeDenominator        uint64 `json:"basefeedenominator"`        // For normalizing effect of the rapid change like impulse gas used
}
```

**File:** params/kip71_config.go (L58-128)
```go
func (kc *KIP71Config) NextMagmaBlockBaseFee(parentHeaderNumber *big.Int, parentHeaderBaseFee *big.Int, parentHeaderGasUsed uint64) *big.Int {
	// governance parameters
	lowerBoundBaseFee := new(big.Int).SetUint64(kc.LowerBoundBaseFee)
	upperBoundBaseFee := new(big.Int).SetUint64(kc.UpperBoundBaseFee)
	makeEvenByCeil(lowerBoundBaseFee)
	makeEvenByFloor(upperBoundBaseFee)

	// If the parent is the magma disabled block or genesis, then return the lowerBoundBaseFee (default 25ston)
	if parentHeaderNumber.Cmp(new(big.Int).SetUint64(0)) == 0 || parentHeaderBaseFee == nil {
		return makeEvenByFloor(lowerBoundBaseFee)
	}

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

**File:** kaiax/gov/paramset.go (L75-78)
```go
	case Kip71BaseFeeDenominator:
		p.BaseFeeDenominator, ok = cv.(uint64)
	case Kip71GasTarget:
		p.GasTarget, ok = cv.(uint64)
```
