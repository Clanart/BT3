I found a concrete, root-cause-provable analog: `Kip71GasTarget` uses `noopFormatChecker`, allowing a governance vote to set `GasTarget = 0`, which causes a division-by-zero panic in `NextMagmaBlockBaseFee` whenever a block's gas usage exceeds the (zero) target — crashing every node computing/verifying the base fee for that block.

### Title
Unbounded `kip71.gastarget` governance parameter allows setting `GasTarget = 0`, causing a division-by-zero panic/consensus halt in base fee calculation - ([File: params/kip71_config.go])

### Summary
The Kip71GasTarget governance parameter format checker is a no-op, allowing any `uint64` value — including `0` — to be accepted as a valid vote/value, unlike `Kip71BaseFeeDenominator` which explicitly rejects `0`.

### Finding Description
`Kip71GasTarget`'s `Param` definition in `kaiax/gov/param.go` uses `FormatChecker: noopFormatChecker`, which always returns `true` regardless of the value: [1](#0-0) 

Compare this to `Kip71BaseFeeDenominator`, which explicitly guards against zero (`ok && v != 0`): [2](#0-1) 

This means a governance vote setting `kip71.gastarget` to `0` passes `NewVoteData`/format validation — confirmed by the absence of any "bad vote" test case for `Kip71GasTarget: value 0` in `vote_test.go`, whereas format-type-mismatch values are rejected: [3](#0-2) 

The `checkConsistency` function in `headergov`, which validates governance votes against other chain state, has no case handling `Kip71GasTarget` at all — it falls into the default no-extra-check bucket alongside other benign parameters: [4](#0-3) 

Once `GasTarget = 0` becomes the active governance parameter, `KIP71Config.NextMagmaBlockBaseFee` — invoked both to build the next block's base fee (block proposer) and to verify it (`VerifyMagmaHeader`, called by all validating nodes) — performs an unconditional `big.Int` division by `gasTarget`: [5](#0-4) 

Since `parentGasUsed := min(parentHeaderGasUsed, upperGasLimit)` will be `> 0` for essentially any non-empty block, and `gasTarget == 0`, the `parentGasUsed > gasTarget` branch is taken, and `x.Div(x, new(big.Int).SetUint64(gasTarget))` performs division by zero. Go's `math/big.Int.Div` panics on division by zero (there is no zero-guard here, unlike the explicit zero-guard for `BaseFeeDenominator` a few lines above at lines 70-76).

### Impact Explanation
Because `NextMagmaBlockBaseFee`/`VerifyMagmaHeader` are called by every node (proposer and validators) while processing/verifying every Magma-forked block's base fee, a single governance vote successfully setting `kip71.gastarget` to `0` deterministically panics all nodes on the very next block that uses any gas — this is a network-wide liveness/consensus-halting DoS rather than a localized issue, since every honest node executes the identical code path and panics identically.

### Likelihood Explanation
The vote itself only requires standard governance vote submission (reachable through the header governance voting mechanism as any other `kip71.*` parameter, e.g. `Kip71LowerBoundBaseFee`/`Kip71UpperBoundBaseFee`), and unlike those two parameters, `Kip71GasTarget` has zero cross-field consistency checks in `checkConsistency`. A single malformed or malicious vote of `0` for this specific well-known parameter is sufficient — no unusual conditions (like extreme gas usage) are needed since `parentGasUsed > 0` is the overwhelmingly common case.

### Recommendation
Add a `FormatChecker` for `Kip71GasTarget` rejecting `0` (mirroring the existing `Kip71BaseFeeDenominator` check `ok && v != 0`), and/or add a defensive zero-check inside `NextMagmaBlockBaseFee` (similar to the existing `BaseFeeDenominator == 0` fallback) before performing division by `gasTarget`.

### Proof of Concept
1. Submit/pass a governance vote setting `kip71.gastarget` to `0` (accepted because `noopFormatChecker` always returns `true`, and `checkConsistency` has no special-case for this parameter).
2. Once active, the next Magma-forked block with `gasUsed > 0` triggers `NextMagmaBlockBaseFee`'s `parentGasUsed > gasTarget` branch.
3. `x.Div(x, new(big.Int).SetUint64(0))` panics inside every node computing or verifying the block's base fee, since `math/big.Int.Div` panics on division by zero — halting block production/validation network-wide.

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

**File:** kaiax/gov/headergov/vote_test.go (L137-140)
```go
		{name: gov.Kip71GasTarget, value: "30000"},
		{name: gov.Kip71GasTarget, value: 3000},
		{name: gov.Kip71GasTarget, value: false},
		{name: gov.Kip71GasTarget, value: true},
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

**File:** params/kip71_config.go (L88-103)
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
```
