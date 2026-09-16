### Title
Missing lower-bound (non-zero) validation on `kip71.gastarget` governance parameter causes division-by-zero panic in base fee computation - ([File: kaiax/gov/param.go], [File: params/kip71_config.go])

### Summary
The KIP-71 dynamic base-fee parameter `kip71.gastarget` can be voted to the value `0` because its `FormatChecker` is `noopFormatChecker` (always returns `true`), unlike the sibling parameter `kip71.basefeedenominator`, which explicitly rejects `0`. Once a `GasTarget` of `0` is adopted into the governance parameter set, every subsequent call to `KIP71Config.NextMagmaBlockBaseFee` divides by `gasTarget`, causing an unrecovered `big.Int` division-by-zero panic that is executed by every node validating or building a block.

### Finding Description
`gov.Params` defines the `FormatChecker` used by `headergov.NewVoteData` to validate governance votes before they are accepted into `header.Vote`: [1](#0-0) 

Note that `Kip71BaseFeeDenominator` explicitly enforces `v != 0`, while `Kip71GasTarget` uses `noopFormatChecker`, which accepts any `uint64` value including `0`: [2](#0-1) 

`checkConsistency` in the header governance module only cross-validates `Kip71LowerBoundBaseFee` against `Kip71UpperBoundBaseFee`; `Kip71GasTarget` falls into the "no more checks" branch that always returns `nil`: [3](#0-2) 

Once accepted, `GasTarget` flows into `ParamSet.ToKip71Config()` and ultimately `KIP71Config.NextMagmaBlockBaseFee`, which is invoked on every block to compute/verify the base fee (via `VerifyMagmaHeader`, consumed by `blockchain/block_validator.go`, `work/worker.go`, `blockchain/tx_pool.go`, `node/cn/gasprice/gasprice.go`, etc.): [4](#0-3) 

When `gasTarget == 0` and `parentGasUsed > 0` (which is the normal case for a non-empty block), execution reaches the "gas used above target" branch and performs `x.Div(x, new(big.Int).SetUint64(gasTarget))`, i.e. division by `big.Int(0)`, which panics in Go's `math/big` package.

### Impact Explanation
`NextMagmaBlockBaseFee`/`VerifyMagmaHeader` is called by all nodes during block assembly and block verification. A panic here is a state-transition/consensus-critical function crash, causing the entire network (or every node applying the offending block/header) to crash simultaneously — a full chain halt/DoS, matching the "no upper/lower limit for setting parameters" bug class from the report (there admins could set an unbounded fee causing fund loss; here the unbounded governance parameter causes network-wide denial of service instead of silent fee theft, which is a more severe consequence).

### Likelihood Explanation
The parameter is set exclusively through the governance vote mechanism, which (in single-governance mode) requires being the governing node, and in multi-node mode requires validator council membership — i.e., the vote path is only reachable by a governance-privileged actor, not by an arbitrary unprivileged sender. However, per the accepted analog categories ("governance parameters" is explicitly listed as reachable via a submitted transaction/vote), this is a legitimate governance-parameter validation gap: nothing in the vote-acceptance pipeline (`NewVoteData` format check, `checkConsistency`) rejects `gastarget = 0`, so a single malicious or erroneous vote by an authorized governance participant (or a compromised governing-node key) is sufficient to trigger the panic on the very next block that uses gas.

### Recommendation
Add a `FormatChecker` for `Kip71GasTarget` (and ideally `Kip71MaxBlockGasUsedForBaseFee`) requiring `v > 0`, consistent with the existing `Kip71BaseFeeDenominator` check, e.g.:
```go
Kip71GasTarget: {
    Canonicalizer: uint64Canonicalizer,
    FormatChecker: func(cv any) bool {
        v, ok := cv.(uint64)
        return ok && v != 0
    },
    ...
},
```
Additionally, `KIP71Config.NextMagmaBlockBaseFee` should defensively guard against `gasTarget == 0` (similar to the existing `BaseFeeDenominator == 0` fallback) to avoid a panic even if an invalid value slips through via chain config or upgrade paths.

### Proof of Concept
1. A governance-privileged voter (governing node in single mode, or a council validator) submits a vote `kip71.gastarget = uint64(0)`.
2. `headergov.NewVoteData` accepts it because `Kip71GasTarget.FormatChecker` is `noopFormatChecker` [5](#0-4) .
3. `checkConsistency` does not reject it — `Kip71GasTarget` is in the pass-through case list [6](#0-5) .
4. The vote is finalized into governance param history and applied starting from the next epoch, so `ParamSet.GasTarget == 0` is used to build `KIP71Config` via `ToKip71Config()`.
5. On the next block where `parentHeaderGasUsed > 0`, all nodes calling `NextMagmaBlockBaseFee`/`VerifyMagmaHeader` execute `x.Div(x, new(big.Int).SetUint64(0))` and panic [7](#0-6) , halting block production/verification network-wide.

### Citations

**File:** kaiax/gov/param.go (L160-162)
```go
func noopFormatChecker(cv any) bool {
	return true
}
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

**File:** params/kip71_config.go (L58-103)
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
```
