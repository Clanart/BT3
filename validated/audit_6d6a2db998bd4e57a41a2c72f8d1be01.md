### Title
Governance-controlled `reward.mintingamount` parameter has no upper bound, allowing the single governing node to mint unbounded KAIA every block - ([File: kaiax/gov/param.go])

### Summary
Kaia's native token supply is inflated every block by the `reward.mintingamount` governance parameter, which is documented as a bounded, hardfork-managed inflation schedule. However, the parameter's `FormatChecker` is `noopFormatChecker` (always returns `true`) and its `checkConsistency` case performs no bound check at all, so the single governing node (`governance.governancemode == "single"`) can vote to set `reward.mintingamount` to any arbitrarily large `*big.Int` value. Once ratified at the epoch boundary, this new minting amount is minted into the proposer/staker/fund reward split every single block indefinitely, permitting effectively unbounded, centralization-driven token inflation analogous to the "owner can mint arbitrary tokens" pattern from the MozToken report.

### Finding Description
The governance parameter `reward.mintingamount` (`gov.RewardMintingAmount`) is defined with: [1](#0-0) 

Its `Canonicalizer` (`bigIntCanonicalizer`) merely converts the vote value into a `*big.Int` and its `FormatChecker` is the permissive `noopFormatChecker`: [2](#0-1) 

Unlike `RewardMinimumStake`, which at least checks `v.Sign() >= 0`, `RewardMintingAmount` has no sign check and no upper bound check whatsoever.

When a vote for this parameter is verified in `checkConsistency`, it falls into the catch-all branch that unconditionally returns `nil` (i.e., no additional validation beyond the no-op format check): [3](#0-2) 

Votes are only restricted to the governing node in `single` mode (checked in `VerifyVote`/`Vote` API), but that node is a single, centrally-controlled key, and there is no cap enforced on the value it can set: [4](#0-3) 

Once ratified at an epoch boundary, the new `MintingAmount` is used directly by the reward module every block to compute the minted amount distributed to the proposer, stakers, and funds: [5](#0-4) [6](#0-5) 

This mirrors the MozToken issue: although the tokenomics are presented as having a controlled minting schedule (fixed via `reward.mintingamount` genesis configuration, akin to MozToken's "fixed supply"), a single privileged entity (the governing node, analogous to the `onlyOwner`/`onlyStakingContract` pattern) can unilaterally set this value to an arbitrarily large number with no protocol-level cap, thereby minting an arbitrary amount of native tokens on every subsequent block.

### Impact Explanation
Unbounded control of `reward.mintingamount` by a single governing node allows arbitrary, sustained token supply inflation. Since this amount is minted and distributed every block via `FinalizeState`/`getDeferredReward*`, a malicious or compromised governing node could set an extreme minting amount, causing the total circulating supply of KAIA to inflate dramatically block after block, diluting all other holders and effectively enabling large-scale unauthorized value creation/redirection to whichever reward recipients (proposer/stakers/funds) the ratio parameters direct it to. This is a supply-inflation / centralization risk consistent with High severity given the direct, unauthorized, and unbounded value creation impact on the native asset.

### Likelihood Explanation
Likelihood is bounded by the fact that only the current governing node (a privileged actor) can cast this vote, and it must be ratified at an epoch boundary — no code path allows an arbitrary unprivileged transaction sender to trigger this directly. However, this exactly matches the "owner"-controlled but reachable-by-privileged-role pattern flagged in the MozToken analog, where the mitigation for one bug (removing `mint()`) doesn't eliminate the deeper problem that a privileged single key can still achieve unbounded minting through a different, still-authorized configuration path (`setStakingContract` there, `governance_vote` here). No additional multisig or on-chain cap exists to prevent the governing node from setting an extreme value.

### Recommendation
Add a meaningful `FormatChecker` for `RewardMintingAmount` that enforces a sane, protocol-defined upper bound (and non-negativity) on the value, similar to how `RewardRatio`/`RewardKip82Ratio` enforce sum-to-100 percentage constraints. Additionally, consider requiring multi-party ratification (rather than single-governing-node approval) for changes to inflation-controlling parameters, or introducing a maximum percentage change per epoch to prevent sudden large jumps in minting amount.

### Proof of Concept
1. Governing node calls `governance_vote("reward.mintingamount", <huge_value>)`, e.g. `"999999999999999999999999999999"`, which passes `NewVoteData` because `FormatChecker` is `noopFormatChecker`: [7](#0-6) 
2. The governing node becomes proposer and writes this vote into `header.Vote`; `VerifyVote` accepts it since `vote.Voter() == params.GoverningNode` and `checkConsistency` for `gov.RewardMintingAmount` returns `nil` unconditionally.
3. At the next epoch boundary, this vote is ratified into `header.Governance`, becoming the effective `reward.mintingamount` for subsequent blocks.
4. From that point on, every block's `RewardConfig.MintingAmount` (via `NewRewardConfig`) picks up the huge value, and `getRewardSummary`/`getDeferredReward*` mint and distribute that amount every block, inflating the total supply without limit.

### Citations

**File:** kaiax/gov/param.go (L160-162)
```go
func noopFormatChecker(cv any) bool {
	return true
}
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

**File:** kaiax/gov/headergov/impl/header.go (L100-107)
```go

	// In single mode, only the governing node can write header.Vote after Permissionless.
	params := h.GetParamSet(blockNum)
	if h.ChainConfig.IsPermissionlessForkEnabled(new(big.Int).SetUint64(blockNum)) &&
		params.GovernanceMode == "single" &&
		vote.Voter() != params.GoverningNode {
		return ErrVotePermissionDenied
	}
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

**File:** kaiax/reward/impl/getter.go (L86-87)
```go
func getRewardSummary(config *reward.RewardConfig, execFee, blobFee *big.Int) *reward.RewardSummary {
	minted := new(big.Int).Set(config.MintingAmount)
```

**File:** kaiax/reward/config.go (L59-63)
```go
	paramset := govModule.GetParamSet(header.Number.Uint64())
	rc.IsSimple = paramset.ProposerPolicy != uint64(istanbul.WeightedRandom)
	rc.UnitPrice = new(big.Int).SetUint64(paramset.UnitPrice)
	rc.MintingAmount = new(big.Int).Set(paramset.MintingAmount)
	rc.MinimumStake = new(big.Int).Set(paramset.MinimumStake)
```

**File:** kaiax/gov/headergov/vote.go (L29-55)
```go
func NewVoteData(voter common.Address, name string, value any) VoteData {
	param, ok := gov.Params[gov.ParamName(name)]
	if !ok {
		param, ok = gov.ValidatorParams[gov.ParamName(name)]
		if !ok {
			logger.Error("Invalid vote name", "name", name)
			return nil
		}
	}

	cv, err := param.Canonicalizer(value)
	if err != nil {
		logger.Error("Canonicalize error", "name", name, "value", value, "err", err)
		return nil
	}

	if !param.FormatChecker(cv) {
		logger.Error("Format check error", "name", name, "value", value)
		return nil
	}

	return &voteData{
		voter: voter,
		name:  gov.ParamName(name),
		value: cv,
	}
}
```
