### Title
Missing bounds check on governance-controlled `kip71.gastarget` parameter allows division-by-zero panic in base fee calculation - ([File: params/kip71_config.go])

### Summary
The `Kip71GasTarget` governance parameter is registered with a no-op format checker that accepts any `uint64` value, including `0`, and the header-vote consistency checker performs no additional validation on it. When `GasTarget` is set to `0` by governance, the KIP-71 base fee update formula in `NextMagmaBlockBaseFee` divides by `gasTarget`, causing a division-by-zero panic in `math/big.Int.Div` on every node that processes the next block, halting consensus/block processing network-wide.

### Finding Description
`Kip71GasTarget` is defined with `Canonicalizer: uint64Canonicalizer` and `FormatChecker: noopFormatChecker`, meaning any numeric value (including `0`) is accepted as valid. [1](#0-0) 

Contrast this with `Kip71BaseFeeDenominator`, which is explicitly protected against zero both at the format-checker level and with a runtime defensive fallback: [2](#0-1) [3](#0-2) 

The header-vote consistency checker (`checkConsistency`) explicitly validates `Kip71LowerBoundBaseFee` and `Kip71UpperBoundBaseFee` against each other, but `Kip71GasTarget` (along with `Kip71BaseFeeDenominator` and `Kip71MaxBlockGasUsedForBaseFee`) falls into the catch-all case that performs no consistency check at all: [4](#0-3) 

Once `GasTarget = 0` is ratified, `NextMagmaBlockBaseFee` uses it unprotected in a division: [5](#0-4) 

Specifically, with `gasTarget = 0`, `parentGasUsed > gasTarget` is true for any block with nonzero gas usage, and (unless the shortcut `parentBaseFee == upperBoundBaseFee` applies) the code executes `x.Div(x, new(big.Int).SetUint64(gasTarget))` with a zero divisor, which panics in Go's `math/big` package.

### Impact Explanation
`NextMagmaBlockBaseFee` is invoked during block header verification/base-fee calculation and in the transaction pool base fee update path, both of which run on every node in the network: [6](#0-5) 

A panic in this consensus-critical path effectively crashes or halts every honest node processing the next block after the malicious/compromised governance parameter takes effect, resulting in a network-wide denial of service and consensus halt — a materially more severe consequence than the referenced fund-deposit blocking issue, since it affects the entire chain's liveness rather than a single application's deposit flow.

### Likelihood Explanation
This requires a governance vote to be cast and ratified (i.e., a validator/governing node acting maliciously or being compromised, directly analogous to the "compromised/malicious admin or governance" actor in the reference report). Given the parameter passes canonicalization and format-checking, and consistency-checking explicitly skips it, a single malicious or compromised governing-node vote for `kip71.gastarget = 0` is sufficient to trigger the panic at the next epoch/block that uses the updated parameter set.

### Recommendation
Add a `FormatChecker` for `Kip71GasTarget` (and audit `Kip71MaxBlockGasUsedForBaseFee`) that rejects `0` and unreasonable values, mirroring the protection already applied to `Kip71BaseFeeDenominator`. Additionally, add a defensive runtime fallback in `NextMagmaBlockBaseFee` (similar to the existing `BaseFeeDenominator == 0` fallback) so that a zero `GasTarget` cannot cause a division-by-zero panic even if an invalid value somehow reaches this function.

### Proof of Concept
1. A governing node (single-mode governance) or majority of validators (via `governance_vote`) votes to set `kip71.gastarget` to `0`.
2. `NewVoteData`/`NewGovData` accept the vote because `Kip71GasTarget`'s `FormatChecker` is `noopFormatChecker`. [1](#0-0) 
3. `checkConsistency` performs no validation for `gov.Kip71GasTarget`, so `VerifyGov` accepts the header/vote. [7](#0-6) 
4. At the next epoch, `GetParamSet` returns `GasTarget = 0` for subsequent blocks.
5. When processing the next block with nonzero gas usage, `NextMagmaBlockBaseFee` computes `parentGasUsed > gasTarget` (true), and executes `x.Div(x, new(big.Int).SetUint64(0))`, panicking every node that computes this value (tx pool base fee update and header base fee verification). [8](#0-7)

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

**File:** blockchain/tx_pool.go (L573-580)
```go
	// It needs to update gas price of tx pool since magma hardfork
	if pool.rules.IsMagma {
		pset := pool.govModule.GetParamSet(newHead.Number.Uint64() + 1)
		pool.gasPrice = pset.ToKip71Config().NextMagmaBlockBaseFee(newHead.Number, newHead.BaseFee, newHead.GasUsed)
		if pool.rules.IsOsaka {
			pool.blobBaseFee = params.CalcBlobFee(pool.gasPrice)
		}
	}
```
