Looking at the codebase, I found a legitimate analog matching the report's bug class: an unvalidated governance-set numeric parameter with no bounds check that breaks downstream arithmetic in a core protocol function.

### Title
`Kip71GasTarget` governance parameter can be set to 0 causing division-by-zero panic in `NextMagmaBlockBaseFee` - ([File: params/kip71_config.go])

### Summary
The `Kip71GasTarget` governance parameter is registered with a `noopFormatChecker` (no validation) in [1](#0-0)  and is not subject to any consistency check in `checkConsistency` for header votes, unlike `Kip71LowerBoundBaseFee`/`Kip71UpperBoundBaseFee` which are cross-validated against each other in [2](#0-1) . If `GasTarget` is voted/set to `0`, it is stored and later consumed unguarded in `NextMagmaBlockBaseFee`, unlike `BaseFeeDenominator` which has an explicit zero-guard (`if kc.BaseFeeDenominator == 0 { ... }`) in [3](#0-2) .

### Finding Description
`NextMagmaBlockBaseFee` divides by `gasTarget` directly with no zero-check: [4](#0-3) 
When `parentGasUsed != gasTarget` (true for essentially any non-empty block once `gasTarget == 0`), the code executes `x.Div(x, new(big.Int).SetUint64(gasTarget))` with `gasTarget = 0`, which is a division by zero on `big.Int`, causing a runtime panic. This exactly mirrors the report's bug class: an owner/governance-controlled setter (`GovernanceUnitPrice`-style parameter, analogous to `changeMaxGenerationId`) accepts any value without validating that it remains compatible with the arithmetic/logic that consumes it downstream (`mintPlayers` in the original report; `NextMagmaBlockBaseFee` here).

### Impact Explanation
`NextMagmaBlockBaseFee` is invoked from `VerifyMagmaHeader`, which is used in core block validation/assembly paths, including `blockchain/block_validator.go`, `work/worker.go`, `blockchain/chain_makers.go`, and `blockchain/tx_pool.go`. A panic in this function during header verification or block building would crash the node process on every node that processes or builds a block after the parameter is set — a chain-wide halt, not a resource-only DoS. This is a critical availability/consensus-processing failure triggered purely by an unvalidated governance parameter value.

### Likelihood Explanation
The `GasTarget` parameter is only settable through governance voting (header-vote path in `headergov` or contract-vote path in `contractgov`), so the "attacker" here is the governance/GC actor — directly analogous to the original report's "owner is the only line of defence" caveat. There is no format or consistency check preventing `0`, so a single governance vote setting `kip71.gastarget = 0` deterministically triggers the panic on the very next non-empty block for all nodes.

### Recommendation
Add a `FormatChecker` for `Kip71GasTarget` in [1](#0-0)  that rejects `0` (mirroring the `IstanbulCommitteeSize` checker pattern), and/or add a defensive zero-guard for `gasTarget` in `NextMagmaBlockBaseFee` in [5](#0-4)  similar to the existing `BaseFeeDenominator` zero-guard.

### Proof of Concept
1. Governance votes to set `kip71.gastarget` to `0` (via `governance_vote` header-vote path or `GovParam.setParam` contract path); no validator rejects this because the `FormatChecker` is a no-op and `checkConsistency` has no case for `Kip71GasTarget`.
2. Once the vote is ratified and the parameter takes effect at the next governance-effective block, any subsequent block with `parentHeaderGasUsed != 0` triggers `NextMagmaBlockBaseFee` to compute `gasUsedDelta := parentGasUsed - gasTarget` (nonzero) then `x.Div(x, new(big.Int).SetUint64(0))`.
3. `big.Int.Div` with a zero divisor panics, crashing every node that calls `VerifyMagmaHeader`/`NextMagmaBlockBaseFee` while validating or building that block, halting the chain.

### Citations

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

**File:** kaiax/gov/headergov/impl/header.go (L188-201)
```go
	case gov.Kip71LowerBoundBaseFee:
		params := h.GetParamSet(blockNum)
		if vote.Value().(uint64) > params.UpperBoundBaseFee {
			return ErrLowerBoundBaseFee
		} else {
			return nil
		}
	case gov.Kip71UpperBoundBaseFee:
		params := h.GetParamSet(blockNum)
		if vote.Value().(uint64) < params.LowerBoundBaseFee {
			return ErrUpperBoundBaseFee
		} else {
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
