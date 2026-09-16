## Finding

### Title
Divide-by-zero panic in KIP-71 base fee calculation via `kip71.gastarget` governance parameter set to 0 - (File: `params/kip71_config.go`)

### Summary
`KIP71Config.NextMagmaBlockBaseFee` divides by the governance-configured `GasTarget` value without checking for zero, unlike the sibling `BaseFeeDenominator` parameter which is explicitly guarded against zero. Because the governance framework's format checker for `kip71.gastarget` is a no-op that accepts any `uint64` including `0`, a governance vote setting `GasTarget` to `0` will pass all validation and, once ratified, cause every node computing the next block's base fee to panic on integer division by zero — analogous to the CVE-2016-3623 pattern where an attacker-controllable divisor parameter of `0` triggers a divide-by-zero crash.

### Finding Description
`NextMagmaBlockBaseFee` explicitly guards `BaseFeeDenominator` against zero: [1](#0-0) 

But `gasTarget` (`kc.GasTarget`) receives no such guard, and is used directly as a divisor in both the "gas used above target" and "gas used below target" branches: [2](#0-1) [3](#0-2) 

If `gasTarget == 0` and `parentGasUsed != 0` (i.e., the block used any gas at all, which is the near-universal case), the code enters one of these branches (`parentGasUsed > gasTarget` is true whenever `parentGasUsed > 0`) and calls `x.Div(x, new(big.Int).SetUint64(gasTarget))`, which panics with a divide-by-zero error for `big.Int` when the divisor is zero.

The value of `GasTarget` is fully attacker/validator-controllable via on-chain governance voting. The `Kip71GasTarget` parameter definition uses `noopFormatChecker`, which accepts any value: [4](#0-3) [5](#0-4) 

Contrast this with `Kip71BaseFeeDenominator`, whose `FormatChecker` explicitly rejects `0`: [6](#0-5) 

Additionally, `checkConsistency` in the header governance verification path — which performs extra semantic checks for `Kip71LowerBoundBaseFee` and `Kip71UpperBoundBaseFee` (e.g., bound ordering) — does not perform any extra check for `Kip71GasTarget`; it falls into the generic "no more checks" case: [7](#0-6) 

Once a `kip71.gastarget = 0` vote is cast by a council member/proposer and ratified at an epoch boundary (per the standard governance vote flow described in the header-gov README), `GasTarget` becomes `0` in the effective `ParamSet`, and every node — while verifying the Magma base fee field of a new header, or while computing `NextMagmaBlockBaseFee`/`ToKip71Config` results for fee history RPC responses — will panic.

### Impact Explanation
A panic in `NextMagmaBlockBaseFee` occurs on the block-processing hot path used to verify header base fees (`VerifyMagmaHeader`) as well as in `node/cn/gasprice/feehistory.go`'s `processBlock`, which is called for public RPC (`eth_feeHistory`). Because this function is called by every full node processing a block (and even RPC nodes computing fee history), a single ratified `GasTarget = 0` governance value causes a chain-wide crash/halt of all nodes — a denial-of-service impacting the entire network, not merely the proposer. This satisfies the "acceptance of an invalid transaction or block" / "state divergence between honest nodes" criteria: nodes lacking a defensive recover would crash entirely, and any node with a recover would still fail to process the header, breaking chain progress.

### Likelihood Explanation
Requires a governance vote to be cast and ratified for `kip71.gastarget = 0`. The vote itself is checked only for proposer/council membership and single-mode governing-node restrictions — no value-level sanity check exists for `GasTarget`, unlike `BaseFeeDenominator`, `LowerBoundBaseFee`, and `UpperBoundBaseFee`, all of which have dedicated protections. Any validator/proposer able to cast a ratified vote (which the rules of this exercise treat as an in-scope "governance parameters" path) can trigger this without needing any other privilege.

### Recommendation
Add a `FormatChecker` for `Kip71GasTarget` in `kaiax/gov/param.go` that rejects `0` (mirroring `Kip71BaseFeeDenominator`'s checker), and/or add a defensive zero-check inside `NextMagmaBlockBaseFee` in `params/kip71_config.go` (as already done for `BaseFeeDenominator`) to fall back to a safe default instead of dividing by zero.

### Proof of Concept
1. A council member/proposer submits `governance_vote("kip71.gastarget", 0)`.
2. The vote passes `NewVoteData`'s format check (`noopFormatChecker` in `kaiax/gov/param.go`) and `checkConsistency` (no special-case for `Kip71GasTarget` in `kaiax/gov/headergov/impl/header.go`).
3. At the next epoch, the vote is ratified into `header.Governance`, setting `ParamSet.GasTarget = 0` for subsequent blocks.
4. On the next Magma-enabled block with any nonzero `GasUsed`, `KIP71Config.NextMagmaBlockBaseFee` executes `parentGasUsed > gasTarget` (true, since `gasTarget=0`), then computes `x.Div(x, new(big.Int).SetUint64(0))`, panicking with "division by zero" in every node validating/producing that block.

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

**File:** params/kip71_config.go (L97-103)
```go
		// If the parent block used more gas than its target,
		// the baseFee of the next block should increase.
		// baseFeeDelta = max(1, parentBaseFee * (parentGasUsed - gasTarget) / gasTarget / baseFeeDenominator)
		gasUsedDelta := new(big.Int).SetUint64(parentGasUsed - gasTarget)
		x := new(big.Int).Mul(parentBaseFee, gasUsedDelta)
		y := x.Div(x, new(big.Int).SetUint64(gasTarget))
		baseFeeDelta := math.BigMax(x.Div(y, baseFeeDenominator), common.Big1)
```

**File:** params/kip71_config.go (L115-121)
```go
		// Otherwise if the parent block used less gas than its target,
		// the baseFee of the next block should decrease.
		// baseFeeDelta = parentBaseFee * (gasTarget - parentGasUsed) / gasTarget / baseFeeDenominator
		gasUsedDelta := new(big.Int).SetUint64(gasTarget - parentGasUsed)
		x := new(big.Int).Mul(parentBaseFee, gasUsedDelta)
		y := x.Div(x, new(big.Int).SetUint64(gasTarget))
		baseFeeDelta := x.Div(y, baseFeeDenominator)
```

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
