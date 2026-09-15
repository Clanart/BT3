Confirmed: `checkConsistency` in `kaiax/gov/headergov/impl/header.go` explicitly passes through `gov.Kip71GasTarget` (line 216) with no additional consistency/zero check — it only special-cases `Kip71LowerBoundBaseFee` and `Kip71UpperBoundBaseFee`. Combined with `Kip71GasTarget`'s `FormatChecker: noopFormatChecker` in `kaiax/gov/param.go`, a `GasTarget = 0` vote passes every validation layer (canonicalization, format check, header consistency check) and gets ratified into `header.Governance`, becoming the effective `KIP71.GasTarget` for all subsequent blocks.

### Title
Divide-by-zero panic in KIP-71 base fee calculation via zero `GasTarget` governance parameter - (File: params/kip71_config.go)

### Summary
`KIP71Config.NextMagmaBlockBaseFee` divides by `gasTarget` without checking for zero, and the governance parameter `kip71.gastarget` has no non-zero validation, unlike its sibling `kip71.basefeedenominator`.

### Finding Description
`NextMagmaBlockBaseFee` in `params/kip71_config.go` explicitly guards against `BaseFeeDenominator == 0` (falls back to 64) but performs no equivalent guard for `GasTarget`: [1](#0-0) 
When `parentGasUsed != gasTarget`, the code computes `y := x.Div(x, new(big.Int).SetUint64(gasTarget))` (increase branch) or an equivalent division in the decrease branch: [2](#0-1) 
`big.Int.Div` panics on a zero divisor, so `gasTarget == 0` triggers an unrecoverable panic in `NextMagmaBlockBaseFee`, which is invoked both by block proposers (block assembly) and by every node validating the block header via `VerifyMagmaHeader`.

The governance parameter that sets `GasTarget` (`gov.Kip71GasTarget`) uses `noopFormatChecker`, i.e., no format validation at all, in contrast to `Kip71BaseFeeDenominator` which explicitly rejects zero: [3](#0-2) 
Header-governance's `checkConsistency` function, which is the only additional semantic check applied to votes beyond `FormatChecker`, special-cases only `Kip71LowerBoundBaseFee` and `Kip71UpperBoundBaseFee` for bound-consistency, but passes `Kip71GasTarget` through unconditionally: [4](#0-3) 
Thus a `GasTarget = 0` vote survives canonicalization, format-checking, and consistency-checking, gets ratified in `header.Governance`, and becomes the active KIP-71 parameter from the next epoch onward.

### Impact Explanation
Once ratified, every node (validators verifying headers and any node computing the pending/next base fee) calls `NextMagmaBlockBaseFee` with `GasTarget = 0`. Because `parentGasUsed` (any nonzero block gas usage) will not equal `gasTarget` (0), the function enters the increase/decrease branch and performs `Div(x, gasTarget)`, causing a panic. This crashes block processing network-wide — a full chain halt / consensus-liveness failure — which is a severe, concrete impact (matches the CVE's divide-by-zero-from-attacker-controlled-zero-value pattern), not merely a resource-exhaustion issue.

### Likelihood Explanation
This requires a governance vote to set `kip71.gastarget=0`, either via header governance (the governing node in `single` mode, or any council member in `none` mode) or via contract governance (`GovParam.setParam`/`setParamIn`, KIP-81), both of which are legitimate, reachable governance-parameter update paths explicitly in scope. No additional privilege escalation, cryptographic break, or malicious-node behavior is needed beyond a single vote/transaction with an unvalidated value — the bug is that the value is never rejected.

### Recommendation
Add an explicit non-zero `FormatChecker` for `gov.Kip71GasTarget` (mirroring `Kip71BaseFeeDenominator`'s `v != 0` check) in `kaiax/gov/param.go`, and/or add a zero-fallback guard in `NextMagmaBlockBaseFee` in `params/kip71_config.go` analogous to the existing `BaseFeeDenominator == 0` fallback, so `gasTarget == 0` cannot reach the `Div` call.

### Proof of Concept
1. As governing node (single mode) or council member (none mode), submit `governance_vote("kip71.gastarget", 0)`, or as GovParam contract owner call `setParam("kip71.gastarget", true, encode(0), activation)`.
2. Once ratified/activated, any block with `parentGasUsed > 0` triggers `NextMagmaBlockBaseFee(parentHeaderNumber, parentBaseFee, parentGasUsed)` → `gasUsedDelta = parentGasUsed - 0`, `y := x.Div(x, big.NewInt(0))` → panic, crashing block assembly and header verification network-wide. [5](#0-4)

### Citations

**File:** params/kip71_config.go (L70-77)
```go
	var baseFeeDenominator *big.Int
	if kc.BaseFeeDenominator == 0 {
		// To avoid panic, set the fluctuation range small
		baseFeeDenominator = new(big.Int).SetUint64(64)
	} else {
		baseFeeDenominator = new(big.Int).SetUint64(kc.BaseFeeDenominator)
	}
	gasTarget := kc.GasTarget
```

**File:** params/kip71_config.go (L98-121)
```go
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

**File:** kaiax/gov/param.go (L310-333)
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

**File:** kaiax/gov/headergov/impl/header.go (L188-220)
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
	case gov.AddValidator, gov.RemoveValidator:
		params := h.GetParamSet(blockNum)

		// compare with governing node only in single mode.
		if params.GovernanceMode != "single" {
			return nil
		}
		if slices.Contains(vote.Value().([]common.Address), params.GoverningNode) {
			return ErrGovNodeInValSetVoteValue
		}
		return nil
		// These votes are valid as long as it passes the format checks in NewVoteData(). No more checks here.
	case gov.GovernanceDeriveShaImpl, gov.GovernanceGovParamContract, gov.GovernanceGovernanceMode, gov.GovernanceUnitPrice,
		gov.IstanbulCommitteeSize, gov.IstanbulEpoch, gov.IstanbulPolicy,
		gov.Kip71BaseFeeDenominator, gov.Kip71GasTarget, gov.Kip71MaxBlockGasUsedForBaseFee,
		gov.RewardDeferredTxFee, gov.RewardKip82Ratio, gov.RewardMintingAmount, gov.RewardMinimumStake,
		gov.RewardProposerUpdateInterval, gov.RewardRatio, gov.RewardStakingRewardThreshold,
		gov.RewardStakingUpdateInterval, gov.RewardUseFlexReward, gov.RewardUseGiniCoeff:
		return nil
```
