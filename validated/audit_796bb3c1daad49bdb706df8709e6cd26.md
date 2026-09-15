## Title
Missing zero-validation for governance-controlled `Kip71GasTarget` causes divide-by-zero panic in base fee calculation - (File: `params/kip71_config.go`)

### Summary
The reported issue describes a missing `> 0` validation on a value later used as a divisor in a curve/pricing formula, enabling division-by-zero and consensus-critical breakage. Kaia does not have `TermMaxOrder`/AMM curves, but it has a directly analogous pattern in the KIP-71 (Magma/EIP-1559-style) base-fee formula, where `GasTarget` is a governance-settable parameter used unconditionally as a divisor with no non-zero check at the point of consumption.

### Finding Description
`KIP71Config.NextMagmaBlockBaseFee` uses `kc.GasTarget` directly as a divisor in `big.Int.Div` calls when the parent block's gas usage differs from `gasTarget`: [1](#0-0) [2](#0-1) 

Unlike `BaseFeeDenominator`, which has an explicit `== 0` fallback guard before being used as a divisor: [3](#0-2) 

`GasTarget` has no equivalent guard. If `GasTarget == 0`, then for any `parentGasUsed > 0` the code takes the `parentGasUsed > gasTarget` branch and executes `x.Div(x, new(big.Int).SetUint64(gasTarget))` with a zero divisor, which panics in Go's `math/big` package (`big.Int.Div` by zero panics rather than returning an error).

Critically, the format checker registered for this governance parameter is a no-op, meaning the governance voting/parameter-set pipeline performs **no validation that `GasTarget` is non-zero** before it is committed to consensus state: [4](#0-3) 

Compare this to `Kip71BaseFeeDenominator`, which explicitly rejects zero at the format-checker level: [5](#0-4) 

The governance vote consistency check (`checkConsistency`) also does not add any additional validation for `Kip71GasTarget` beyond the no-op format check — it simply accepts it in the pass-through case list: [6](#0-5) 

This is structurally the same bug class as the report: a governance/config-controlled numeric input that flows unchecked into a division operation used in the core pricing/fee formula, with only a sibling parameter (`BaseFeeDenominator`/`liqSquare` peer values) receiving proper validation while this one does not.

### Impact Explanation
`NextMagmaBlockBaseFee` is called on every block during header verification (`VerifyMagmaHeader`) and base fee computation, which is on the block-assembly/consensus-critical path reachable indirectly by governance vote (a governance parameter change reachable via a submitted governance vote transaction). If `GasTarget` is ever voted to `0` (or otherwise set to `0`, e.g. misconfiguration or a permissioned/governing-node vote that isn't properly caught because the format checker is a no-op), any block with non-zero gas usage relative to the parent will trigger a panic in the base fee computation, which would crash node processes computing the next block's base fee — a chain-halting/state-divergence condition across all nodes evaluating that header, rather than isolated to one node. This matches the report's "Protocol Insolvency"/systemic-failure class of impact (invalid governance state creating protocol-breaking computation), scaled to consensus-node crash and potential chain halt instead of pure fund drain.

### Likelihood Explanation
Likelihood is constrained by the fact that `GasTarget` can only be changed via the governance vote mechanism (requires governance-node/validator voting privileges under the "single" or council governance mode), not by an arbitrary unprivileged transaction sender directly. This reduces the likelihood versus a fully public/permissionless trigger, but the report's own severity class explicitly includes "governance parameters" as an in-scope reachable path. Since there is no validation anywhere in the vote-format-checking, vote-consistency-checking, or consumption code paths that rejects `GasTarget == 0`, a single governance vote (which is the "transaction" that reaches this code) is sufficient to plant the time-bomb; no additional conditions are required beyond a subsequent block where `parentGasUsed != 0`.

### Recommendation
Add an explicit non-zero `FormatChecker` for `Kip71GasTarget` in `kaiax/gov/param.go`, analogous to the one used for `Kip71BaseFeeDenominator`:
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
Additionally, as defense-in-depth, add a zero-guard directly in `NextMagmaBlockBaseFee` (mirroring the existing `BaseFeeDenominator == 0` fallback) so that even a config loaded from genesis/legacy state with `GasTarget == 0` cannot panic: [7](#0-6) 

### Proof of Concept
Conceptual reproduction using the existing config path:
1. Set `chainConfig.Governance.KIP71.GasTarget = 0` (either via a successful governance vote that is never rejected by the no-op format checker, or directly in a test/genesis config as the existing test `TestEvenBaseFee` demonstrates is possible for other fields).
2. Call `KIP71Config.NextMagmaBlockBaseFee(parentNumber, parentBaseFee, parentGasUsed)` with any `parentGasUsed > 0` (not equal to `gasTarget`), e.g. `parentGasUsed = 1`.
3. Execution reaches: [8](#0-7) 
`x.Div(x, new(big.Int).SetUint64(0))` panics with "division by zero" inside `math/big`, since Go's `big.Int.Div` does not return an error for zero divisors.
4. This function is invoked during header verification for every block (`VerifyMagmaHeader`), so the panic would occur consistently across all nodes computing the next base fee once such a header is processed, is unrecoverable without a code fix, and could halt the chain.

Note: I was unable to fully trace the exact code path that submits/applies a `Kip71GasTarget` governance vote end-to-end (e.g., whether any other layer outside `kaiax/gov` might reject a zero value before it reaches `ChainConfig.Governance.KIP71.GasTarget`), so confirm this via a live governance-vote integration test before treating this as fully proven in production.

### Citations

**File:** params/kip71_config.go (L70-78)
```go
	var baseFeeDenominator *big.Int
	if kc.BaseFeeDenominator == 0 {
		// To avoid panic, set the fluctuation range small
		baseFeeDenominator = new(big.Int).SetUint64(64)
	} else {
		baseFeeDenominator = new(big.Int).SetUint64(kc.BaseFeeDenominator)
	}
	gasTarget := kc.GasTarget
	upperGasLimit := kc.MaxBlockGasUsedForBaseFee
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
