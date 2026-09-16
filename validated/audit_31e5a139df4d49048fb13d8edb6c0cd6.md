I found a solid analog: an unvalidated governance parameter (`kip71.gastarget`) that, when set to `0`, causes a division-by-zero panic in the base fee calculation formula — directly analogous to the report's "unvalidated numeric parameter that breaks core contract logic" bug class.

### Title
Missing zero-validation on `kip71.gastarget` governance parameter causes division-by-zero panic in `NextMagmaBlockBaseFee` - (File: `params/kip71_config.go`)

### Summary
The `Kip71GasTarget` governance parameter is registered with a `noopFormatChecker` [1](#0-0) , meaning any `uint64` value, including `0`, is accepted as valid. This is inconsistent with the sibling parameter `Kip71BaseFeeDenominator`, whose `FormatChecker` explicitly rejects `0` [2](#0-1) . `GasTarget` is used unguarded as a divisor in `KIP71Config.NextMagmaBlockBaseFee`, so a value of `0` triggers a `big.Int` division-by-zero panic.

### Finding Description
`NextMagmaBlockBaseFee` computes the next block's base fee using `gasTarget` (taken directly from the governance parameter) as a divisor without any zero-guard, unlike `baseFeeDenominator`, which is explicitly defended against zero: [3](#0-2) 

Then further down, `gasTarget` is used as a raw divisor: [4](#0-3) [5](#0-4) 

If `parentGasUsed != gasTarget` (the common case) and `gasTarget == 0`, `x.Div(x, new(big.Int).SetUint64(0))` panics, since Go's `math/big` `Div` panics on division by zero.

The governance-vote consistency checker (`checkConsistency`) does not validate `Kip71GasTarget` beyond the no-op format check — it falls into the default "no additional checks" bucket alongside other already-format-validated parameters: [6](#0-5) 

So a vote setting `kip71.gastarget = 0` is accepted at every validation layer (format check, vote consistency check) and becomes part of the effective `ParamSet` once ratified, from which it flows into `KIP71Config.GasTarget` via `ToKip71Config()` [7](#0-6)  and is used by every node when validating/building Magma-fork blocks via `VerifyMagmaHeader`/`NextMagmaBlockBaseFee` [8](#0-7) .

### Impact Explanation
Once `GasTarget = 0` becomes the effective governance parameter, every node (validators and non-validators alike) that computes the expected base fee for the next block — during block validation and block assembly — will panic and crash. Since `NextMagmaBlockBaseFee` is on the consensus-critical header-verification path, this is not a localized failure but a uniform crash across the entire network, resulting in a full chain halt (loss of liveness). This is analogous to the original report's concern that unvalidated immutable/governance parameters can "disfunction" a critical contract, except the reachable impact in this codebase is more severe — a network-wide DoS rather than a reverting function call.

### Likelihood Explanation
The `kip71.gastarget` parameter is explicitly listed as a mutable governance parameter changeable via on-chain voting [9](#0-8) . Any governance vote proposing `0` for this parameter passes the format checker (`noopFormatChecker`) and the consistency checker (default no-op branch), so it requires no code change or exploit sophistication beyond a single malformed/malicious governance vote reaching quorum — the same class of "no validation on a critical numeric governance input" flagged in the source report.

### Recommendation
Add a non-zero `FormatChecker` for `Kip71GasTarget`, mirroring the existing check for `Kip71BaseFeeDenominator`:
```go
Kip71GasTarget: {
    Canonicalizer: uint64Canonicalizer,
    FormatChecker: func(cv any) bool {
        v, ok := cv.(uint64)
        return ok && v != 0
    },
    ...
},
```
Additionally, as defense-in-depth (consistent with how `BaseFeeDenominator == 0` is already defensively handled), add a zero-guard fallback for `gasTarget` inside `NextMagmaBlockBaseFee` in `params/kip71_config.go`.

### Proof of Concept
1. A governance vote sets `kip71.gastarget` to `0`. This vote passes `Params[Kip71GasTarget].FormatChecker` (no-op) [1](#0-0)  and `checkConsistency` (falls into the default pass-through case) [6](#0-5) .
2. Once the vote is enacted at the next epoch, `GetParamSet` returns `GasTarget: 0`, which is converted into `KIP71Config.GasTarget = 0` via `ToKip71Config()`.
3. On the next block after the change, if `parentGasUsed != 0`, `NextMagmaBlockBaseFee` takes the `parentGasUsed > gasTarget` branch and executes `x.Div(x, new(big.Int).SetUint64(0))` [4](#0-3) , which panics with "division by zero".
4. Every node that calls `VerifyMagmaHeader`/`NextMagmaBlockBaseFee` while validating or building that block crashes simultaneously, halting the chain.

### Citations

**File:** kaiax/gov/param.go (L310-315)
```go
	Kip71BaseFeeDenominator: {
		Canonicalizer: uint64Canonicalizer,
		FormatChecker: func(cv any) bool {
			v, ok := cv.(uint64)
			return ok && v != 0
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

**File:** params/kip71_config.go (L45-56)
```go
func (kc *KIP71Config) VerifyMagmaHeader(headerBaseFee *big.Int, parentHeaderNumber *big.Int, parentHeaderBaseFee *big.Int, parentHeaderGasUsed uint64) error {
	if headerBaseFee == nil {
		return fmt.Errorf("header is missing baseFee")
	}
	// Verify the baseFee is correct based on the parent header.
	expectedBaseFee := kc.NextMagmaBlockBaseFee(parentHeaderNumber, parentHeaderBaseFee, parentHeaderGasUsed)
	if headerBaseFee.Cmp(expectedBaseFee) != 0 {
		return fmt.Errorf("invalid baseFee: have %s, want %s, parentBaseFee %s, parentGasUsed %d",
			headerBaseFee, expectedBaseFee, parentHeaderBaseFee, parentHeaderGasUsed)
	}
	return nil
}
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

**File:** params/kip71_config.go (L98-103)
```go
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

**File:** kaiax/gov/README.md (L17-33)
```markdown
```
<mutable parameters>
governance.deriveshaimpl
governance.governingnode
governance.govparamcontract
governance.unitprice
istanbul.committeesize
kip71.basefeedenominator
kip71.gastarget
kip71.lowerboundbasefee
kip71.maxblockgasusedforbasefee
kip71.upperboundbasefee
reward.kip82ratio
reward.mintingamount
reward.ratio
reward.stakingrewardthreshold
reward.useflexreward
```
