Confirmed: `NextMagmaBlockBaseFee` in `params/kip71_config.go` explicitly guards against `BaseFeeDenominator == 0` (line 71-76: falls back to 64) but has **no equivalent guard for `GasTarget == 0`**, and `Kip71GasTarget`'s `FormatChecker` in `kaiax/gov/param.go` is `noopFormatChecker` (always returns true), unlike `Kip71BaseFeeDenominator` which explicitly rejects `v == 0` [1](#0-0) . This is directly analogous to the reported `MAX_TTL` issue: a governance-tunable value that is silently allowed to take an invalid/dangerous value.

### Title
Missing Zero-Value Validation on `Kip71GasTarget` Governance Parameter Causes Divide-by-Zero Panic in Base Fee Calculation - ([File: params/kip71_config.go])

### Summary
The KIP-71 dynamic base-fee formula divides by `GasTarget` in two branches of `NextMagmaBlockBaseFee`. The governance parameter `kip71.gastarget` that feeds this value uses `noopFormatChecker`, meaning any `uint64` value — including `0` — passes format validation when voted on or set via governance. When `GasTarget == 0` and `parentGasUsed != 0`, `NextMagmaBlockBaseFee` performs `x.Div(x, new(big.Int).SetUint64(gasTarget))` with `gasTarget = 0`, which panics in Go's `math/big` package. This function is called on every block during header verification (`VerifyMagmaHeader`) and block assembly, so a single accepted governance vote setting `GasTarget = 0` will crash every full node in the network the moment it takes effect.

### Finding Description
`params/kip71_config.go`'s `NextMagmaBlockBaseFee` already anticipates and defends against a `BaseFeeDenominator == 0` value:
```go
if kc.BaseFeeDenominator == 0 {
    // To avoid panic, set the fluctuation range small
    baseFeeDenominator = new(big.Int).SetUint64(64)
} else {
    ...
}
``` [2](#0-1) 

However, no equivalent guard exists for `gasTarget := kc.GasTarget`, which is used unguarded as a divisor further down:
```go
gasUsedDelta := new(big.Int).SetUint64(parentGasUsed - gasTarget)
x := new(big.Int).Mul(parentBaseFee, gasUsedDelta)
y := x.Div(x, new(big.Int).SetUint64(gasTarget))
``` [3](#0-2) 
and the symmetric decrease branch:
```go
gasUsedDelta := new(big.Int).SetUint64(gasTarget - parentGasUsed)
x := new(big.Int).Mul(parentBaseFee, gasUsedDelta)
y := x.Div(x, new(big.Int).SetUint64(gasTarget))
``` [4](#0-3) 

The root cause is in the governance parameter registration for `Kip71GasTarget`, which sets `FormatChecker: noopFormatChecker` — always returning `true` — instead of a check like `v != 0`, unlike its sibling parameter `Kip71BaseFeeDenominator` which correctly enforces `ok && v != 0`:
```go
Kip71BaseFeeDenominator: {
    Canonicalizer: uint64Canonicalizer,
    FormatChecker: func(cv any) bool {
        v, ok := cv.(uint64)
        return ok && v != 0
    },
    ...
},
Kip71GasTarget: {
    Canonicalizer: uint64Canonicalizer,
    FormatChecker: noopFormatChecker,
    ...
},
``` [1](#0-0) 

`checkConsistency` in `kaiax/gov/headergov/impl/header.go`, which performs cross-field consistency checks for votes such as `Kip71LowerBoundBaseFee`/`Kip71UpperBoundBaseFee`, treats `Kip71GasTarget` (along with several others) as needing "no more checks here" beyond the format check performed in `NewVoteData()`:
```go
case gov.GovernanceDeriveShaImpl, gov.GovernanceGovParamContract, gov.GovernanceGovernanceMode, gov.GovernanceUnitPrice,
    gov.IstanbulCommitteeSize, gov.IstanbulEpoch, gov.IstanbulPolicy,
    gov.Kip71BaseFeeDenominator, gov.Kip71GasTarget, gov.Kip71MaxBlockGasUsedForBaseFee,
    ...
    return nil
``` [5](#0-4) 

Since the format check is a no-op for `GasTarget`, a vote of `kip71.gastarget = 0` passes `NewVoteData`, passes `VerifyVote`/`checkConsistency`, and becomes part of the effective `ParamSet`. That `ParamSet` is converted via `ToKip71Config()` [6](#0-5)  and consumed by `NextMagmaBlockBaseFee`, which is invoked from `blockchain/block_validator.go` during header validation of every subsequently produced block.

### Impact Explanation
Once a `GasTarget = 0` vote takes effect (via governance, reachable to any council member/proposer who can cast a vote, and to the governing node in single mode), every node that validates the next block whose parent has nonzero `GasUsed` will hit `big.Int.Div` with a zero divisor. This is a hard Go panic, not a recoverable error — it crashes the node process. Since `NextMagmaBlockBaseFee` is deterministic and used identically by every honest node during header verification, this results in a **network-wide simultaneous crash of all nodes**, i.e. denial-of-service against the entire chain, and a total halt of block production until code is patched — a Critical-severity consensus/availability defect.

### Likelihood Explanation
The parameter is a normal governance vote name (`kip71.gastarget`), submitted the same way as any other governance vote (via the `governance_vote` RPC and included in `header.Vote` as a proposer). No exploit of memory corruption or special privilege beyond normal governance participation is required — only a single malicious or careless vote with value `0` that reaches consensus. There is no existing safeguard anywhere in the vote pipeline (`NewVoteData` → `VerifyVote` → `checkConsistency`) that rejects a zero `GasTarget`, and the codebase explicitly acknowledges and defends against the exact same zero-divisor risk for the sibling parameter `BaseFeeDenominator`, demonstrating that this exact bug class (missing bound/zero check on a governance parameter used as a divisor) is a known but incompletely-applied concern.

### Recommendation
Add a `FormatChecker` for `Kip71GasTarget` in `kaiax/gov/param.go` analogous to `Kip71BaseFeeDenominator`'s, rejecting `v == 0` (and ideally enforcing `v <= MaxBlockGasUsedForBaseFee` for consistency). Additionally, add a defensive guard in `NextMagmaBlockBaseFee` (`params/kip71_config.go`) mirroring the existing `BaseFeeDenominator == 0` fallback, so that even out-of-band configuration (e.g., genesis file misconfiguration) cannot trigger the panic.

### Proof of Concept
1. As a validator/governing node, submit a governance vote: `governance_vote("kip71.gastarget", 0)`.
2. `NewVoteData` canonicalizes the value to `uint64(0)` and calls `Kip71GasTarget.FormatChecker` = `noopFormatChecker`, which returns `true` — the vote is accepted [7](#0-6) .
3. `checkConsistency` for `gov.Kip71GasTarget` falls into the no-op case and returns `nil` [8](#0-7) .
4. Once the vote is included and takes effect at the next epoch, `ParamSet.GasTarget` becomes `0`, propagated into `KIP71Config.GasTarget` via `ToKip71Config()`.
5. On the next block whose parent `GasUsed != 0`, `NextMagmaBlockBaseFee(parentHeaderNumber, parentHeaderBaseFee, parentHeaderGasUsed)` is called during header validation; since `parentGasUsed != gasTarget(0)`, it enters the `parentGasUsed > gasTarget` branch and executes `x.Div(x, new(big.Int).SetUint64(0))`, which panics [9](#0-8) .
6. Every node processing this block panics simultaneously, halting the chain.

### Citations

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

**File:** params/kip71_config.go (L88-102)
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
```

**File:** params/kip71_config.go (L118-120)
```go
		gasUsedDelta := new(big.Int).SetUint64(gasTarget - parentGasUsed)
		x := new(big.Int).Mul(parentBaseFee, gasUsedDelta)
		y := x.Div(x, new(big.Int).SetUint64(gasTarget))
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
