### Title
Missing zero-value check on `Kip71GasTarget` governance parameter causes division-by-zero panic in base fee calculation - ([File: params/kip71_config.go])

### Summary
The `Kip71GasTarget` governance parameter uses `noopFormatChecker` (no validation), unlike sibling parameter `Kip71BaseFeeDenominator` which explicitly rejects zero. If `GasTarget` is set to `0` via governance (contract-based `GovParam.setParam`/`setParamIn` or header-vote), `NextMagmaBlockBaseFee` performs a `big.Int` division by `gasTarget`, causing a runtime panic in every node computing the next block's base fee.

### Finding Description
In `kaiax/gov/param.go`, most numeric KIP-71 parameters that are later used as divisors are validated to be non-zero — e.g. `Kip71BaseFeeDenominator`'s `FormatChecker` explicitly checks `v != 0`: [1](#0-0) 

However, `Kip71GasTarget` uses `noopFormatChecker`, meaning any `uint64` value including `0` is accepted: [2](#0-1) 

This unchecked `GasTarget` flows into `params.KIP71Config` (via `ParamSet.ToMap`/`getChainConfig` and `pset.ToKip71Config()`), and is used directly as a divisor in `NextMagmaBlockBaseFee`: [3](#0-2) 

Specifically, whenever `parentGasUsed != gasTarget` (which is essentially always true once `gasTarget == 0` and any gas is used), the code computes:
```
y := x.Div(x, new(big.Int).SetUint64(gasTarget))
```
`big.Int.Div` panics with "division by zero" when the divisor is zero. Note that the code already defensively guards against `BaseFeeDenominator == 0` (falling back to 64) at line 71-76, but has no equivalent guard for `GasTarget`.

### Impact Explanation
`NextMagmaBlockBaseFee` is invoked during header preparation, header validation (`VerifyMagmaHeader`), and fee-history/gas-price oracle computations for every block once the Magma hardfork is active — this is on the consensus-critical block assembly and validation path executed by all full nodes/CNs. A governance vote (which is a normal, in-scope governance parameter update path, not a privileged-node/operator-only exploit) setting `kip71.gastarget = 0` would cause every node computing or verifying the next block's base fee to panic/crash, halting block production network-wide — a chain-wide denial of service and consensus/state-divergence risk (nodes that crash vs. any that might not reach this code path).

### Likelihood Explanation
Governance parameter updates are a supported, reachable mechanism (via `GovParamContract.setParam`/`setParamIn` calls or header votes) and are explicitly listed as an in-scope surface. Setting a governance numeric parameter to `0` is a trivial, low-cost action (no special privilege beyond normal governance/vote submission flow), and unlike `Kip71BaseFeeDenominator`, there is no format check preventing `0` for `Kip71GasTarget`. Once activated, the panic triggers deterministically on the very next block after any non-zero gas usage.

### Recommendation
Add a non-zero `FormatChecker` for `Kip71GasTarget` (and audit `Kip71MaxBlockGasUsedForBaseFee`/`Kip71LowerBoundBaseFee`/`Kip71UpperBoundBaseFee` for similar unchecked zero/invalid-range issues), mirroring the existing `Kip71BaseFeeDenominator` pattern:
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
Additionally, consider adding a defensive zero-check fallback inside `NextMagmaBlockBaseFee` (similar to the existing `BaseFeeDenominator == 0` fallback) as a defense-in-depth measure against any parameter that bypasses the format checker (e.g. via direct `ChainConfig` genesis misconfiguration).

### Proof of Concept
1. Deploy/enable the `GovParamContract` and submit a governance parameter change setting `kip71.gastarget` (canonicalized as `uint64(0)`) — this passes `FormatChecker` because `noopFormatChecker` always returns true.
2. Once the parameter activates at a target block, any block whose parent used any non-zero gas (`parentGasUsed > 0`) with `gasTarget == 0` hits the `parentGasUsed > gasTarget` branch in `NextMagmaBlockBaseFee`: [4](#0-3) 
3. `x.Div(x, new(big.Int).SetUint64(gasTarget))` panics with division by zero, crashing the node process during header preparation/validation for every CN/full node computing that block.

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
