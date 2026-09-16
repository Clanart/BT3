### Title
Missing non-zero validation for `Kip71GasTarget` governance parameter causes division-by-zero panic in base fee calculation - (File: `params/kip71_config.go`)

### Summary
The `kaiax/gov` module validates governance parameter values before accepting them via header votes or contract governance, using per-parameter `FormatChecker` functions. While `Kip71BaseFeeDenominator` explicitly enforces `v != 0`, the `Kip71GasTarget` parameter uses `noopFormatChecker`, meaning it accepts a value of `0` without any validation, and that value is later used unguarded as a divisor in the consensus-critical base fee computation.

### Finding Description
Each governance parameter definition in `Params` specifies a `FormatChecker`. For `Kip71BaseFeeDenominator`, the checker explicitly rejects zero: `FormatChecker: func(cv any) bool { v, ok := cv.(uint64); return ok && v != 0 }` [1](#0-0) . In contrast, `Kip71GasTarget` uses `noopFormatChecker`, imposing no constraint on its value, including zero [2](#0-1) . A governance vote setting `Kip71GasTarget` to `0` passes both `NewVoteData` format checks and `checkConsistency`, since `gov.Kip71GasTarget` falls into the generic "no more checks here" case in `checkConsistency` [3](#0-2) .

This unvalidated `GasTarget` value flows into `NextMagmaBlockBaseFee`, where it is used both as an equality check and, critically, as a divisor:
```
gasTarget := kc.GasTarget
...
} else if parentGasUsed > gasTarget {
    gasUsedDelta := new(big.Int).SetUint64(parentGasUsed - gasTarget)
    x := new(big.Int).Mul(parentBaseFee, gasUsedDelta)
    y := x.Div(x, new(big.Int).SetUint64(gasTarget))   // divide-by-zero if gasTarget == 0
``` [4](#0-3) 

Unlike `BaseFeeDenominator`, which has a defensive zero-fallback (`if kc.BaseFeeDenominator == 0 { baseFeeDenominator = 64 }`) [5](#0-4) , `GasTarget` has no such fallback. If `GasTarget` is `0` and any block uses gas (`parentGasUsed > 0`), `big.Int.Div` will be called with a zero divisor, which panics in Go's `math/big` package.

`NextMagmaBlockBaseFee` is invoked from `VerifyMagmaHeader`, which is called during block header validation on every node processing that header [6](#0-5) , and this is wired into `blockchain/block_validator.go`'s header verification path (confirmed via reference in that file). This mirrors the reported "TGE" bug class exactly: a governance-controlled value (`GasTarget`, analogous to "TGE") is used in downstream arithmetic without a dedicated non-zero validation, whereas a sibling parameter (`BaseFeeDenominator`) received the fix that `GasTarget` did not.

### Impact Explanation
If a `Kip71GasTarget = 0` vote is accepted (via the governing node's header vote or contract governance, both of which pass through the same unguarded `FormatChecker`/`checkConsistency` path), every node computing the next block's base fee for any block with nonzero gas usage will panic during `VerifyMagmaHeader`/`NextMagmaBlockBaseFee`. Because this function executes deterministically on all validating nodes for every subsequent header, it results in a chain-wide halt: every node crashes or rejects all valid blocks going forward, rather than diverging silently. This is a critical liveness/consensus-halting issue reachable purely through the same governance-vote transaction mechanism already recognized as an in-scope kaia surface.

### Likelihood Explanation
The likelihood depends on a `Kip71GasTarget=0` vote being accepted, which requires it to pass governance vote submission and consistency checks — both of which currently impose no restriction on this value, unlike the sibling `BaseFeeDenominator` parameter that was hardened against exactly this class of input. Since no additional safeguard exists at the `NextMagmaBlockBaseFee` call site (no zero-fallback as with `BaseFeeDenominator`), any successful vote setting the value to zero deterministically triggers the panic on the very next non-idle block.

### Recommendation
Add a `FormatChecker` for `Kip71GasTarget` that rejects zero, consistent with the pattern already used for `Kip71BaseFeeDenominator`:
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
Additionally, consider adding a defensive zero-fallback in `NextMagmaBlockBaseFee` similar to the existing `BaseFeeDenominator` guard, to protect against any legacy/misconfigured chain configs that may already contain a zero `GasTarget`.

### Proof of Concept
1. Submit/accept a governance vote (header vote or contract governance) setting `kip71.gastarget` to `0`. This passes `NewVoteData` (canonicalizer accepts any `uint64`) and `checkConsistency` (falls into the no-op case for `gov.Kip71GasTarget`) [3](#0-2) .
2. Once the vote takes effect, the `ParamSet.GasTarget` field becomes `0` and is propagated into `KIP71Config` via `ToKip71Config()` [7](#0-6) .
3. On the next block where `parentHeaderGasUsed > 0` (essentially any block with transactions), `NextMagmaBlockBaseFee` executes `x.Div(x, new(big.Int).SetUint64(gasTarget))` with `gasTarget == 0` [8](#0-7) , causing a `division by zero` panic in `math/big`.
4. Since this function is invoked via `VerifyMagmaHeader` during header validation on every node, all nodes attempting to validate the next block panic/crash, halting the chain.

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
