### Title
Governance can ratify `kip71.gastarget = 0`, causing a division-by-zero panic in `NextMagmaBlockBaseFee` and halting the chain - ([File: params/kip71_config.go])

### Summary
The Telcoin report flags that `challengePeriod` can be set to an unreasonable value with no bounds check, breaking protocol invariants. The same pattern exists in Kaia's `kaiax/gov` governance parameter system: `Kip71GasTarget` (`governance.gastarget`/`kip71.gastarget`) is registered with `FormatChecker: noopFormatChecker`, meaning **no bound or non-zero check** is performed before the value is accepted, ratified, and later consumed in base-fee computation, unlike its sibling parameter `Kip71BaseFeeDenominator` which explicitly checks `v != 0`.

### Finding Description
In `kaiax/gov/param.go`, `Kip71GasTarget` is defined as: [1](#0-0) 
with `FormatChecker: noopFormatChecker`, which always returns `true`: [2](#0-1) 

By contrast, `Kip71BaseFeeDenominator` explicitly guards against a zero value: [3](#0-2) 

The header-governance vote-consistency checker (`checkConsistency`) also does not perform any range/zero check for `Kip71GasTarget`—it is only used for cross-checking `Kip71LowerBoundBaseFee`/`Kip71UpperBoundBaseFee` against each other: [4](#0-3) 

`GasTarget` (via `ParamSet.ToKip71Config()`) flows directly into `KIP71Config.NextMagmaBlockBaseFee`, which computes the base fee for the next block: [5](#0-4) 

If `kc.GasTarget == 0` and `parentGasUsed > 0` (the overwhelmingly common case), execution takes the `parentGasUsed > gasTarget` branch and performs:
```go
y := x.Div(x, new(big.Int).SetUint64(gasTarget))  // gasTarget = 0
```
`big.Int.Div` by zero panics. Note that `BaseFeeDenominator` has an explicit `if kc.BaseFeeDenominator == 0 { ... }` fallback guard, but no equivalent guard exists for `gasTarget`: [6](#0-5) 

### Impact Explanation
`NextMagmaBlockBaseFee`/`VerifyMagmaHeader` are consensus-critical: they are invoked during block assembly (`work/worker.go`) and during block header validation (`blockchain/block_validator.go`). Once `governance.gastarget = 0` is voted by the (single-mode) governing node and ratified at an epoch boundary, **every honest node** that builds or validates a subsequent block with nonzero gas usage will panic in this deterministic, consensus-path code. This is not a localized bug—it triggers simultaneously across the network, halting block production/validation network-wide and effectively locking all funds and pending transactions indefinitely, since the chain cannot progress past the point where the malformed parameter takes effect. This directly matches the report's "lock funds indefinitely" impact class, applied to Kaia's KIP-71 dynamic base-fee governance parameter instead of Telcoin's `challengePeriod`.

### Likelihood Explanation
The governing node is the sole voter for parameters in `single` governance mode. A single malicious or erroneous vote for `governance.gastarget = 0` (or a similarly named path such as `kip71.gastarget`), which passes `noopFormatChecker` unconditionally, is sufficient. This does not require exploiting multiple contracts or complex preconditions—only that the vote is ratified normally through the existing (unprivileged-reachable) governance-vote/ratification pipeline covered by the allowed "governance parameters" category, and that a subsequent block has any nonzero gas usage (essentially guaranteed).

### Recommendation
Add a `FormatChecker` for `Kip71GasTarget` (and any other unrestricted numeric governance parameter feeding into base-fee/division logic) that rejects `0`, mirroring the existing check on `Kip71BaseFeeDenominator`:
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
Additionally, add a defensive zero-check/fallback inside `NextMagmaBlockBaseFee` for `gasTarget`, similar to the existing fallback for `baseFeeDenominator`, so that even if a bad value is somehow ratified (e.g., via legacy header-governance state or a bug elsewhere), the network does not panic.

### Proof of Concept
1. Governing node (in `single` governance mode) casts a header-governance vote: `("kip71.gastarget", 0)`.
2. `NewVoteData`/`checkConsistency` accept the vote since `Kip71GasTarget`'s `FormatChecker` is `noopFormatChecker` and `checkConsistency` has no special-case for it: [7](#0-6) 
3. The vote is ratified at the next epoch boundary, and `GetParamSet` for subsequent blocks returns `GasTarget = 0`.
4. On the next block with `GasUsed > 0`, `work/worker.go` calls `NextMagmaBlockBaseFee` while assembling the block header, and `blockchain/block_validator.go` calls `VerifyMagmaHeader`→`NextMagmaBlockBaseFee` while validating it; both paths execute `x.Div(x, big.NewInt(0))` in `params/kip71_config.go`, causing a panic on every node processing that block.

### Citations

**File:** kaiax/gov/param.go (L160-162)
```go
func noopFormatChecker(cv any) bool {
	return true
}
```

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

**File:** kaiax/gov/headergov/impl/header.go (L188-223)
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
	default:
		return ErrInvalidKeyValue
	}
```

**File:** params/kip71_config.go (L58-129)
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

		nextBaseFee := x.Sub(parentBaseFee, baseFeeDelta)
		if nextBaseFee.Cmp(lowerBoundBaseFee) < 0 {
			return makeEvenByFloor(lowerBoundBaseFee)
		}
		return makeEvenByFloor(nextBaseFee)
	}
}
```
