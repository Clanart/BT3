### Title
Divide-by-zero panic in KIP-71 base fee calculation via unchecked `GasTarget` governance vote - (File: `params/kip71_config.go`)

### Summary
The `Kip71GasTarget` governance parameter is registered with a no-op format checker, allowing it to be set to `0` through the standard governance-vote flow. This value later flows unchecked into `KIP71Config.NextMagmaBlockBaseFee`, where it is used as a divisor. A zero `GasTarget` causes a division-by-zero panic in `math/big.Int.Div`, which is invoked on every block after Magma activation, crashing block production/verification.

### Finding Description
Every other KIP-71 parameter that is used as a divisor is explicitly guarded against a zero value:
- `Kip71BaseFeeDenominator`'s `FormatChecker` rejects `0`: [1](#0-0) 
- `NextMagmaBlockBaseFee` even has a defensive fallback for `BaseFeeDenominator == 0` ("To avoid panic..."): [2](#0-1) 

By contrast, `Kip71GasTarget` uses `noopFormatChecker`, so any `uint64` value including `0` is accepted as a valid vote value: [3](#0-2) 

When a validator/council member (block proposer, per the header-vote mechanism) casts this vote, `checkConsistency` explicitly treats `gov.Kip71GasTarget` as needing "no more checks here" beyond the format check performed in `NewVoteData()`: [4](#0-3) 

The resulting `GasTarget=0` is later read into `KIP71Config.GasTarget` and used as the direct divisor in `NextMagmaBlockBaseFee`, both in the gas-used-above-target branch and gas-used-below-target branch: [5](#0-4) 

Specifically:
- `parentGasUsed := min(parentHeaderGasUsed, upperGasLimit)` — for any block with nonzero gas used, `parentGasUsed > gasTarget(=0)`.
- The code then executes `x.Div(x, new(big.Int).SetUint64(gasTarget))` at both line 102 (`x.Div(x, gasTarget)`) in the "above target" branch and line 120 in the "below target" branch — `gasTarget` here is `0`.

`math/big.Int.Div` panics with "division by zero" when the divisor is zero, unlike the ClickHouse CVE's silent-corruption modulo bug, but the root cause is identical in class: an attacker/governance-controlled value flows into a division/modulo operation without a zero-check, despite a sibling parameter (`BaseFeeDenominator`) demonstrating that the code owners were aware such checks were necessary.

This function is called from core consensus-critical paths: block validation (`blockchain/block_validator.go`), the tx pool (`blockchain/tx_pool.go`), block assembly (`work/worker.go`), and RPC (`node/cn/gasprice/*`), meaning a panic here would be triggered on every node computing or validating the next block's base fee after the vote takes effect.

### Impact Explanation
Once the malicious/faulty `GasTarget=0` vote is included in the governance data at an epoch boundary (per the standard `checkConsistency`/`VerifyGov` flow), the following epoch's blocks with any nonzero gas usage will cause `NextMagmaBlockBaseFee` to panic on every full node, halting block production and verification network-wide — a consensus-halting Denial-of-Service reachable via the officially supported governance-parameter voting mechanism.

### Likelihood Explanation
This requires a block proposer/council member (the only entity permitted to write `header.Vote`, verified in `VerifyVote`) to cast a `Kip71GasTarget=0` vote. This falls within the explicitly in-scope "governance parameters" category. Since no additional bounds are enforced by either the `FormatChecker` or `checkConsistency` for this parameter (unlike its sibling `BaseFeeDenominator`), a single malicious or buggy governing/council node can trigger this without needing any additional privilege escalation beyond what is normally granted to governance participants.

### Recommendation
Add a `FormatChecker` for `Kip71GasTarget` that rejects `0` (mirroring `Kip71BaseFeeDenominator`'s check at `kaiax/gov/param.go:310-323`), and/or add a defensive zero-check with fallback inside `NextMagmaBlockBaseFee` in `params/kip71_config.go`, analogous to the existing `BaseFeeDenominator == 0` guard at lines 70-76.

### Proof of Concept
1. A council member/governing node casts a vote to set `Kip71GasTarget` to `0` (accepted because `noopFormatChecker` performs no validation, and `checkConsistency` in `kaiax/gov/headergov/impl/header.go:216` performs no additional check for this key).
2. The vote is aggregated into `Governance` at the next epoch boundary via `getExpectedGovernance`/`VerifyGov`, updating the effective `ParamSet.GasTarget` to `0`.
3. On the next block after the epoch, any node with `GasUsed > 0` calls `KIP71Config.NextMagmaBlockBaseFee(parentHeaderNumber, parentHeaderBaseFee, parentHeaderGasUsed)`; since `parentGasUsed(> 0) > gasTarget(0)`, execution reaches `x.Div(x, new(big.Int).SetUint64(gasTarget))` with `gasTarget = 0`, which panics in `math/big`.
4. This function is invoked from block assembly, block validation, and the tx pool's gas-price computation, so the panic occurs on every node attempting to produce or validate the next block — halting the chain.

**Note:** I was unable to fully verify (within available searches) whether any other validation layer outside of `kaiax/gov/param.go` and `kaiax/gov/headergov/impl/header.go` performs additional bounds-checking on `GasTarget` before it reaches `KIP71Config`. If such a check exists elsewhere in the codebase (not surfaced by my searches), it could mitigate this finding.

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

**File:** params/kip71_config.go (L88-122)
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
