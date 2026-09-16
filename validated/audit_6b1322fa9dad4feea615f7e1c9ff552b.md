## Analog Vulnerability Found

### Title
Unrestricted `reward.mintingamount` governance parameter allows unauthorized draining of validator/fund balances via negative minting - ([File: kaiax/gov/param.go])

### Summary
This is an analog to the original Mars Protocol finding: a privileged-but-external actor (there, the contract "owner"/governance; here, the Kaia governing node casting a governance vote) can set an economic parameter with no bounds validation, and that unfair value is consumed downstream by code that assumes the value is well-formed, resulting in unintended value movement for third parties (validators, stakers, funds) with no additional consent.

### Finding Description
The `reward.mintingamount` governance parameter, defined in `kaiax/gov/param.go`, uses `bigIntCanonicalizer` and `noopFormatChecker`: [1](#0-0) 

`bigIntCanonicalizer` accepts any string/`[]byte` that parses as a `big.Int`, including negative values (e.g. `"-1000000000000000000"`), and `noopFormatChecker` performs no additional validation: [2](#0-1) 

This is inconsistent with sibling parameters in the same file such as `RewardMinimumStake` and `RewardStakingRewardThreshold`, which explicitly require `v.Sign() >= 0`: [3](#0-2) 

The consistency-check hook in header governance (`checkConsistency`) also does not add any bound for `RewardMintingAmount`; it falls into the default "no additional check" case for most reward/kip71 parameters: [4](#0-3) 

Once a negative `MintingAmount` is ratified, it becomes `config.MintingAmount` used by the reward computation in `kaiax/reward/impl/getter.go`, where it's split among proposer, stakers, KIF, KEF (and KPF): [5](#0-4) 

The resulting (now negative) per-recipient amounts are passed straight into `state.AddBalance` in `FinalizeState`, with no sign check: [6](#0-5) 

Adding a negative balance via `state.AddBalance` effectively subtracts KAIA from the proposer/staker/fund recipient addresses every block — an unauthorized value movement imposed on those accounts without their consent, purely as a side effect of an unvalidated governance parameter.

### Impact Explanation
A ratified negative `reward.mintingamount` would cause every subsequent block's `FinalizeState` to *decrease* the balances of the block proposer, staking reward recipients, and protocol funds (KIF/KEF/KPF) instead of minting rewards to them — a deterministic, chain-wide, recurring unauthorized value movement affecting all validators/stakers, not just a single account. Because this executes identically on every honest node (same governance parameter, same deterministic calculation), it does not cause consensus divergence by itself, but it does directly deplete validator/fund balances each block for as long as the malicious value remains ratified, which is a concrete asset-drain analogous to the original "drain user assets" impact.

### Likelihood Explanation
Like the original finding, likelihood is low because setting `reward.mintingamount` requires being the governing node (a privileged governance actor under `single` governance mode, verified in `checkConsistency`), not an arbitrary unprivileged transaction sender. This mirrors the original report's own acknowledgment that likelihood is low because the privileged role is expected to be trustworthy governance. However, the report explicitly treats this class of "privileged role can set unfair unbounded value with no code-level guardrail" as valid, and the same reasoning applies here: nothing in `Params[RewardMintingAmount]` prevents a compromised/malicious/mistaken governing node from ratifying a negative value.

### Recommendation
Add a `FormatChecker` for `RewardMintingAmount` (and any other economically-sensitive parameter still on `noopFormatChecker`, e.g. `Kip71GasTarget`, `Kip71LowerBoundBaseFee`, `Kip71MaxBlockGasUsedForBaseFee`) enforcing non-negativity, consistent with the pattern already used for `RewardMinimumStake` and `RewardStakingRewardThreshold`:
```go
FormatChecker: func(cv any) bool {
    v, ok := cv.(*big.Int)
    if !ok {
        return false
    }
    return v.Sign() >= 0
},
```

### Proof of Concept
1. As the governing node, submit a `governance_vote` for `reward.mintingamount` with value `"-9600000000000000000"` (negative of the default minting amount).
2. Once ratified at the epoch boundary and effective from the next epoch, `RewardConfig.MintingAmount` becomes negative.
3. In `getDeferredRewardFullKore`/`getDeferredRewardFullFlex`/`getDeferredRewardFullLegacy` (`kaiax/reward/impl/getter.go`), `minted := new(big.Int).Set(config.MintingAmount)` is negative, so `RewardRatio.Split(minted)` produces negative `proposer`, `kif`, `kef` (and `kpf`) values.
4. `FinalizeState` in `kaiax/reward/impl/blockstate.go` calls `state.AddBalance(addr, amount)` for each negative `amount`, subtracting KAIA from the proposer's, KIF's, KEF's (and KPF's) balances every block until the parameter is corrected.

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

**File:** kaiax/gov/param.go (L425-440)
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

**File:** kaiax/reward/impl/getter.go (L334-367)
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
```

**File:** kaiax/reward/impl/blockstate.go (L46-56)
```go
	spec, err := r.GetDeferredReward(header, txs, receipts)
	if err != nil {
		return err
	}
	if err := spec.Validate(); err != nil {
		return err
	}
	for addr, amount := range spec.Rewards {
		state.AddBalance(addr, amount)
	}
	return nil
```
