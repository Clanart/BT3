### Title
Missing zero-check on `kip71.gastarget` governance parameter causes division-by-zero panic in `NextMagmaBlockBaseFee` - (File: `params/kip71_config.go`)

### Summary
The `kip71.gastarget` governance parameter can be set to `0` through the GovParam contract (contract governance) or header governance voting, because its `FormatChecker` is a no-op that accepts any `uint64` value. Unlike the sibling parameter `kip71.basefeedenominator` — which has an explicit `v != 0` format check — `kip71.gastarget` has no such guard. When `GasTarget == 0`, `NextMagmaBlockBaseFee` in `params/kip71_config.go` performs `big.Int.Div(x, new(big.Int).SetUint64(gasTarget))`, which panics on division by zero for any block where gas is used. This is directly analogous to the reported bug where the Portfolios owner could set `numPeriods = 0` and brick the FutureCash market's `_isValidBlock` check — here, the governance owner can set `gastarget = 0` and brick base-fee computation for every subsequent block.

### Finding Description
`Kip71GasTarget` is defined with `FormatChecker: noopFormatChecker`, which always returns `true` regardless of the input value: [1](#0-0) 

This contrasts with `Kip71BaseFeeDenominator`, whose format checker explicitly rejects zero: [2](#0-1) 

Consequently, a governance value of `gastarget = 0` will canonicalize and pass validation successfully, and be accepted into the `ParamSet`/`KIP71Config` via `ParamSet.Set`: [3](#0-2) 

`NextMagmaBlockBaseFee` reads `kc.GasTarget` directly into a local `gasTarget` and, unlike `BaseFeeDenominator` (which has an explicit `== 0` fallback guard), performs no such check before using it as a divisor: [4](#0-3) [5](#0-4) 

When `gasTarget == 0` and `parentGasUsed > 0` (the common case for any active chain), execution enters the `parentGasUsed > gasTarget` branch and calls `x.Div(x, new(big.Int).SetUint64(gasTarget))` with a zero divisor. Go's `math/big.Int.Div` panics with "division by zero" in this situation, rather than returning an error.

This function is invoked from `VerifyMagmaHeader`, which is called during block header verification (`blockchain/block_validator.go`) and block building (`work/worker.go`, `blockchain/chain_makers.go`), meaning every node validating or building a block after the malicious parameter takes effect will panic.

### Impact Explanation
Once `kip71.gastarget = 0` becomes effective (either through the governing node's single-owner setting or a GC on-chain vote via the `GovParam` contract's `setParamIn`/`setParam`, both gated by `Ownable`), every node in the network computing the next Magma base fee for a non-empty block will hit a Go runtime panic in `NextMagmaBlockBaseFee`. This halts block validation and block production network-wide — a chain-halting denial of service far more severe than the referenced report's per-market lockup, since it affects consensus/liveness for the entire chain rather than a single market.

### Likelihood Explanation
The governance parameter is only settable by a privileged actor (the governing node in `single` mode, or GC members voting in `ballot`/contract-governance mode), analogous to the "owner" role in the original report. The missing zero-check is a straightforward oversight — the codebase clearly anticipated and guarded against `BaseFeeDenominator == 0` in the exact same function but omitted the equivalent guard for `GasTarget`, showing the vulnerability class was known but incompletely mitigated for a sibling parameter.

### Recommendation
Add a `FormatChecker` for `Kip71GasTarget` that rejects `0` (mirroring `Kip71BaseFeeDenominator`'s `v != 0` check) at `kaiax/gov/param.go`. As defense in depth, also add an explicit zero-guard for `gasTarget` inside `NextMagmaBlockBaseFee` in `params/kip71_config.go`, following the same fallback pattern already used for `baseFeeDenominator`.

### Proof of Concept
1. As the governing node (or via GC on-chain vote), call `GovParam.setParam("kip71.gastarget", true, <8-byte encoding of uint64(0)>, activationBlock)` — this succeeds because `Kip71GasTarget`'s format checker is a no-op that never rejects any value, unlike `kip71.basefeedenominator`. [1](#0-0) 
2. Once the parameter activates at `activationBlock`, any block with `parentGasUsed > 0` triggers `NextMagmaBlockBaseFee` to compute `x.Div(x, new(big.Int).SetUint64(0))`. [6](#0-5) 
3. This call panics with "division by zero" inside every node's header verification (`VerifyMagmaHeader`) and block-building path, halting the chain.

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

**File:** kaiax/gov/paramset.go (L75-84)
```go
	case Kip71BaseFeeDenominator:
		p.BaseFeeDenominator, ok = cv.(uint64)
	case Kip71GasTarget:
		p.GasTarget, ok = cv.(uint64)
	case Kip71LowerBoundBaseFee:
		p.LowerBoundBaseFee, ok = cv.(uint64)
	case Kip71MaxBlockGasUsedForBaseFee:
		p.MaxBlockGasUsedForBaseFee, ok = cv.(uint64)
	case Kip71UpperBoundBaseFee:
		p.UpperBoundBaseFee, ok = cv.(uint64)
```

**File:** params/kip71_config.go (L70-103)
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
```

**File:** params/kip71_config.go (L110-121)
```go
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
