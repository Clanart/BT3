## Finding [1](#0-0) 

The `Kip71GasTarget` governance parameter uses `noopFormatChecker`, meaning **any** `uint64` value (including `0`) is accepted, while the sibling parameter `Kip71BaseFeeDenominator` was explicitly hardened with a `v != 0` check: [2](#0-1) 

`GasTarget` is later used as a divisor in `NextMagmaBlockBaseFee`, with no zero-guard analogous to the one added for `BaseFeeDenominator`: [3](#0-2) 

### Title
Missing bounds validation on `kip71.gastarget` allows a zero value that causes a division-by-zero panic in base fee computation - (File: `kaiax/gov/param.go`, `params/kip71_config.go`)

### Summary
`Kip71GasTarget`'s `FormatChecker` is a no-op, unlike `Kip71BaseFeeDenominator` which was fixed to reject `0`. If a governance vote sets `kip71.gastarget = 0`, `NextMagmaBlockBaseFee` divides by `gasTarget` (a `big.Int` set to `0`) whenever `parentGasUsed != gasTarget`, causing a `math/big` division-by-zero panic on every node computing the next block's base fee.

### Finding Description
`NextMagmaBlockBaseFee` explicitly special-cases `BaseFeeDenominator == 0` to avoid a panic: [4](#0-3) 

But it performs no equivalent guard for `gasTarget`. In the "gas used above target" branch: [5](#0-4) 

and the "below target" branch: [6](#0-5) 

both divide by `new(big.Int).SetUint64(gasTarget)`. If `gasTarget == 0`, and `parentGasUsed != 0` (the "equal" branch is the only one that returns early when `parentGasUsed == gasTarget`), this line panics with a division-by-zero.

The governance vote-processing pipeline never rejects a zero `GasTarget`: the format checker is a no-op, and `checkConsistency` explicitly routes `gov.Kip71GasTarget` to the case that performs no additional checks beyond the format check: [7](#0-6) 

This is the exact bug class from the report: a `PPM`/divisor-like governance-settable parameter that lacks a "not zero"/bounds check, even though the analogous parameter (`BaseFeeDenominator`) already received that fix.

### Impact Explanation
Once a `kip71.gastarget = 0` vote is enacted (applied at the next epoch), **every** full node computing `VerifyMagmaHeader`/`NextMagmaBlockBaseFee` for any block whose gas usage differs from the target (i.e., essentially any real block) will panic. Since this code runs in block validation and header verification paths executed by all honest nodes identically, the result is a simultaneous chain-wide halt/crash — not a subtle divergence, but a full network-wide denial of service requiring node restarts/manual intervention to recover (similar to the reward-config `Ratio`/`Kip82Ratio` becoming misconfigured being "fixable" only via inactive/redeploy scenarios in the original report, except here it is a total outage instead of a merely non-functional feature).

### Likelihood Explanation
Reaching this requires a governance vote for `kip71.gastarget` to pass and be applied — this occurs through the standard governance voting flow (`gov.Vote` API / header-embedded votes), which is explicitly listed as an in-scope reachable surface ("governance parameters"). No format or consistency check prevents voting `0` for this parameter, unlike the sibling `BaseFeeDenominator`, which shows the developers already recognized and partially patched this exact class of bug but missed `GasTarget`.

### Recommendation
Add a `FormatChecker` for `Kip71GasTarget` (and audit `Kip71MaxBlockGasUsedForBaseFee`/`Kip71LowerBoundBaseFee`/`Kip71UpperBoundBaseFee` similarly) mirroring the `Kip71BaseFeeDenominator` fix, rejecting `v == 0`:
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
As defense in depth, `NextMagmaBlockBaseFee` should also guard against `gasTarget == 0` the same way it already guards `BaseFeeDenominator == 0`, since chain config / genesis values are a separate input path from governance votes.

### Proof of Concept
1. In single/ballot governance mode, submit a vote (or genesis-config) setting `kip71.gastarget` to `0`. [8](#0-7)  — the vote passes format validation because `FormatChecker` is `noopFormatChecker`.
2. Once the vote is enacted at the next epoch boundary, any subsequent block whose `GasUsed != 0` (i.e., `parentGasUsed != gasTarget`) triggers `NextMagmaBlockBaseFee`, entering the `parentGasUsed > gasTarget` branch. [9](#0-8) 
3. `x.Div(x, new(big.Int).SetUint64(gasTarget))` executes `big.Int.Div` with a zero divisor, panicking on every node that validates or extends the chain from that point, since `VerifyMagmaHeader` / `NextMagmaBlockBaseFee` are invoked uniformly by all honest nodes during header verification.

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

**File:** kaiax/gov/headergov/impl/header.go (L213-220)
```go
		// These votes are valid as long as it passes the format checks in NewVoteData(). No more checks here.
	case gov.GovernanceDeriveShaImpl, gov.GovernanceGovParamContract, gov.GovernanceGovernanceMode, gov.GovernanceUnitPrice,
		gov.IstanbulCommitteeSize, gov.IstanbulEpoch, gov.IstanbulPolicy,
		gov.Kip71BaseFeeDenominator, gov.Kip71GasTarget, gov.Kip71MaxBlockGasUsedForBaseFee,
		gov.RewardDeferredTxFee, gov.RewardKip82Ratio, gov.RewardMintingAmount, gov.RewardMinimumStake,
		gov.RewardProposerUpdateInterval, gov.RewardRatio, gov.RewardStakingRewardThreshold,
		gov.RewardStakingUpdateInterval, gov.RewardUseFlexReward, gov.RewardUseGiniCoeff:
		return nil
```
