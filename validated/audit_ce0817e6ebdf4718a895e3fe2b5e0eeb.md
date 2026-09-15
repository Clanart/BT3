### Title
Missing zero-value check on `kip71.gastarget` governance parameter causes divide-by-zero panic in `NextMagmaBlockBaseFee` - ([File: params/kip71_config.go])

### Summary
`KIP71Config.NextMagmaBlockBaseFee` divides by `gasTarget` without ever checking for zero, unlike its sibling parameter `BaseFeeDenominator`, which is explicitly guarded against zero both in the config code and in its governance `FormatChecker`. The `Kip71GasTarget` governance parameter uses `noopFormatChecker`, so a value of `0` is accepted as valid, and once effective it triggers a `big.Int` division panic on every subsequent block, analogous to the CVE-2017-6835 divide-by-zero crash pattern (`reset1`/`BlockCodec.cpp` dividing by an attacker/crafted-input-controlled value with no zero check).

### Finding Description
In `params/kip71_config.go`, `NextMagmaBlockBaseFee` computes the next block's base fee by dividing intermediate values by `gasTarget`: [1](#0-0) [2](#0-1) 

Note that `BaseFeeDenominator` is explicitly special-cased when zero to avoid a panic: [3](#0-2) 

But no equivalent guard exists for `gasTarget`. If `gasTarget == 0` and `parentGasUsed != gasTarget` (i.e., any nonzero gas usage, which is essentially guaranteed on any active chain), execution reaches `x.Div(x, new(big.Int).SetUint64(gasTarget))`, which is a division by the value `0`. Go's `math/big.Int.Div` panics on division by zero.

The root cause is that the governance parameter `Kip71GasTarget` has no format validation preventing zero: [4](#0-3) 

Compare this to `Kip71BaseFeeDenominator`, which explicitly rejects zero: [5](#0-4) 

Furthermore, `checkConsistency` in the header-governance vote-verification path performs no additional consistency check for `Kip71GasTarget` votes — it simply falls into the passthrough case that returns `nil`: [6](#0-5) 

So a vote setting `kip71.gastarget = 0` passes both `NewVoteData`'s format check and `checkConsistency`, becomes part of the effective `ParamSet`, and is propagated into `KIP71Config.GasTarget`, which is subsequently consumed by `NextMagmaBlockBaseFee` used in block validation (`VerifyMagmaHeader`, called from `blockchain/block_validator.go`) and in block assembly (`work/worker.go`), i.e., on the hot path executed by **every node for every block** once the parameter becomes effective.

### Impact Explanation
Once `kip71.gastarget` is set to `0` and takes effect, every node in the network will panic while computing/verifying the next block's base fee as soon as any transaction consumes gas (which is virtually certain). This is a full chain-halting denial of service across all honest nodes simultaneously — a more severe outcome than the original audiofile CVE (single-process crash on a malicious file), because it affects consensus-critical code executed uniformly by every validator/full node, causing a synchronized crash/halt of the network rather than isolated resource-only damage.

### Likelihood Explanation
Setting `kip71.gastarget` requires a successful governance vote (in single mode, from the governing node; in ballot mode, via council majority) — this is not a fully unprimed unprivileged-attacker vector, but it is a legitimate reachable path through the in-scope "governance parameters" category, and the actual bug is a straightforward missing validation (asymmetric with the analogous `BaseFeeDenominator` parameter, which the codebase authors clearly recognized needed a zero-guard but omitted for `GasTarget`). No cryptographic or p2p-level access is required — only a successful on-chain governance vote using the standard vote mechanism.

### Recommendation
Add a `FormatChecker` for `Kip71GasTarget` in `kaiax/gov/param.go` that rejects `0` (mirroring `Kip71BaseFeeDenominator`'s `v != 0` check), and/or add a defensive zero-guard directly in `NextMagmaBlockBaseFee` in `params/kip71_config.go` analogous to the existing `BaseFeeDenominator == 0` fallback, to prevent a panic regardless of how the value is set (governance vote, `ChainConfig`, or genesis).

### Proof of Concept
1. Governance vote sets `kip71.gastarget = 0` (passes `NewVoteData` format check since `Kip71GasTarget.FormatChecker == noopFormatChecker`, and passes `checkConsistency` since there's no dedicated case for it).
2. The vote becomes effective in `ParamSet`, and `GetParamSet(blockNum).GasTarget` propagates `0` into `KIP71Config.GasTarget` used for the next block.
3. Any block with `GasUsed > 0` (essentially every real block) causes `NextMagmaBlockBaseFee` to execute `x.Div(x, new(big.Int).SetUint64(0))` at `params/kip71_config.go:102` (or the symmetric branch at line 120), panicking with "division by zero."
4. Because this function is invoked both during header verification (`VerifyMagmaHeader`, from `blockchain/block_validator.go`) and block assembly (`work/worker.go`), every node processing/verifying that block crashes — halting the chain.

### Citations

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

**File:** params/kip71_config.go (L99-103)
```go
		// baseFeeDelta = max(1, parentBaseFee * (parentGasUsed - gasTarget) / gasTarget / baseFeeDenominator)
		gasUsedDelta := new(big.Int).SetUint64(parentGasUsed - gasTarget)
		x := new(big.Int).Mul(parentBaseFee, gasUsedDelta)
		y := x.Div(x, new(big.Int).SetUint64(gasTarget))
		baseFeeDelta := math.BigMax(x.Div(y, baseFeeDenominator), common.Big1)
```

**File:** params/kip71_config.go (L117-121)
```go
		// baseFeeDelta = parentBaseFee * (gasTarget - parentGasUsed) / gasTarget / baseFeeDenominator
		gasUsedDelta := new(big.Int).SetUint64(gasTarget - parentGasUsed)
		x := new(big.Int).Mul(parentBaseFee, gasUsedDelta)
		y := x.Div(x, new(big.Int).SetUint64(gasTarget))
		baseFeeDelta := x.Div(y, baseFeeDenominator)
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
