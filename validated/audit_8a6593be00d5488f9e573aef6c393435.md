### Title
Division by zero panic in `KIP71Config.NextMagmaBlockBaseFee` via unchecked `kip71.gastarget` governance vote — ([File: params/kip71_config.go])

### Summary
`Kip71GasTarget` is a governable parameter that any validator/council member can vote on via the standard KIP-81 header-governance mechanism, but unlike its sibling parameter `Kip71BaseFeeDenominator`, it has **no non-zero format check**. If `GasTarget` is set to `0`, the KIP-71 base-fee computation `NextMagmaBlockBaseFee` divides by `gasTarget` and panics, which is invoked on every block during Magma base-fee verification.

### Finding Description
`Kip71BaseFeeDenominator`'s parameter definition explicitly rejects zero: [1](#0-0) 

But `Kip71GasTarget` uses `noopFormatChecker`, which accepts any `uint64` value including `0`: [2](#0-1) 

The header-governance consistency checker (`checkConsistency`) explicitly allows `gov.Kip71GasTarget` votes to pass through with no additional validation: [3](#0-2) 

Once a `GasTarget = 0` vote is enacted, `KIP71Config.NextMagmaBlockBaseFee` uses it as a divisor. `NextMagmaBlockBaseFee` defends against `BaseFeeDenominator == 0` with an explicit fallback (line 71-76), but performs no equivalent check for `GasTarget`: [4](#0-3) 

Specifically, when `parentGasUsed != gasTarget` (which is virtually guaranteed once `gasTarget == 0` and any gas was used in the parent block), the code executes:
```go
gasUsedDelta := new(big.Int).SetUint64(parentGasUsed - gasTarget)
x := new(big.Int).Mul(parentBaseFee, gasUsedDelta)
y := x.Div(x, new(big.Int).SetUint64(gasTarget))   // division by zero -> panic
```
`big.Int.Div` panics at runtime with "division by zero" when the divisor is zero — this is the exact same bug class as the reported `QuantizedBiasAdd` issue (dividing by an attacker/operator-controllable value without a zero check).

`NextMagmaBlockBaseFee` is called from `VerifyMagmaHeader`, which is invoked during block header verification for every incoming block: [5](#0-4) [6](#0-5) 

It is also called from RPC-facing fee estimation code (`node/cn/gasprice/feehistory.go`), meaning the panic is reachable both through consensus header verification (executed by every full node processing the block) and through public RPC calls that compute the next base fee. [7](#0-6) 

### Impact Explanation
Once `kip71.gastarget` is voted to `0` and the vote is enacted (Magma already active), **every node** that verifies the next block (via `VerifyMagmaHeader`) or serves `eth_feeHistory`/`eth_gasPrice` RPC calls will panic in `big.Int.Div`. Because header verification of the base fee is part of core consensus block processing, this causes a chain-wide halt/crash across all full nodes and validators — a network-wide denial of service, not merely a local resource issue. This matches "acceptance of an invalid transaction or block" / state-transition breakage criteria in scope (governance parameters are explicitly listed as a reachable path).

### Likelihood Explanation
The only barrier is that `kip71.gastarget` votes are governance votes (submitted by council/validator members through the standard KIP-81 process), but the format checker for this specific parameter performs **no validation at all** (`noopFormatChecker`), unlike the sibling `BaseFeeDenominator` parameter which explicitly guards against zero. This asymmetry strongly suggests the zero-check omission was an oversight rather than an intentional design decision, making it a real, concretely reachable path once a `GasTarget=0` vote is cast and enacted at an epoch boundary.

### Recommendation
Add a `FormatChecker` for `Kip71GasTarget` in `kaiax/gov/param.go` that rejects `v == 0`, mirroring the existing check for `Kip71BaseFeeDenominator`:
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
Additionally, as defense in depth, `NextMagmaBlockBaseFee` in `params/kip71_config.go` should apply the same zero-fallback pattern used for `BaseFeeDenominator` to `GasTarget` before it is used as a divisor.

### Proof of Concept
1. A council member submits a governance vote setting `kip71.gastarget` to `0` via the standard header-governance vote mechanism (`headerGovAPI.Vote("kip71.gastarget", uint64(0))`); this passes `checkConsistency` and `noopFormatChecker` with no rejection.
2. The vote is enacted at the next epoch block, updating the effective `ParamSet.GasTarget` to `0`.
3. At the following block (post-Magma), any node computing `NextMagmaBlockBaseFee(parentHeaderNumber, parentHeaderBaseFee, parentHeaderGasUsed)` with `parentHeaderGasUsed > 0` (essentially any block with transactions) hits the branch `parentGasUsed > gasTarget` (0), and executes `x.Div(x, new(big.Int).SetUint64(0))`, causing an unrecoverable Go runtime panic ("division by zero") in every node validating the header — network-wide crash.

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

**File:** params/kip71_config.go (L58-102)
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
```

**File:** blockchain/block_validator.go (L1-1)
```go
// Modifications Copyright 2024 The Kaia Authors
```

**File:** node/cn/gasprice/feehistory.go (L111-115)
```go
	if isNextBlockMagma {
		bf.results.nextBaseFee = kip71Config.NextMagmaBlockBaseFee(bf.header.Number, bf.header.BaseFee, bf.header.GasUsed)
	} else {
		bf.results.nextBaseFee = new(big.Int).SetUint64(params.ZeroBaseFee)
	}
```
