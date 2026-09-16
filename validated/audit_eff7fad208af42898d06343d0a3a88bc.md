## Analysis

The report's bug class — a governance-controlled numeric parameter accepted without a zero-value check, leading to unsafe arithmetic — has a direct, stronger analog in Kaia's KIP-71 (Magma) dynamic base fee mechanism.

`Kip71GasTarget` is registered as a governable parameter with a `noopFormatChecker`, meaning **no validation is performed on its value**, unlike sibling parameters that do get checked (e.g. `RewardMinimumStake` checks `v.Sign() >= 0`): [1](#0-0) 

This value flows into `KIP71Config.GasTarget` and is consumed unchecked in `NextMagmaBlockBaseFee`, which explicitly guards against `BaseFeeDenominator == 0` but has **no equivalent guard for `GasTarget == 0`**: [2](#0-1) 

When `GasTarget == 0` and any nonzero gas is used in the parent block (the normal case for a live chain), execution takes the `parentGasUsed > gasTarget` branch and divides by `gasTarget` directly: [3](#0-2) 

`big.Int.Div` panics on division by zero in Go, so this call panics deterministically on every node that executes it.

`checkConsistency` in the header governance module explicitly whitelists `gov.Kip71GasTarget` in the group of parameters that pass with "no more checks here", confirming that a vote setting `gastarget = 0` is accepted at the governance-verification layer with no additional bound checking: [4](#0-3) 

`NextMagmaBlockBaseFee` (and its wrapper `VerifyMagmaHeader`) is called from core, consensus-relevant paths: block validation, block assembly, mining, the tx pool, and gas price estimation — i.e., every node runs this on every new block: [5](#0-4) [6](#0-5) 

### Title
Unvalidated `kip71.gastarget` governance parameter can be set to 0, causing a division-by-zero panic in `NextMagmaBlockBaseFee` on every node - (File: params/kip71_config.go)

### Summary
The KIP-71 governance parameter `GasTarget` has no zero-value validation at the format-checker level, unlike `BaseFeeDenominator` which explicitly guards against zero in the same function. A governance vote setting `gastarget = 0` is accepted by `checkConsistency` without restriction and is later applied by all nodes, triggering a division-by-zero panic in the dynamic base-fee calculation used by block validation and block assembly.

### Finding Description
`Kip71GasTarget`'s `Param` definition uses `noopFormatChecker`, so any `uint64` value including `0` passes format validation: [1](#0-0) . The header governance consistency check (`checkConsistency`) explicitly classifies `gov.Kip71GasTarget` among parameters requiring "no more checks here" beyond the `NewVoteData` format check: [7](#0-6) . Once such a vote reaches quorum at an epoch boundary, `GasTarget` becomes part of the enacted `ParamSet` and is copied into the chain's `KIP71Config.GasTarget` used at runtime.

`NextMagmaBlockBaseFee` defensively handles a zero `BaseFeeDenominator` by substituting a fallback value of 64, explicitly commented "To avoid panic": [2](#0-1) . No equivalent fallback exists for `GasTarget`. When `parentGasUsed > gasTarget` (true for essentially any block with `gasTarget == 0` and nonzero gas usage), the code computes `x.Div(x, new(big.Int).SetUint64(gasTarget))`, dividing by zero: [8](#0-7) . The symmetric decrease branch has the same unguarded division: [9](#0-8) . Go's `big.Int.Div` panics on a zero divisor, so this crashes the calling goroutine.

### Impact Explanation
`NextMagmaBlockBaseFee`/`VerifyMagmaHeader` are invoked from block validation, block assembly (`chain_makers.go`), the miner's worker loop (`work/worker.go`), the transaction pool, and gas price estimation. Because the malicious/faulty parameter is enacted network-wide via governance and read from the shared chain config, every node — validators and full nodes alike — hits the same panic when processing the first post-effective block with nonzero gas usage. This is a consensus-critical denial-of-service: it can halt block production and validation chain-wide, which is a more severe outcome than the original report's fund-related soft-cap concern.

### Likelihood Explanation
Reaching this requires only a governance vote setting `kip71.gastarget` to `0`, which passes all existing validation (`FormatChecker` is a no-op, and `checkConsistency` performs no bound check). Governance voting is a normal, unprivileged-relative-to-this-bug operation available to council members; no malicious validator collusion or off-chain compromise is needed beyond a single vote reaching adoption. Given that essentially any live chain will produce blocks with nonzero gas usage, the panic is triggered deterministically at the very first applicable block once the parameter is effective.

### Recommendation
Add a `FormatChecker` for `Kip71GasTarget` that rejects `0` (matching the pattern already used for `RewardMinimumStake`), and/or add a defensive zero check inside `NextMagmaBlockBaseFee` mirroring the existing `BaseFeeDenominator == 0` fallback, e.g. falling back to `DefaultGasTarget` when `kc.GasTarget == 0`.

### Proof of Concept
1. Governance council votes to set `kip71.gastarget = 0`; the vote passes `NewVoteData` format checks (no-op checker) and `checkConsistency` (explicitly unrestricted).
2. At the next epoch boundary, the vote is enacted into the chain's `ParamSet`/`KIP71Config.GasTarget`.
3. On the next block after activation with `parentHeaderGasUsed > 0`, every node calls `NextMagmaBlockBaseFee`, which executes `x.Div(x, new(big.Int).SetUint64(0))` and panics, crashing block validation/assembly network-wide.

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

**File:** params/kip71_config.go (L117-121)
```go
		// baseFeeDelta = parentBaseFee * (gasTarget - parentGasUsed) / gasTarget / baseFeeDenominator
		gasUsedDelta := new(big.Int).SetUint64(gasTarget - parentGasUsed)
		x := new(big.Int).Mul(parentBaseFee, gasUsedDelta)
		y := x.Div(x, new(big.Int).SetUint64(gasTarget))
		baseFeeDelta := x.Div(y, baseFeeDenominator)
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

**File:** blockchain/block_validator.go (L1-1)
```go
// Modifications Copyright 2024 The Kaia Authors
```

**File:** work/worker.go (L1-1)
```go
// Modifications Copyright 2024 The Kaia Authors
```
