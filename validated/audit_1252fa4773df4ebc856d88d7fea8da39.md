Confirmed: `Kip71GasTarget` explicitly falls into the `checkConsistency` case (line 216, `gov.Kip71GasTarget`) that returns `nil` — "These votes are valid as long as it passes the format checks in NewVoteData(). No more checks here." [1](#0-0)  And the format check for `Kip71GasTarget` is `noopFormatChecker`, which accepts any `uint64` value including `0`, unlike `Kip71BaseFeeDenominator` which explicitly requires `v != 0`. [2](#0-1) 

### Title
Missing zero-value validation on `governance.kip71.gastarget` allows a governance vote to set GasTarget=0, causing a division-by-zero panic in `NextMagmaBlockBaseFee` - (File: `params/kip71_config.go`, `kaiax/gov/param.go`)

### Summary
The KIP-71 (Magma) dynamic base fee formula divides by the governance-controlled `GasTarget` parameter. While `Kip71BaseFeeDenominator` has an explicit `FormatChecker` requiring a non-zero value, `Kip71GasTarget` uses `noopFormatChecker`, which performs no validation at all. A validator can therefore submit a governance vote setting `governance.kip71.gastarget` to `0`, and once adopted this value is fed directly into `big.Int.Div`, which panics on division by zero, rather than returning a graceful error.

### Finding Description
`NextMagmaBlockBaseFee` computes the delta between `parentGasUsed` and `gasTarget` and divides intermediate values by `gasTarget` directly, with no guard against `gasTarget == 0`: [3](#0-2) [4](#0-3) 

Compare this to `BaseFeeDenominator`, which does have a defensive zero-check inline (`if kc.BaseFeeDenominator == 0 { baseFeeDenominator = 64 }`) [5](#0-4)  — no equivalent fallback exists for `GasTarget`.

The governance parameter registry defines `Kip71GasTarget` with `FormatChecker: noopFormatChecker`, allowing any `uint64`, whereas `Kip71BaseFeeDenominator` explicitly requires `v != 0`: [2](#0-1) 

When a vote for `Kip71GasTarget` is embedded in a header, `checkConsistency` in the header governance module performs no additional semantic check for it, explicitly stating that format checks in `NewVoteData()` are sufficient: [1](#0-0) 

Once accepted as the network's new `GasTarget = 0` at the next epoch, every subsequent block whose `parentGasUsed != 0` triggers the `parentGasUsed > gasTarget` branch of `NextMagmaBlockBaseFee`, executing `x.Div(x, new(big.Int).SetUint64(gasTarget))` with `gasTarget = 0`. Go's `big.Int.Div` panics (it does not return an error) on a zero divisor. This function is invoked from block header validation (`VerifyMagmaHeader`) for every node processing new blocks.

### Impact Explanation
A panic during header/base-fee validation, reachable by every full node and validator processing new blocks, causes a chain-wide crash/halt — a denial-of-service impacting the entire network's ability to progress, not just a single node. This matches the class of "acceptance of an invalid transaction/block causing state divergence or halt" and "governance parameters" reachability listed as in-scope.

### Likelihood Explanation
Likelihood depends on whether a malicious or careless validator can get a `GasTarget=0` vote approved by governance quorum. This still requires validator/council participation (votes must come from council members per `VerifyVote`), so it is not a fully permissionless single-transaction attack, but it is reachable through the standard governance parameter voting mechanism without any special privileged "operator-only" backdoor — any council member can propose it, and no consistency check prevents it, unlike the analogous `LowerBoundBaseFee`/`UpperBoundBaseFee` pair which do have cross-field consistency checks (lines 188–201 of the same file).

### Recommendation
Add a `FormatChecker` for `Kip71GasTarget` (and ideally `Kip71MaxBlockGasUsedForBaseFee`) requiring `v != 0`, mirroring the check already in place for `Kip71BaseFeeDenominator`. Additionally, add a defensive zero-check with a safe fallback inside `NextMagmaBlockBaseFee` before dividing by `gasTarget`, consistent with the existing `baseFeeDenominator` zero-guard.

### Proof of Concept
1. A validator submits a governance vote header setting `governance.kip71.gastarget = 0`. `NewVoteData` canonicalizes it via `uint64Canonicalizer` and calls `FormatChecker` = `noopFormatChecker`, which accepts it unconditionally.
2. `checkConsistency` for `gov.Kip71GasTarget` returns `nil` without any additional check [6](#0-5) .
3. At the next epoch boundary, this value becomes the active `GasTarget` in `ParamSet`/`KIP71Config`.
4. On the next block where `parentGasUsed > 0`, `NextMagmaBlockBaseFee` executes:
```go
gasUsedDelta := new(big.Int).SetUint64(parentGasUsed - gasTarget) // = parentGasUsed
x := new(big.Int).Mul(parentBaseFee, gasUsedDelta)
y := x.Div(x, new(big.Int).SetUint64(gasTarget)) // gasTarget = 0 → panic: division by zero
``` [7](#0-6) 
5. This panics inside header verification for every node computing/verifying the expected base fee, crashing block production/validation network-wide.

### Citations

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

**File:** kaiax/gov/param.go (L310-334)
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

**File:** params/kip71_config.go (L96-103)
```go
		}
		// If the parent block used more gas than its target,
		// the baseFee of the next block should increase.
		// baseFeeDelta = max(1, parentBaseFee * (parentGasUsed - gasTarget) / gasTarget / baseFeeDenominator)
		gasUsedDelta := new(big.Int).SetUint64(parentGasUsed - gasTarget)
		x := new(big.Int).Mul(parentBaseFee, gasUsedDelta)
		y := x.Div(x, new(big.Int).SetUint64(gasTarget))
		baseFeeDelta := math.BigMax(x.Div(y, baseFeeDenominator), common.Big1)
```

**File:** params/kip71_config.go (L110-121)
```go
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
