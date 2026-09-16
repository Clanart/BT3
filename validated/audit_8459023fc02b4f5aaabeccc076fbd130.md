## Analysis

Confirmed root cause: the `Kip71GasTarget` governance parameter has **no format validation** (`FormatChecker: noopFormatChecker`), unlike its sibling `Kip71BaseFeeDenominator`, which explicitly rejects zero (`return ok && v != 0`). `checkConsistency()` in `headerGovModule.VerifyVote` also does not add any special-case validation for `gov.Kip71GasTarget` (it falls into the generic "no more checks here" case). `NextMagmaBlockBaseFee()` in `params/kip71_config.go` divides by `gasTarget` unconditionally whenever `parentGasUsed != gasTarget`, with no zero-check (unlike `BaseFeeDenominator`, which is explicitly defaulted to 64 if zero). If `gasTarget` is voted to `0` via governance and accepted into a header, every node computing the next block's base fee (KIP-71/Magma dynamic base fee) will call `big.Int.Div` with divisor zero, which panics in Go's `math/big` package.

### Title
Ungracefully-validated `Kip71GasTarget` governance parameter allows divide-by-zero panic in KIP-71 base fee calculation - (File: `kaiax/gov/param.go`, `params/kip71_config.go`)

### Summary
The `Kip71GasTarget` governance parameter is registered with `FormatChecker: noopFormatChecker` [1](#0-0) , meaning any `uint64` value, including `0`, passes format validation when a validator casts a governance vote to change `gasTarget`. `checkConsistency()` treats `gov.Kip71GasTarget` as one of the parameters requiring "no more checks" beyond the format check [2](#0-1) . The value flows directly into `KIP71Config.GasTarget` and is used unguarded as a divisor in `NextMagmaBlockBaseFee` [3](#0-2) .

### Finding Description
`NextMagmaBlockBaseFee` computes the next block's base fee (used by every full/consensus node after the Magma hardfork) as follows:
```go
gasUsedDelta := new(big.Int).SetUint64(parentGasUsed - gasTarget)
x := new(big.Int).Mul(parentBaseFee, gasUsedDelta)
y := x.Div(x, new(big.Int).SetUint64(gasTarget))   // <-- divide by gasTarget
``` [4](#0-3) 
and the symmetric branch for the "used less gas than target" case also divides by `gasTarget`: [5](#0-4) 

Unlike `BaseFeeDenominator`, which has an explicit fallback (`if kc.BaseFeeDenominator == 0 { baseFeeDenominator = new(big.Int).SetUint64(64) }`) to avoid a panic [6](#0-5) , there is no equivalent guard for `GasTarget == 0`. The governance parameter registry only prevents `BaseFeeDenominator` from being zero (`FormatChecker: func(cv any) bool { v, ok := cv.(uint64); return ok && v != 0 }` [7](#0-6) ), but `Kip71GasTarget` uses `noopFormatChecker`, so `gasTarget = 0` is a legal, acceptable governance value [1](#0-0) .

A council member (validator) with voting rights can cast a `governance.kip71.gastarget = 0` vote through the normal governance-vote mechanism (`header.Vote`). Since `parentGasUsed` (any non-zero block gas usage) will practically always differ from `gasTarget=0`, subsequent base-fee computation for any block after this parameter takes effect executes `big.Int.Div(x, 0)`, causing a **runtime panic** in every node computing/verifying the header's base fee (block production, `VerifyMagmaHeader`, `feehistory.go`'s `processBlock`, gas price oracle `SuggestPrice`, etc.).

This directly mirrors the reported bug class: an unvalidated "minimum/target" parameter reaching an unguarded divisor in an AMM/fee-calculation routine, causing funds/operations to become unusable — here it causes chain-wide denial of service / crash instead of stuck ETH, but the root cause (missing zero-check before use as a divisor) is identical.

### Impact Explanation
A zero `gasTarget` reaching consensus would panic every node (CN/PN/EN) that recomputes the base fee for the next block — this includes block validators verifying `VerifyMagmaHeader` and block producers preparing headers. This is a chain-halting denial-of-service affecting the entire network's liveness, and could also cause state divergence between nodes running different Go versions/recover-wrappers if some catch the panic and others don't. This satisfies the "acceptance of an invalid transaction/parameter causing state divergence" and "DoS via unauthorized parameter" criteria at High impact.

### Likelihood Explanation
Likelihood is Low-to-Medium: the change requires an active governance vote from a validator/council member currently permitted to vote (or the governing node in single mode), which is a privileged but still "unprivileged-relative-to-protocol-code" action reachable through the ordinary governance voting RPC, not requiring any code-level compromise. There is no additional validation anywhere in the pipeline (format checker, `checkConsistency`, or the base fee formula) that would catch or reject `gasTarget = 0` before it is committed to the chain config and used as a divisor.

### Recommendation
Add a zero-check to `Kip71GasTarget`'s `FormatChecker` (mirroring `Kip71BaseFeeDenominator`'s `v != 0` check) in `kaiax/gov/param.go`, and/or add a defensive zero-fallback for `gasTarget` in `NextMagmaBlockBaseFee` in `params/kip71_config.go`, analogous to the existing `BaseFeeDenominator == 0` fallback.

### Proof of Concept
1. A validator submits a governance vote setting `governance.kip71.gastarget` to `0`.
2. Because `Kip71GasTarget`'s `FormatChecker` is `noopFormatChecker` [1](#0-0) , the vote is accepted as valid, and `checkConsistency` performs no further check for this parameter [2](#0-1) .
3. Once the vote is finalized at the epoch boundary, `GasTarget = 0` is applied to `KIP71Config`.
4. On the next block where `parentGasUsed != 0`, `NextMagmaBlockBaseFee` executes `x.Div(x, new(big.Int).SetUint64(0))` [3](#0-2) , which panics with "division by zero" in Go's `math/big`, crashing every node that processes/verifies this block (block production, header verification, `feehistory.go` processing, gas price oracle).

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

**File:** params/kip71_config.go (L99-121)
```go
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
