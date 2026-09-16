Found a strong analog. The bug-class (unchecked division causing div-by-zero) maps directly to Kaia's KIP-71 base fee calculation, where the `GasTarget` governance parameter lacks a non-zero format check while `BaseFeeDenominator` has one.

### Title
Unchecked `GasTarget` governance parameter can be set to zero causing a division-by-zero panic in KIP-71 base fee calculation - (File: params/kip71_config.go)

### Summary
The `Kip71GasTarget` governance parameter is registered with a `noopFormatChecker`, allowing it to be set to `0` via a governance vote. This value flows unchecked into `KIP71Config.NextMagmaBlockBaseFee`, which divides by `gasTarget` without a zero-check (unlike the sibling `BaseFeeDenominator`, which explicitly falls back to a safe default when zero). A `GasTarget` of `0` combined with any block that has non-zero gas usage causes a `math/big` division-by-zero panic, crashing every node that computes or validates the next block's base fee.

### Finding Description
The `Kip71GasTarget` parameter definition uses `noopFormatChecker`, which always returns `true` regardless of value, meaning any `uint64` including `0` passes validation: [1](#0-0) 

Compare this to `Kip71BaseFeeDenominator`, which explicitly rejects zero: [2](#0-1) 

The consistency check in header governance processing (`checkConsistency`) also performs no additional validation for `Kip71GasTarget`; it is grouped with parameters that only rely on the (no-op) format check: [3](#0-2) 

The unchecked `GasTarget` value is stored in `ParamSet.GasTarget` and converted to a `KIP71Config` via `ToKip71Config`: [4](#0-3) 

`KIP71Config.NextMagmaBlockBaseFee` computes the next block's base fee based on `parentGasUsed` versus `gasTarget`. Note that `BaseFeeDenominator` is defensively substituted with `64` if it is zero, but `gasTarget` has no equivalent guard: [5](#0-4) 

When `parentGasUsed > gasTarget` (true for essentially any block with `GasUsed > 0` if `gasTarget == 0`), the code divides by `new(big.Int).SetUint64(gasTarget)`, which is zero, causing `math/big`'s `Div` to panic: [6](#0-5) 

The symmetric "decrease" branch performs the same unguarded division: [7](#0-6) 

This function is invoked both during header verification (`VerifyMagmaHeader`) and fee-history/base-fee computations used by every node processing new blocks: [8](#0-7) [9](#0-8) 

### Impact Explanation
Once `GasTarget` is set to `0` through a successful governance vote (a normal governance-parameter-setting mechanism, not a p2p/consensus-message exploit), the very next block containing any gas usage will cause `NextMagmaBlockBaseFee` to panic with a division-by-zero error in `math/big`. Since this function executes during block header validation/base-fee derivation on every node (validators and full nodes alike) as part of ordinary state-transition/block-processing logic, it results in a chain-wide node crash/halt — an availability-critical failure affecting the entire network, not a resource-only or isolated DoS.

### Likelihood Explanation
Likelihood is high in any deployment where `Kip71GasTarget` governance votes are permitted (e.g., single-governance mode where the governing node can directly set governance parameters, or ballot mode once passed). No additional access beyond the standard governance-vote mechanism is required, and the format checker that should reject invalid values (`noopFormatChecker`) provides no protection, unlike the analogous and properly-guarded `BaseFeeDenominator` parameter.

### Recommendation
Add a non-zero (and ideally non-degenerate, e.g. `> 0`) format check to the `Kip71GasTarget` parameter definition, mirroring the existing `Kip71BaseFeeDenominator` check:
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
Additionally, as defense in depth, `NextMagmaBlockBaseFee` in `params/kip71_config.go` should guard against `gasTarget == 0` the same way it already guards `BaseFeeDenominator == 0`, to prevent any other governance/config path from reintroducing the same panic.

### Proof of Concept
1. Under single governance mode, the governing node casts a governance vote setting `kip71.gastarget = 0` (accepted because `Kip71GasTarget`'s `FormatChecker` is `noopFormatChecker`, see [1](#0-0) ).
2. The vote is processed with no additional consistency check ( [3](#0-2) ) and becomes effective in the `ParamSet` for the next epoch/block.
3. `ParamSet.ToKip71Config()` propagates `GasTarget: 0` into the `KIP71Config` used for base-fee computation ( [4](#0-3) ).
4. On the next block that has `GasUsed > 0` (virtually guaranteed), `NextMagmaBlockBaseFee` executes `parentGasUsed > gasTarget` (`>0`), then calls `x.Div(x, new(big.Int).SetUint64(gasTarget))` with `gasTarget = 0` ( [6](#0-5) ), causing a `math/big` division-by-zero panic and crashing the node process during standard block header validation/base-fee derivation.

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

**File:** kaiax/gov/headergov/impl/header.go (L214-220)
```go
	case gov.GovernanceDeriveShaImpl, gov.GovernanceGovParamContract, gov.GovernanceGovernanceMode, gov.GovernanceUnitPrice,
		gov.IstanbulCommitteeSize, gov.IstanbulEpoch, gov.IstanbulPolicy,
		gov.Kip71BaseFeeDenominator, gov.Kip71GasTarget, gov.Kip71MaxBlockGasUsedForBaseFee,
		gov.RewardDeferredTxFee, gov.RewardKip82Ratio, gov.RewardMintingAmount, gov.RewardMinimumStake,
		gov.RewardProposerUpdateInterval, gov.RewardRatio, gov.RewardStakingRewardThreshold,
		gov.RewardStakingUpdateInterval, gov.RewardUseFlexReward, gov.RewardUseGiniCoeff:
		return nil
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

**File:** params/kip71_config.go (L115-121)
```go
		// Otherwise if the parent block used less gas than its target,
		// the baseFee of the next block should decrease.
		// baseFeeDelta = parentBaseFee * (gasTarget - parentGasUsed) / gasTarget / baseFeeDenominator
		gasUsedDelta := new(big.Int).SetUint64(gasTarget - parentGasUsed)
		x := new(big.Int).Mul(parentBaseFee, gasUsedDelta)
		y := x.Div(x, new(big.Int).SetUint64(gasTarget))
		baseFeeDelta := x.Div(y, baseFeeDenominator)
```

**File:** node/cn/gasprice/feehistory.go (L108-115)
```go
	if bf.results.baseFee = bf.header.BaseFee; bf.results.baseFee == nil {
		bf.results.baseFee = new(big.Int).SetUint64(params.ZeroBaseFee)
	}
	if isNextBlockMagma {
		bf.results.nextBaseFee = kip71Config.NextMagmaBlockBaseFee(bf.header.Number, bf.header.BaseFee, bf.header.GasUsed)
	} else {
		bf.results.nextBaseFee = new(big.Int).SetUint64(params.ZeroBaseFee)
	}
```
