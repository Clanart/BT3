### Title
Unvalidated `governance.kip71.gastarget` parameter causes division-by-zero panic in Magma base fee calculation - ([File: params/kip71_config.go])

### Summary
The `Kip71GasTarget` governance parameter is registered with a `noopFormatChecker`, meaning no bound or non-zero check is enforced when it is voted/set. This value is later used unconditionally as a divisor in `KIP71Config.NextMagmaBlockBaseFee()`, which computes every block's base fee post-Magma. If `GasTarget` is set to `0`, this function performs `big.Int.Div(x, 0)`, which panics in Go's `math/big` package, crashing every node that validates or builds a block — an availability/consensus-halting DoS analogous to the reported `performanceFee` underflow bug (an unvalidated numeric governance-style parameter causing a hard failure deep in a core arithmetic routine).

### Finding Description
`Kip71GasTarget` is defined in the governance parameter table with only a `uint64Canonicalizer` and a `noopFormatChecker` — i.e., any `uint64` value including `0` is accepted: [1](#0-0) 

`headerGovModule.checkConsistency()` also performs no additional validation for `gov.Kip71GasTarget` votes — it falls into the case that simply returns `nil` (no more checks after format validation): [2](#0-1) 

The resulting `GasTarget` value flows into `KIP71Config.GasTarget` and is used directly as a divisor in `NextMagmaBlockBaseFee()`, in both the "gas used above target" and "gas used below target" branches: [3](#0-2) [4](#0-3) 

Unlike `BaseFeeDenominator`, which has an explicit zero-guard that substitutes a safe default (`64`) to avoid a panic: [5](#0-4) 

`GasTarget` has no such guard. If `GasTarget == 0` and `parentGasUsed != 0` (the near-universal case for any active chain), the code executes `x.Div(x, gasTarget)` with `gasTarget = 0`, which is a division by zero and results in a runtime panic in `math/big`.

This mirrors the reported bug class: a governance/administrative numeric parameter is accepted without a sanity bound, and a downstream core computation performs unchecked arithmetic (division/subtraction) using that parameter, producing a hard failure (panic/revert) rather than a controlled error.

### Impact Explanation
`NextMagmaBlockBaseFee()` is a consensus-critical function invoked on every block during header validation and block-building (e.g., `KIP71Config.VerifyMagmaHeader`, used from `blockchain/block_validator.go`, `blockchain/tx_pool.go`, `blockchain/chain_makers.go`, `work/worker.go`, and `node/cn/gasprice`). A panic here — rather than a clean error — would crash the node process attempting to validate or build the next block. If this parameter is (mis)configured to `0` via governance (whether accidentally or maliciously by a governing/voting node under single-governance mode, which the codebase explicitly allows to reach `GetParamSet`), every CN, EN, and PN in the network processing the next block will panic simultaneously, i.e. a network-wide denial-of-service / chain halt. This satisfies the "state divergence" / DoS-class impact analogous to the original report (systemic denial of service from an unchecked administrative parameter feeding directly into unguarded arithmetic).

### Likelihood Explanation
The likelihood depends on governance being able to set `GasTarget = 0`, which requires a validator/governance vote to pass (not a plain unprivileged transaction). However, per the scope rules, governance parameters are an explicitly permitted analog surface. There is no on-chain safeguard preventing this value: `NewVoteData`, `checkConsistency`, and the parameter's `FormatChecker` all accept `0` without objection (confirmed via test cases in `kaiax/gov/headergov/vote_test.go` and `header_test.go`, which do not test `Kip71GasTarget == 0` for rejection, unlike `Kip71LowerBoundBaseFee`/`Kip71UpperBoundBaseFee`, which do have explicit cross-validation). Since `BaseFeeDenominator == 0` was already specifically hardened against (evidence the developers were aware of the divide-by-zero risk class for this exact function), the omission for `GasTarget` appears to be an oversight rather than an intentional design decision, increasing confidence this is a genuine gap rather than an accepted risk.

### Recommendation
Add a `FormatChecker` for `Kip71GasTarget` (and equivalently `Kip71MaxBlockGasUsedForBaseFee`, which is also divisor-adjacent via `parentGasUsed := min(parentHeaderGasUsed, upperGasLimit)`) that rejects zero values, consistent with the treatment already given to `BaseFeeDenominator`. Additionally, add a defensive zero-check directly inside `NextMagmaBlockBaseFee()` (mirroring the existing `BaseFeeDenominator == 0` fallback) so that even if an invalid value somehow reaches this function (e.g., via genesis misconfiguration rather than governance vote), the function degrades gracefully instead of panicking.

### Proof of Concept
Conceptual reproduction (cannot execute in this environment, but derivable directly from the cited code):
1. Configure or vote `governance.kip71.gastarget = 0` (accepted because `Kip71GasTarget`'s `FormatChecker` is `noopFormatChecker`, and `checkConsistency` performs no extra validation for this parameter).
2. Once this parameter takes effect and a block with `GasUsed != 0` is processed (virtually guaranteed on an active chain), `KIP71Config.NextMagmaBlockBaseFee()` executes:
   ```go
   gasTarget := kc.GasTarget // == 0
   ...
   } else if parentGasUsed > gasTarget { // true, since gasTarget == 0
       gasUsedDelta := new(big.Int).SetUint64(parentGasUsed - gasTarget)
       x := new(big.Int).Mul(parentBaseFee, gasUsedDelta)
       y := x.Div(x, new(big.Int).SetUint64(gasTarget)) // division by zero -> panic
   ``` [6](#0-5) 
3. This is called from `blockchain/block_validator.go` during header validation for every subsequent block, causing every node validating or building a block to panic, halting the chain.

### Citations

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

**File:** params/kip71_config.go (L92-103)
```go
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

**File:** params/kip71_config.go (L116-122)
```go
		// the baseFee of the next block should decrease.
		// baseFeeDelta = parentBaseFee * (gasTarget - parentGasUsed) / gasTarget / baseFeeDenominator
		gasUsedDelta := new(big.Int).SetUint64(gasTarget - parentGasUsed)
		x := new(big.Int).Mul(parentBaseFee, gasUsedDelta)
		y := x.Div(x, new(big.Int).SetUint64(gasTarget))
		baseFeeDelta := x.Div(y, baseFeeDenominator)

```
