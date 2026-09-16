### Title
Governance vote can set `kip71.gastarget` to zero, causing a division-by-zero panic in `NextMagmaBlockBaseFee` during block base-fee calculation - ([File: params/kip71_config.go])

### Summary
Kaia's governance module allows the governing node (in single-governance mode) or the council (in ballot mode) to vote and change the KIP-71 (Magma) dynamic base fee parameters, including `kip71.gastarget`. Unlike `kip71.lowerboundbasefee`/`kip71.upperboundbasefee`, which have explicit consistency checks against each other, `Kip71GasTarget` (along with `Kip71BaseFeeDenominator` and `Kip71MaxBlockGasUsedForBaseFee`) has no lower-bound / non-zero validation anywhere in the vote-acceptance pipeline. A `GasTarget` value of `0` reaches `NextMagmaBlockBaseFee`, which divides by `gasTarget` without a zero-guard, causing every node that computes the next block's base fee to panic.

### Finding Description
The governance parameter definition for `Kip71GasTarget` uses `noopFormatChecker`, i.e., no validation on the canonical value: [1](#0-0) 

The header-vote consistency check (`checkConsistency`) only special-cases `Kip71LowerBoundBaseFee` and `Kip71UpperBoundBaseFee` (checking they don't cross each other); `Kip71GasTarget`, `Kip71BaseFeeDenominator`, and `Kip71MaxBlockGasUsedForBaseFee` fall into the catch-all branch that performs no additional checks and returns `nil`: [2](#0-1) 

This means a vote such as `{key: "kip71.gastarget", value: uint64(0)}` passes both `NewVoteData` format checks and `checkConsistency`, and will be merged into the effective `ParamSet` at the next epoch/param application, ultimately flowing into `ParamSet.ToKip71Config()`: [3](#0-2) 

The resulting `KIP71Config.GasTarget = 0` is then used in `NextMagmaBlockBaseFee`. Note that `BaseFeeDenominator` has a defensive zero-check (falls back to 64), but `GasTarget` has none: [4](#0-3) 

When `gasTarget == 0` and `parentGasUsed > 0` (the common case, since almost any block will use non-zero gas), execution takes the "increase" branch:
```
gasUsedDelta := new(big.Int).SetUint64(parentGasUsed - gasTarget) // = parentGasUsed
x := new(big.Int).Mul(parentBaseFee, gasUsedDelta)
y := x.Div(x, new(big.Int).SetUint64(gasTarget))                  // Div(x, 0) -> panic
```
Go's `math/big.Int.Div` panics with "division by zero" when the divisor is zero. `NextMagmaBlockBaseFee` is called from multiple critical block-production/validation paths, including `work/worker.go`, `blockchain/chain_makers.go`, `blockchain/tx_pool.go`, and gas price estimation code (`node/cn/gasprice/gasprice.go`, `node/cn/gasprice/feehistory.go`), meaning both block proposers and validating nodes would panic when this parameter reaches the effective value 0.

### Impact Explanation
This is analogous to the reported bug class (an `onlyOwner`/privileged setter accepting an invalid value such as zero, causing the contract/system to break) but reachable through Kaia's governance parameter system rather than a Solidity `onlyOwner` function. A single malicious or careless governance vote (from the governing node, or achievable in ballot mode with sufficient validator votes) that sets `kip71.gastarget` to `0` will, once the parameter becomes effective, cause every honest node computing the next base fee to panic and crash — a chain-halting denial of service affecting the entire network, not just a single contract's functionality. This satisfies the "state divergence between honest nodes" / block acceptance criterion in the sense that it can crash the entire network's block production and validation pipeline simultaneously.

### Likelihood Explanation
Likelihood depends on governance permissions: in `single` governance mode only the designated governing node can push such a vote, and in ballot mode a majority/council vote is required. This constrains the attack to a privileged actor (matching the original report's "malicious owner" scenario), but there is no code-level safeguard preventing this catastrophic value from being accepted — unlike the sibling parameters `Kip71LowerBoundBaseFee`/`Kip71UpperBoundBaseFee`, which do have mutual bound validation. The complete absence of a zero-check for `GasTarget` (and lack of a defensive `if kc.GasTarget == 0` fallback, unlike the `BaseFeeDenominator` case in the same function) makes this a straightforward oversight rather than a purely theoretical risk.

### Recommendation
Add a `FormatChecker` for `Kip71GasTarget` in `kaiax/gov/param.go` rejecting `0` (and any other value that could cause degenerate arithmetic), matching the treatment already given to `Kip71LowerBoundBaseFee`/`Kip71UpperBoundBaseFee`. Additionally, add a defensive guard in `NextMagmaBlockBaseFee` (`params/kip71_config.go`) similar to the existing `BaseFeeDenominator` fallback, so that `GasTarget == 0` does not cause a division-by-zero panic even if an invalid value somehow reaches this function (e.g., via a corrupted `ChainConfig` or future code path that bypasses `checkConsistency`).

### Proof of Concept
1. As the governing node (single mode) or via a council majority ballot, submit a header vote: `key = "kip71.gastarget"`, `value = uint64(0)`.
2. `NewVoteData` succeeds (format check for `Kip71GasTarget` is `noopFormatChecker`): [1](#0-0) 
3. `checkConsistency` accepts the vote without any bound checks, since `Kip71GasTarget` is grouped in the no-op branch: [5](#0-4) 
4. Once the vote is applied at the epoch boundary, `GetParamSet` returns `GasTarget = 0`, which is converted via `ToKip71Config()` into the effective `KIP71Config` used for base fee calculation.
5. On the next block where `parentGasUsed != 0` (virtually always true), `NextMagmaBlockBaseFee` executes `x.Div(x, new(big.Int).SetUint64(0))`, which panics in Go's `math/big` package, crashing the node process during block assembly/validation (`work/worker.go`, `blockchain/chain_makers.go`, etc.).

### Citations

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
