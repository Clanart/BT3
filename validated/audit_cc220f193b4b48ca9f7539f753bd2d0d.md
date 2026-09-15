### Title
Missing sign validation on `reward.mintingamount` governance parameter enables negative minting via `noopFormatChecker` - ([File: kaiax/gov/param.go])

### Summary
`RewardMintingAmount`'s `FormatChecker` is `noopFormatChecker`, which accepts any value, unlike the sibling parameter `RewardMinimumStake` which explicitly requires `v.Sign() >= 0`. Since `bigIntCanonicalizer` happily parses negative decimal strings via `big.Int.SetString`, a governance vote can set `reward.mintingamount` to a negative `*big.Int`. This value flows unchecked into `RewardConfig.MintingAmount` and is used directly as `minted` in block-reward computation.

### Finding Description
`bigIntCanonicalizer` in `kaiax/gov/param.go` accepts any parseable integer string, including negative numbers: [1](#0-0) 

The `RewardMintingAmount` parameter definition uses `noopFormatChecker` (always returns `true`), while the adjacent `RewardMinimumStake` explicitly enforces non-negativity: [2](#0-1) 

A validated vote is admitted through `PartialParamSet.Add`, which only runs `Canonicalizer` then `FormatChecker`: [3](#0-2) 

The resulting parameter set is consumed by `NewRewardConfig`, which copies the value straight into `RewardConfig.MintingAmount` with no additional bound checking: [4](#0-3) 

That value is then used as `minted` in every reward-distribution path, e.g. `getDeferredRewardSimple`, `getDeferredRewardFullKore`, and `getDeferredRewardFullLegacy`, where it is added to/subtracted from proposer, staker, and fund balances: [5](#0-4) [6](#0-5) 

The KIP-71-analogous defensive coding pattern (validating that a numeric governance/config value cannot exceed or go below its representable/sane bounds, as flagged in the Taurus report for `setOffsetPercentage()`) is inconsistently applied here: `RewardMinimumStake` has the guard, `RewardMintingAmount` does not.

### Impact Explanation
If `reward.mintingamount` is voted to a negative value, minting turns into burning: `RewardSpec.Minted` becomes negative and is propagated into `proposer`/`stakers`/fund allocations via `Add`/`Sub` operations on `*big.Int`, which do not clamp at zero (unlike EVM `uint256` arithmetic, Go's `big.Int` supports negative numbers natively, so there's no "overflow" trap — the value silently goes negative). This can produce negative balances being credited to state accounts or negative amounts being distributed to stakers/funds, corrupting the block reward accounting, and depending on downstream state-transition handling of negative balances, could allow supply deflation/inflation inconsistent with consensus rules or a chain-halting panic when `RewardSpec.Validate()` is invoked (it errors on negative reward amounts, `errNegativeRewardAmount`), causing nodes to diverge on whether the resulting block is valid. [7](#0-6) 

### Likelihood Explanation
Setting `reward.mintingamount` requires a governance vote, which in this codebase is gated to holders of voting rights (`ErrVotePermissionDenied` guards non-authorized voters and `checkConsistency` performs additional semantic checks for several other parameters). `reward.mintingamount` is not covered by any `checkConsistency` case beyond the missing `FormatChecker`, meaning a validator/governing-node with vote rights (a "governance parameter" caller per the reachable-surface rules) can push through a negative value with no additional consensus-level rejection, unlike `kip71.lowerboundbasefee`/`kip71.upperboundbasefee` which have dedicated cross-checks in `checkConsistency`. [8](#0-7) 

### Recommendation
Add a `FormatChecker` for `RewardMintingAmount` (mirroring `RewardMinimumStake`) that rejects negative values, e.g. `func(cv any) bool { v, ok := cv.(*big.Int); return ok && v.Sign() >= 0 }`. Additionally consider adding an upper sanity bound and asserting `RewardSpec.Validate()` (or equivalent) is invoked on every reward computation path before it's applied to state, not only in tests.

### Proof of Concept
1. A council member/governing node with vote rights submits a header-governance vote (or GovParam contract vote per KIP-81) for `reward.mintingamount` with value `"-1000000000000000000"`.
2. `bigIntCanonicalizer` successfully parses this as `*big.Int(-1e18)` since `big.Int.SetString` accepts the leading `-`. [1](#0-0) 
3. `PartialParamSet.Add` calls `RewardMintingAmount.FormatChecker` = `noopFormatChecker`, which returns `true` unconditionally, so the negative value is accepted into the parameter set. [3](#0-2) 
4. At the next epoch, `NewRewardConfig` copies this negative value into `RewardConfig.MintingAmount`. [9](#0-8) 
5. During block finalization, `getDeferredRewardSimple`/`getDeferredRewardFullKore` use this negative `minted` to compute `proposer`, `stakers`, `kif`, `kef` amounts, propagating negative values into `RewardSpec.Rewards`, corrupting reward distribution and potentially causing block-validity disagreement between nodes that do/do not additionally validate reward specs before applying them to state.

**Note on scope/uncertainty**: This is an analog of the *class* of bug reported (insufficient bound validation on a governance-controlled numeric parameter leading to invalid arithmetic outcomes), not a literal integer-overflow, since Kaia is written in Go and uses `big.Int`/fixed-width `uint64` types rather than Solidity's `uint256`, so a true "value exceeds the type's max" overflow as described in the Taurus report does not directly translate. I was not able to fully trace whether `RewardSpec.Validate()` is called on every code path that applies a computed `RewardSpec` to `StateDB` balances (e.g., via `AddBalance`/`SubBalance` in `blockchain/state/statedb.go`) before committing state, so the exact blast radius (panic/consensus-halt vs. silent balance corruption) could not be confirmed with certainty from the available index.

### Citations

**File:** kaiax/gov/param.go (L94-112)
```go
	bigIntCanonicalizer canonicalizerT = func(v any) (any, error) {
		switch v := v.(type) {
		case []byte:
			cv, ok := new(big.Int).SetString(string(v), 10)
			if !ok {
				return nil, ErrCanonicalizeByteToBigInt
			}
			return cv, nil
		case string:
			cv, ok := new(big.Int).SetString(v, 10)
			if !ok {
				return nil, ErrCanonicalizeStringToBigInt
			}
			return cv, nil
		case *big.Int:
			return v, nil
		}
		return nil, ErrCanonicalizeBigInt
	}
```

**File:** kaiax/gov/param.go (L414-441)
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

**File:** kaiax/reward/config.go (L53-66)
```go
func NewRewardConfig(chainConfig *params.ChainConfig, govModule GovModule, header *types.Header) (*RewardConfig, error) {
	rc := &RewardConfig{}

	rc.Rules = chainConfig.Rules(header.Number)
	rc.Rewardbase = header.Rewardbase

	paramset := govModule.GetParamSet(header.Number.Uint64())
	rc.IsSimple = paramset.ProposerPolicy != uint64(istanbul.WeightedRandom)
	rc.UnitPrice = new(big.Int).SetUint64(paramset.UnitPrice)
	rc.MintingAmount = new(big.Int).Set(paramset.MintingAmount)
	rc.MinimumStake = new(big.Int).Set(paramset.MinimumStake)
	rc.DeferredTxFee = paramset.DeferredTxFee
	rc.StakingRewardThreshold = new(big.Int).Set(paramset.StakingRewardThreshold)
	rc.UseFlexReward = paramset.UseFlexReward
```

**File:** kaiax/reward/impl/getter.go (L224-266)
```go
// getDeferredRewardSimple is for Simple policy.
func getDeferredRewardSimple(config *reward.RewardConfig, execFee, blobFee *big.Int) (*reward.RewardSpec, error) {
	spec := reward.NewRewardSpec()
	minted := new(big.Int).Set(config.MintingAmount)

	// Non-deferred mode
	if !config.DeferredTxFee {
		var proposer *big.Int
		if config.Rules.IsMagma {
			// In non-deferred mode, no fees to distribute here at the end of block processing.
			// Just distribute the minting reward to the proposer and stop.
			proposer = new(big.Int).Set(minted)
			execFee = big.NewInt(0)
		} else {
			// But Simple policy had a bug where transaction fees were distributed to the proposer here at the end of block processing
			// despite configured to non-deferred mode. To keep the backward compatibility, the buggy behavior retains until Magma.
			proposer = new(big.Int).Add(minted, execFee)
		}
		spec.Minted = new(big.Int).Set(minted)
		// Both exec fees and blob fees are burned during state transition in non-deferred mode,
		// not at finalization. TotalFee/BurntFee are completed by specWithNonDeferredFee (GetBlockReward only).
		spec.TotalFee = execFee // Note that we've set it to 0 for non-deferred + Magma (see above).
		spec.BurntFee = big.NewInt(0)
		spec.Proposer = proposer
		spec.IncRecipient(config.Rewardbase, proposer)
		return spec, nil
	}

	// Deferred mode
	burntFee := big.NewInt(0)
	if config.Rules.IsMagma {
		burntFee = getBurnAmountMagma(execFee)
	}
	proposer := new(big.Int).Add(minted, execFee)
	proposer.Sub(proposer, burntFee)

	spec.Minted = minted
	spec.TotalFee = new(big.Int).Add(execFee, blobFee)
	spec.BurntFee = new(big.Int).Add(burntFee, blobFee)
	spec.Proposer = proposer
	spec.IncRecipient(config.Rewardbase, proposer)
	return spec, nil
}
```

**File:** kaiax/reward/impl/getter.go (L334-390)
```go
// getDeferredRewardFullKore is for non-Simple policy and after Kore.
func getDeferredRewardFullKore(config *reward.RewardConfig, execFee, burntFee, blobFee *big.Int, si *staking.StakingInfo) (*reward.RewardSpec, error) {
	var (
		spec             = reward.NewRewardSpec()
		minted           = new(big.Int).Set(config.MintingAmount)
		distributableFee = new(big.Int).Sub(execFee, burntFee)
	)

	// Distribute using RewardRatio first. Unlike Legacy, fees are not distributed here
	// because fees are exclusively allocated to proposer. By the way, remainder goes to KIF.
	validators, kif, kef := config.RewardRatio.Split(minted)
	proposer, stakers := config.Kip82Ratio.Split(validators)
	ratioRemainder := calcRemainder(minted, proposer, stakers, kif, kef)
	kif.Add(kif, ratioRemainder)

	// Further distribute using Kip82Ratio. By the way, remainder goes to proposer.
	// After Prague, if the CLStaking is not nil, the proposer and staking rewards are proportionally distributed to both CN and CL.
	// For proposer rewards, see `specWithProposerAndFunds`.
	stakersAlloc, kip82Remainder := assignStakingRewards(config, stakers, si)
	proposer.Add(proposer, kip82Remainder)
	stakers.Sub(stakers, kip82Remainder)

	// Proposer gets the fees.
	proposer.Add(proposer, distributableFee)

	spec.Minted = minted
	spec.TotalFee = new(big.Int).Add(execFee, blobFee)
	spec.BurntFee = new(big.Int).Add(burntFee, blobFee)
	spec.Stakers = stakers
	for addr, amount := range stakersAlloc {
		spec.IncRecipient(addr, amount)
	}
	spec = specWithProposerAndFunds(spec, config, proposer, kif, kef, si)
	return spec, nil
}

// getDeferredRewardFullLegacy is for non-Simple policy and before Kore.
func getDeferredRewardFullLegacy(config *reward.RewardConfig, execFee, burntFee *big.Int, si *staking.StakingInfo) (*reward.RewardSpec, error) {
	var (
		spec             = reward.NewRewardSpec()
		minted           = new(big.Int).Set(config.MintingAmount)
		distributableFee = new(big.Int).Sub(execFee, burntFee)
		totalReward      = new(big.Int).Add(minted, distributableFee)
	)

	// Distribute using RewardRatio. Remainder goes to KIF.
	proposer, kif, kef := config.RewardRatio.Split(totalReward)
	ratioRemainder := calcRemainder(totalReward, proposer, kif, kef)
	kif.Add(kif, ratioRemainder)

	spec.Minted = minted
	spec.TotalFee = execFee
	spec.BurntFee = burntFee
	spec.Stakers = common.Big0 // No stakers reward before Kore
	spec = specWithProposerAndFunds(spec, config, proposer, kif, kef, si)
	return spec, nil
}
```

**File:** kaiax/reward/spec.go (L118-125)
```go
func (spec *RewardSpec) Validate() error {
	for addr, amount := range spec.Rewards {
		if amount.Sign() < 0 {
			return errNegativeRewardAmount(addr, amount)
		}
	}
	return nil
}
```

**File:** kaiax/gov/headergov/impl/header.go (L156-224)
```go
// checkConsistency checks if vote values are consistent with chain states such as other parameters and validator set.
func (h *headerGovModule) checkConsistency(blockNum uint64, vote headergov.VoteData) error {
	switch vote.Name() {
	case gov.GovernanceGoverningNode:
		params := h.GetParamSet(blockNum)

		// compare with governing node only in single mode.
		if params.GovernanceMode != "single" {
			return nil
		}

		council, err := h.ValSet.GetCouncil(blockNum)
		if err != nil {
			return err
		}

		if !slices.Contains(council, params.GoverningNode) {
			return ErrGovNodeNotInValSetList
		}
		if !h.ChainConfig.IsPermissionlessForkEnabled(new(big.Int).SetUint64(blockNum)) {
			return nil
		}

		// After Permissionless only the governing node may vote, so a successor outside the council could never vote again.
		newNode, ok := vote.Value().(common.Address)
		if !ok || common.EmptyAddress(newNode) {
			return ErrInvalidKeyValue
		}
		if !slices.Contains(council, newNode) {
			return ErrGovNodeNotInValSetList
		}
		return nil
	case gov.Kip71LowerBoundBaseFee:
		params := h.GetParamSet(blockNum)
		if vote.Value().(uint64) > params.UpperBoundBaseFee {
			return ErrLowerBoundBaseFee
		} else {
			return nil
		}
	case gov.Kip71UpperBoundBaseFee:
		params := h.GetParamSet(blockNum)
		if vote.Value().(uint64) < params.LowerBoundBaseFee {
			return ErrUpperBoundBaseFee
		} else {
			return nil
		}
	case gov.AddValidator, gov.RemoveValidator:
		params := h.GetParamSet(blockNum)

		// compare with governing node only in single mode.
		if params.GovernanceMode != "single" {
			return nil
		}
		if slices.Contains(vote.Value().([]common.Address), params.GoverningNode) {
			return ErrGovNodeInValSetVoteValue
		}
		return nil
		// These votes are valid as long as it passes the format checks in NewVoteData(). No more checks here.
	case gov.GovernanceDeriveShaImpl, gov.GovernanceGovParamContract, gov.GovernanceGovernanceMode, gov.GovernanceUnitPrice,
		gov.IstanbulCommitteeSize, gov.IstanbulEpoch, gov.IstanbulPolicy,
		gov.Kip71BaseFeeDenominator, gov.Kip71GasTarget, gov.Kip71MaxBlockGasUsedForBaseFee,
		gov.RewardDeferredTxFee, gov.RewardKip82Ratio, gov.RewardMintingAmount, gov.RewardMinimumStake,
		gov.RewardProposerUpdateInterval, gov.RewardRatio, gov.RewardStakingRewardThreshold,
		gov.RewardStakingUpdateInterval, gov.RewardUseFlexReward, gov.RewardUseGiniCoeff:
		return nil
	default:
		return ErrInvalidKeyValue
	}
}
```
