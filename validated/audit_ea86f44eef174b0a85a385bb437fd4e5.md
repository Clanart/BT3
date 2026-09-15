## Title
KIP-71 GasTarget governance parameter accepts zero, causing division-by-zero panic in `NextMagmaBlockBaseFee` - (File: params/kip71_config.go)

### Summary
The `Kip71GasTarget` governance parameter uses a `noopFormatChecker` that performs no validation, unlike its sibling parameter `Kip71BaseFeeDenominator` which explicitly rejects `0` (`v != 0`). If `GasTarget` is set to `0` via governance vote, `NextMagmaBlockBaseFee` divides by `gasTarget` without a zero guard, causing a `big.Int` division-by-zero panic during block base-fee computation. This mirrors the reported GMX pattern where a single un-guarded parameter (`MIN_COLLATERAL_USD`/here `GasTarget`) being zero skips one safety branch and then hits a division that reverts/panics instead of being handled safely.

### Finding Description
`NextMagmaBlockBaseFee` computes the KIP-71 (Magma) base fee update using the governance-controlled `GasTarget`: [1](#0-0) 

Specifically, when `parentGasUsed != gasTarget`, the code divides by `gasTarget` in both the "above target" and "below target" branches:
- `y := x.Div(x, new(big.Int).SetUint64(gasTarget))` (line 102, "above target" branch)
- `y := x.Div(x, new(big.Int).SetUint64(gasTarget))` (line 120, "below target" branch)

There is a special-case guard only for `BaseFeeDenominator == 0` (falls back to `64`): [2](#0-1) 

No equivalent guard exists for `GasTarget == 0`. If `GasTarget` is `0` and `parentGasUsed > 0`, the code takes the "above target" branch (`parentGasUsed > gasTarget`), computing `gasUsedDelta := new(big.Int).SetUint64(parentGasUsed - gasTarget)` and then dividing `x` by `gasTarget` (which is `0`), triggering Go's `big.Int` division-by-zero panic.

The governance parameter registry enforces no format check for `GasTarget`: [3](#0-2) 

This is `noopFormatChecker`, in contrast to `Kip71BaseFeeDenominator`, which explicitly requires `v != 0`: [4](#0-3) 

`NextMagmaBlockBaseFee` is called on every block during header preparation/validation and fee-history computation (e.g. `feehistory.go`), so once a governance vote sets `GasTarget` to `0`, all subsequent block processing (header validation, base fee computation, and RPC fee history queries) that reaches the "not equal to target" branches will panic.

### Impact Explanation
A panic during block header validation/base-fee computation is a consensus-critical crash: every node computing `NextMagmaBlockBaseFee` (in block validation, block assembly, or `eth_feeHistory`/`kaia_feeHistory` RPC serving) will crash or halt in the same way, since the parameter is a chain-wide governance value applied identically at all nodes. This does not directly move funds, but it is a network-wide denial-of-service / node-halting bug reachable purely through a governance parameter update (no privileged off-chain access needed once the vote passes), and it prevents the network from continuing to produce/validate blocks — a much stronger impact than a simple missed liquidation, since it stops chain progress entirely rather than allowing one bad position to survive.

### Likelihood Explanation
Likelihood depends on a governance vote successfully setting `governance.kip71.gastarget` to `0`. Because `noopFormatChecker` performs no validation, the governance parameter pipeline (`kaiax/gov`) will accept and canonicalize the value without rejection, unlike the `BaseFeeDenominator` case which is explicitly guarded. Any governance-authorized proposer/voter (or misconfiguration) reaching consensus on this parameter would trigger the bug on the very next block where `parentGasUsed != 0`. This requires governance-level write access (not an arbitrary unprivileged tx sender), but is fully reachable through the standard governance parameter update path with no additional privilege beyond normal governance voting rights, and no additional code path bypasses are needed.

### Recommendation
Add a zero-check for `GasTarget` in `NextMagmaBlockBaseFee`, mirroring the existing `BaseFeeDenominator` fallback, e.g.:
```go
gasTarget := kc.GasTarget
if gasTarget == 0 {
    gasTarget = params.DefaultGasTarget // or another safe non-zero fallback
}
```
Additionally, update the `Kip71GasTarget` `FormatChecker` in `kaiax/gov/param.go` to reject `0` the same way `Kip71BaseFeeDenominator` does (`return ok && v != 0`), preventing the invalid value from being accepted into the parameter set in the first place.

### Proof of Concept
1. Submit and pass a governance vote (or configure genesis/chain config) setting `governance.kip71.gastarget = 0`, which is accepted because `Kip71GasTarget`'s `FormatChecker` is `noopFormatChecker` (`kaiax/gov/param.go:324-334`).
2. Once activated, the next block that is not exactly `gasTarget` gas used (i.e. any block with `GasUsed > 0`, since `gasTarget == 0`) causes `NextMagmaBlockBaseFee` to enter the `parentGasUsed > gasTarget` branch (`params/kip71_config.go:92-109`).
3. Execution reaches `y := x.Div(x, new(big.Int).SetUint64(gasTarget))` at line 102 with `gasTarget == 0`.
4. `big.Int.Div` on a zero divisor panics ("division by zero"), crashing block validation/assembly on every node computing the header base fee (and any RPC call to `feehistory.go` for the affected block range), halting the chain.

**Uncertainty note:** I could not fully verify whether an additional upstream validation layer (e.g. a governance proposal validity check outside `kaiax/gov/param.go`) exists elsewhere in the codebase that might independently reject `GasTarget == 0` before it reaches `ChainConfig`; the index did not surface such a check in the files inspected. If such a check exists, it should be confirmed with a full repository scan (e.g. via a Devin session) before treating this as immediately exploitable in production configuration.

### Citations

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
