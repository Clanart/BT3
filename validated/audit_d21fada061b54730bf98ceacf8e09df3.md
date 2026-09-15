Confirmed: `RewardMintingAmount` has zero bound validation, and the value flows directly into `state.AddBalance` via `FinalizeState`, giving a clean, provable supply-inflation analog. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) 

### Title
Governance `reward.mintingamount` accepted with no bound/sign check enables unlimited supply inflation - (File: kaiax/gov/param.go)

### Summary
The `RewardMintingAmount` governance parameter definition uses `noopFormatChecker`, which unconditionally returns `true` for any canonicalized value, and its canonicalizer (`bigIntCanonicalizer`) accepts any `*big.Int`, including zero, arbitrarily large, or negative values. Unlike sibling parameters such as `RewardMinimumStake` (which enforces `v.Sign() >= 0`) or `RewardRatio` (which enforces the parts sum to 100), `reward.mintingamount` has no invariant at all. This is a direct analog of the reported bug class: critical economic parameters re-parameterized via governance voting without sanity/threshold checks, allowing a party with voting power to set an economically or structurally invalid value that is applied automatically once ratified.

### Finding Description
`RewardMintingAmount` is declared with `FormatChecker: noopFormatChecker`, which always returns `true`, so any big-integer value canonicalized from a vote (string, bytes, or `*big.Int`) is accepted as valid. [1](#0-0) 

`bigIntCanonicalizer` performs no bound or sign checking either — it simply parses the input into a `*big.Int` and returns it as-is. [2](#0-1) 

`checkConsistency` in header governance's vote verification also does not add any special-case validation for `RewardMintingAmount`; it falls into the generic bucket of parameters that pass with `return nil` as long as the format checker (which is a no-op) succeeds. [5](#0-4) 

Once ratified (by the governing node in `single` mode, or by GC majority in `none` mode, at epoch boundaries), the new `MintingAmount` becomes part of `RewardConfig` and is used directly as the block-level minting source `M` in every subsequent reward calculation, e.g. `minted := new(big.Int).Set(config.MintingAmount)`. [3](#0-2) 

The minted amount is then credited to the proposer/staker/fund recipients via `state.AddBalance` in `FinalizeState`, with no upper bound check against the configured value. [4](#0-3) 

Consequently, a governance participant with sufficient voting power (the governing node in single mode, or GC majority in none mode) can vote `reward.mintingamount` up to an arbitrarily large value (e.g., `10^30`), and once ratified, every subsequent block will mint and distribute that amount as new native token supply with no cap enforced anywhere in the parameter-validation, header-verification, or reward-distribution pipeline.

### Impact Explanation
This directly causes unauthorized native token supply inflation — every block after ratification mints the attacker-chosen amount and distributes it to the proposer/staker/fund addresses, diluting all other token holders and directly enriching whoever controls the minting/reward recipients. This matches the reported bug class where SPREAD/STAKE/PLURALITY-type parameters can be voted to invalid/extreme values to "gain unfairly," here manifesting concretely as inflation of the KAIA supply via `reward.mintingamount`, a governance parameter explicitly reachable through the standard `governance_vote` RPC and header governance ratification process.

### Likelihood Explanation
Exploitation requires control of governance voting power (the sole governing node in `single` mode — the mode used on Mainnet/Kairos — or GC majority in `none` mode), which is a plausible "Madman"/majority-collusion scenario explicitly called out in the source report. No other privilege (validator signing key, node compromise, etc.) is required beyond normal governance voting rights, and the change requires no code deploy or exploit chain — a single vote transaction and its ratification at the next epoch boundary is sufficient.

### Recommendation
Add an explicit `FormatChecker` for `RewardMintingAmount` (and other unconstrained numeric governance parameters such as `Kip71GasTarget`, `Kip71BaseFeeDenominator`, `Kip71MaxBlockGasUsedForBaseFee`, `RewardStakingRewardThreshold`) that enforces a non-negative value and a sane upper bound (e.g., a hardcoded maximum inflation-per-block ceiling, or a maximum percentage change from the current value), mirroring the invariant already applied to `RewardMinimumStake` (`v.Sign() >= 0`) and `RewardRatio` (percentages sum to 100). Consider adding increase/decrease rate limits enforced in `checkConsistency` similar to the existing `Kip71LowerBoundBaseFee`/`Kip71UpperBoundBaseFee` cross-checks.

### Proof of Concept
1. As the governing node (single mode) or with GC voting majority (none mode), call `governance_vote` with `{"governance_vote","params":["reward.mintingamount", "1000000000000000000000000000000"]}` (or any economically absurd value).
2. `PartialParamSet.Add`/`ParamSet.Set` accepts the value unconditionally because `RewardMintingAmount`'s `FormatChecker` is `noopFormatChecker` and `bigIntCanonicalizer` performs no bound checks. [1](#0-0) 
3. The vote is inscribed in `header.Vote`; `checkConsistency` passes it through without special validation. [5](#0-4) 
4. At the next epoch boundary, the vote is ratified into `header.Governance`, and `GetParamSet` reflects the new `MintingAmount` from that point on.
5. Starting from the first block after ratification, `getDeferredRewardSimple`/`getDeferredRewardFullKore`/etc. use `config.MintingAmount` as `minted`, and `FinalizeState` calls `state.AddBalance` for each recipient with amounts derived from this inflated `minted` value every block, permanently inflating the KAIA supply. [3](#0-2) [4](#0-3)

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
