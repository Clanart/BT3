## Title
Setting governance parameter `kip71.gastarget` to zero via a governance vote causes chain-wide Denial of Service / panic in `KIP71Config.NextMagmaBlockBaseFee()` - (File: `params/kip71_config.go`)

### Summary
The governance parameter `kip71.gastarget` (`gov.Kip71GasTarget`) can be set to `0` through a normal governance vote because its format-validation function performs no check at all. This value is later used as a divisor in the base-fee calculation function that every node must execute for every block after the Magma hardfork, producing an unrecovered integer division-by-zero panic and halting block processing network-wide.

### Finding Description
`Kip71GasTarget` is registered with `FormatChecker: noopFormatChecker`, meaning any `uint64` value including `0` passes validation: [1](#0-0) 

By contrast, the sibling parameter `Kip71BaseFeeDenominator`, which is also used as a divisor in the same function, explicitly rejects zero: [2](#0-1) 

The header-governance consistency checker (`checkConsistency`) treats `gov.Kip71GasTarget` as one of the parameters requiring "no more checks here" beyond the format checker: [3](#0-2) 

`GasTarget` is then used unconditionally as a divisor in `NextMagmaBlockBaseFee`, in both the gas-used-above-target and gas-used-below-target branches: [4](#0-3) 

Note that the code explicitly guards `BaseFeeDenominator == 0` with a fallback value (line 71-76 in the same file), but there is no equivalent guard for `gasTarget == 0`. If `parentGasUsed != gasTarget` (which is virtually guaranteed when `gasTarget == 0`, since `parentGasUsed` is a real usage value), the code executes `x.Div(x, new(big.Int).SetUint64(gasTarget))`, which panics on division by zero in Go's `math/big` package.

### Impact Explanation
`NextMagmaBlockBaseFee` is a consensus-critical function invoked by every node (via `VerifyMagmaHeader`) to compute/verify the base fee of every block once the Magma hardfork is active. A single governance vote setting `kip71.gastarget = 0` — once it takes effect at the next epoch/voting boundary — will cause every full node, validator, and RPC endpoint to panic on the very first block requiring a base-fee calculation, resulting in a **complete network halt** (chain-wide Denial of Service). This is a stronger impact than the referenced original finding (localized revert of a single auction function), since it affects block production and validation for the entire network, not a single transaction path.

### Likelihood Explanation
The vote is placed through the standard governance voting mechanism (an ordinary transaction/vote reachable by the governing node under `single` mode or by council members), and no additional privilege beyond normal governance participation is required. Because the format checker performs no validation (`noopFormatChecker`), the vote is accepted at proposal time with no error, and the resulting invalid state is only discovered once the base-fee computation actually executes, i.e., after the parameter is already active across the entire network. This mirrors exactly the reported bug class: an owner/governance-settable parameter that is later used unguarded as a divisor.

### Recommendation
Add a `FormatChecker` for `Kip71GasTarget` (and ideally also review `Kip71MaxBlockGasUsedForBaseFee`, `IstanbulEpoch`, and other divisor-like uint64 governance parameters) that rejects `0`, consistent with the existing protection already applied to `Kip71BaseFeeDenominator`:
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
Additionally, consider adding a defensive zero-check inside `NextMagmaBlockBaseFee` itself (mirroring the existing `BaseFeeDenominator == 0` fallback) so that even a value that slips through governance validation cannot crash node processing.

### Proof of Concept
1. Governing node (or council under general mode) submits a governance vote setting `kip71.gastarget = 0`.
2. `PartialParamSet.Add("kip71.gastarget", 0)` succeeds because `noopFormatChecker` accepts any value [5](#0-4) .
3. `checkConsistency` in header governance does not reject the vote either [3](#0-2) .
4. Once the parameter takes effect, at the next block after Magma is active with `parentGasUsed != 0`, every node calls `NextMagmaBlockBaseFee`, which executes `new(big.Int).SetUint64(gasTarget)` = 0 as a divisor at `x.Div(x, new(big.Int).SetUint64(gasTarget))` [6](#0-5) , causing a division-by-zero panic in every node attempting to build or verify the block, halting the chain.

### Citations

**File:** kaiax/gov/param.go (L310-315)
```go
	Kip71BaseFeeDenominator: {
		Canonicalizer: uint64Canonicalizer,
		FormatChecker: func(cv any) bool {
			v, ok := cv.(uint64)
			return ok && v != 0
		},
```

**File:** kaiax/gov/param.go (L324-333)
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
