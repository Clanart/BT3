## Title
Governance-set `Kip71GasTarget = 0` causes chain-halting division-by-zero panic in `NextMagmaBlockBaseFee` due to missing range validation - (File: `kaiax/gov/param.go`, `params/kip71_config.go`)

### Summary
The Sherlock report describes `Config._validateRange` claiming to "validate that the value is within the allowed range" but never actually checking the value against any bound. The analogous pattern exists in Kaia's governance parameter validation table: several `Param.FormatChecker` entries in `kaiax/gov/param.go` are set to `noopFormatChecker`, which always returns `true` regardless of the value, silently skipping range validation that downstream consensus code assumes has been performed. `Kip71GasTarget` is one such parameter, and its unchecked value of `0` triggers a division-by-zero panic in the KIP-71 base-fee computation used on the consensus path.

### Finding Description
`kaiax/gov/param.go` defines a `Params` map where each entry has a `FormatChecker` meant to validate the canonical value before it is accepted as a vote. Most numeric parameters have a real checker (e.g. `Kip71BaseFeeDenominator` requires `v != 0`), but `Kip71GasTarget` uses `noopFormatChecker`, which unconditionally returns `true`: [1](#0-0) [2](#0-1) 

This checker is invoked from `NewVoteData`, the single gate through which any governance vote (header vote or contract-based vote) must pass before being accepted as valid: [3](#0-2) 

`checkConsistency` in the header governance module also does not perform any additional bound-check for `Kip71GasTarget`; it falls into the default "no more checks here" bucket alongside other Kip71 parameters: [4](#0-3) 

Once accepted, `GasTarget` flows unchecked into `KIP71Config.GasTarget`, which is used directly as a divisor in `NextMagmaBlockBaseFee`: [5](#0-4) 

If `gasTarget == 0` and the parent block used any gas (`parentGasUsed > 0`), the code takes the `parentGasUsed > gasTarget` branch and executes `x.Div(x, new(big.Int).SetUint64(gasTarget))` with a zero divisor. Go's `math/big.Int.Div` panics on division by zero. `NextMagmaBlockBaseFee` (and its caller `VerifyMagmaHeader`) sit on the consensus-critical block-processing/verification path (invoked from block assembly in `work/worker.go`, from `blockchain/chain_makers.go`, and from header verification), so this panic would be triggered on every node validating the next block after the malicious `Kip71GasTarget=0` governance parameter takes effect.

### Impact Explanation
A single accepted governance vote setting `kip71.gastarget` to `0` — which passes both `NewVoteData`'s format check and `checkConsistency` because neither performs an actual range check — will cause every honest node to panic/crash while computing or verifying the base fee of the very next non-empty block. This is a network-wide chain halt (denial of service against the entire chain), not merely a local issue, satisfying the "acceptance of an invalid transaction or block" / "state divergence" impact bar (in this case, a crash rather than divergence, but the effect is complete unavailability of the chain via a permissible governance parameter).

### Likelihood Explanation
Reaching this requires a governance vote to reach quorum and be enacted, which requires validator/governing-node participation rather than an arbitrary unprivileged sender. However, "governance parameters" is explicitly listed as an in-scope reachable category for this analysis, and the underlying root cause — `_validateRange`-style validators that don't actually check bounds — is a direct structural analog of the referenced bug. Any validator (single governing node in "single" mode, or a majority in "none"/council mode) can submit this parameter change through the normal governance voting mechanism without any other code change.

### Recommendation
Add a real `FormatChecker` for `Kip71GasTarget` (and audit other `noopFormatChecker` usages such as `Kip71LowerBoundBaseFee`, `Kip71UpperBoundBaseFee`, `Kip71MaxBlockGasUsedForBaseFee`, `GovernanceUnitPrice`) that rejects `0` or otherwise unsafe values, e.g. `func(cv any) bool { v, ok := cv.(uint64); return ok && v != 0 }`, matching the pattern already used for `Kip71BaseFeeDenominator`. Additionally, add a defensive zero-check inside `NextMagmaBlockBaseFee` before dividing by `gasTarget`, mirroring the existing defensive fallback already present for `BaseFeeDenominator == 0`.

### Proof of Concept
1. A governance vote (or governing node in single mode) submits `gov.Kip71GasTarget = 0`.
2. `NewVoteData` canonicalizes it via `uint64Canonicalizer` (succeeds) and calls `Kip71GasTarget.FormatChecker` which is `noopFormatChecker` → returns `true`, so the vote is accepted. [2](#0-1) 
3. `checkConsistency` does not add any check for `Kip71GasTarget`, so `VerifyGov`/`VerifyVote` accept it. [4](#0-3) 
4. Once the vote takes effect at the next epoch, `c.Governance.KIP71.GasTarget` becomes `0` in the active `ChainConfig`.
5. On the next block where `parentHeaderGasUsed > 0`, `NextMagmaBlockBaseFee` executes `parentGasUsed > gasTarget` (`gasTarget=0`) branch and calls `x.Div(x, new(big.Int).SetUint64(0))`, panicking with "division by zero" in every node processing/verifying that block. [6](#0-5)

### Citations

**File:** kaiax/gov/param.go (L160-162)
```go
func noopFormatChecker(cv any) bool {
	return true
}
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

**File:** kaiax/gov/headergov/vote.go (L29-55)
```go
func NewVoteData(voter common.Address, name string, value any) VoteData {
	param, ok := gov.Params[gov.ParamName(name)]
	if !ok {
		param, ok = gov.ValidatorParams[gov.ParamName(name)]
		if !ok {
			logger.Error("Invalid vote name", "name", name)
			return nil
		}
	}

	cv, err := param.Canonicalizer(value)
	if err != nil {
		logger.Error("Canonicalize error", "name", name, "value", value, "err", err)
		return nil
	}

	if !param.FormatChecker(cv) {
		logger.Error("Format check error", "name", name, "value", value)
		return nil
	}

	return &voteData{
		voter: voter,
		name:  gov.ParamName(name),
		value: cv,
	}
}
```

**File:** kaiax/gov/headergov/impl/header.go (L213-223)
```go
		// These votes are valid as long as it passes the format checks in NewVoteData(). No more checks here.
	case gov.GovernanceDeriveShaImpl, gov.GovernanceGovParamContract, gov.GovernanceGovernanceMode, gov.GovernanceUnitPrice,
		gov.IstanbulCommitteeSize, gov.IstanbulEpoch, gov.IstanbulPolicy,
		gov.Kip71BaseFeeDenominator, gov.Kip71GasTarget, gov.Kip71MaxBlockGasUsedForBaseFee,
		gov.RewardDeferredTxFee, gov.RewardKip82Ratio, gov.RewardMintingAmount, gov.RewardMinimumStake,
		gov.RewardProposerUpdateInterval, gov.RewardRatio, gov.RewardStakingRewardThreshold,
		gov.RewardStakingUpdateInterval, gov.RewardUseFlexReward, gov.RewardUseGiniCoeff:
		return nil
	default:
		return ErrInvalidKeyValue
	}
```

**File:** params/kip71_config.go (L77-103)
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
```
