### Title
Unbounded `reward.mintingamount` governance parameter allows arbitrary native token supply inflation - (File: `kaiax/gov/param.go`)

### Summary
The root cause of the Perennial finding is that a privileged party (the market Coordinator) can set an economically-sensitive parameter (`scale`/`makerLimit`) with no meaningful upper/lower bound validation, and that parameter is then fed directly into a value-distribution formula, letting the privileged party extract value from other participants. Kaia's governance module has the same structural weakness for the `reward.mintingamount` parameter: the governing party can set it to any value with no bound check, and it is fed directly into the block reward minting formula, allowing unconstrained inflation of the native token supply.

### Finding Description
`RewardMintingAmount` is registered in the governance parameter table with `FormatChecker: noopFormatChecker`, i.e. no validation is performed on the submitted value at all: [1](#0-0) 

Compare this to a neighboring parameter, `RewardMinimumStake`, which at least enforces non-negativity: [2](#0-1) 

and to `Kip71BaseFeeDenominator`, which explicitly guards against a degenerate (zero) value: [3](#0-2) 

No such guard rail exists for `RewardMintingAmount` — it can be set to `big.Int` of arbitrary (including astronomically large) magnitude. The vote consistency checker in `headergov` also does not add any cross-check for this parameter; it is simply passed through as "format-checked only, no additional consistency check": [4](#0-3) 

The value is consumed directly by the reward module which mints and distributes this amount as new native token supply every block: [5](#0-4) 

This mirrors the Perennial bug precisely: a governance-controlled economic parameter (`scale`/`makerLimit` there, `mintingamount` here) that feeds directly into a per-block/per-settlement value formula, with validation limited to unrelated or insufficient bounds (only `scaleLimit` derived from `makerLimit` there; nothing at all here), enabling the privileged party to extract disproportionate value (fee/bad-debt transfer there, direct token minting here).

### Impact Explanation
If the governing node (in `single` governance mode) or a controlling voting majority (in multi-mode) submits a `reward.mintingamount` vote with an unbounded value, every subsequent block will mint that amount of native KAIA and distribute it per the `reward.ratio` split to validators/funds, directly inflating total supply without limit. This is a direct "supply inflation" outcome, one of the explicitly recognized abuse categories, and economically equivalent to unauthorized value creation for the party(ies) controlling governance.

### Likelihood Explanation
Exploitation requires control of the governance voting mechanism (the governing node in single mode, analogous to the "Coordinator" role in the original report who is explicitly a semi-trusted-but-bounded actor). Given that the protocol's own parameter framework enforces bounds/format checks on several other sensitive parameters (`MinimumStake`, `BaseFeeDenominator`, `Kip71LowerBoundBaseFee`/`UpperBoundBaseFee` cross-checks), the complete absence of any check on `RewardMintingAmount` is very likely an oversight rather than an intentional design choice, making this a realistic and directly reachable governance abuse path.

### Recommendation
Add a `FormatChecker` for `RewardMintingAmount` that enforces a sane upper bound (e.g., a protocol-defined maximum minting rate per block, or a percentage cap relative to current supply/previous value), similar to how `Kip71LowerBoundBaseFee`/`UpperBoundBaseFee` are cross-validated against each other in `checkConsistency`. Consider requiring minting-amount changes to be gradual (delta-limited) rather than allowing an instantaneous jump to an arbitrary value.

### Proof of Concept
1. Governing node casts a header-governance vote for `reward.mintingamount` with an extremely large `big.Int` value (e.g. `"999999999999999999999999999999"`).
2. `PartialParamSet.Add` / `ParamSet.Set` accept it unconditionally because `Kip71`/`RewardMintingAmount`'s `FormatChecker` is `noopFormatChecker`: [6](#0-5) 
3. `checkConsistency` in `headergov/impl/header.go` performs no additional validation for `gov.RewardMintingAmount` (line 217 above), so the vote is accepted into the canonical parameter history.
4. From the block after the vote takes effect, the reward module mints and distributes this inflated amount every block indefinitely, as described in `kaiax/reward/README.md` reward-source formula.

Note: I was unable to fully trace the exact governance voter eligibility gating code (`GovernanceMode`/council-membership enforcement) within the available index in this session; this should be confirmed to establish exactly which privileged role(s) can submit this vote in a given network configuration.

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

**File:** kaiax/gov/param.go (L414-424)
```go
	RewardMintingAmount: {
		Canonicalizer: bigIntCanonicalizer,
		FormatChecker: noopFormatChecker,
		ChainConfigValue: func(c *params.ChainConfig) (any, error) {
			if c.Governance == nil || c.Governance.Reward == nil || c.Governance.Reward.MintingAmount == nil {
				return nil, errors.New("reward is not set")
			}
			return c.Governance.Reward.MintingAmount, nil
		},
		DefaultValue: big.NewInt(0),
	},
```

**File:** kaiax/gov/param.go (L425-441)
```go
	RewardMinimumStake: {
		Canonicalizer: bigIntCanonicalizer,
		FormatChecker: func(cv any) bool {
			v, ok := cv.(*big.Int)
			if !ok {
				return false
			}
			return v.Sign() >= 0
		},
		ChainConfigValue: func(c *params.ChainConfig) (any, error) {
			if c.Governance == nil || c.Governance.Reward == nil || c.Governance.Reward.MinimumStake == nil {
				return nil, errors.New("reward is not set")
			}
			return c.Governance.Reward.MinimumStake, nil
		},
		DefaultValue: big.NewInt(2000000),
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

**File:** kaiax/reward/README.md (L18-20)
```markdown
### Reward source

- **Minting amount (M)**: The amount of token minted every block. Also known as the inflation. This amount is determined by the `reward.mintingamount` parameter in the genesis configuration and can be modified by governance.
```

**File:** kaiax/gov/paramset.go (L209-226)
```go
func (p PartialParamSet) Add(name string, value any) error {
	param, ok := Params[ParamName(name)]
	if !ok {
		return ErrInvalidParamName
	}

	cv, err := param.Canonicalizer(value)
	if err != nil {
		return err
	}

	if !param.FormatChecker(cv) {
		return ErrInvalidParamValue
	}

	p[ParamName(name)] = cv
	return nil
}
```
