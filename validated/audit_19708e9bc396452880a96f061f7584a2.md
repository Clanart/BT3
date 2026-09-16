### Title
Missing bounds/sign validation on `reward.mintingamount` governance parameter allows unbounded or negative native-token minting - ([File: kaiax/gov/param.go])

### Summary
The `RewardMintingAmount` governance parameter, which directly controls the amount of native KAIA token minted every block (block reward inflation), is registered with `FormatChecker: noopFormatChecker` in `kaiax/gov/param.go`, meaning **no validation is performed** on the value beyond being parseable as a `*big.Int`. This mirrors the reported UXP token bug: a mint-controlling value can be set to an unbounded or invalid (negative) amount because there is no upper/lower boundary check, unlike sibling parameters such as `RewardMinimumStake` (which enforces `v.Sign() >= 0`) or `RewardRatio`/`RewardKip82Ratio` (which enforce sum-to-100 invariants).

### Finding Description
`Params` map defines each governance parameter with a `Canonicalizer` and `FormatChecker`. For `RewardMintingAmount`: [1](#0-0) 

`bigIntCanonicalizer` accepts any `*big.Int`, including negative values, with no sign check: [2](#0-1) 

Contrast this with `RewardMinimumStake`, which is validated for non-negativity via its `FormatChecker`: [3](#0-2) 

and with `RewardRatio`/`RewardKip82Ratio`, which enforce percentage-sum invariants via their `FormatChecker`s: [4](#0-3) 

The `checkConsistency` function in header governance vote validation, which performs additional state-dependent checks for select parameters (e.g. `Kip71LowerBoundBaseFee`/`Kip71UpperBoundBaseFee` cross-validation), explicitly passes `RewardMintingAmount` through with no such check: [5](#0-4) 

The value flows directly and unmodified into `RewardConfig.MintingAmount`, which is then used as the source of the block's `Minted` amount in every reward-distribution code path (`getRewardSummary`, `getDeferredRewardSimple`, `getDeferredRewardFullKore`, `getDeferredRewardFullFlex`, `getDeferredRewardFullLegacy`): [6](#0-5) [7](#0-6) 

This minted amount is directly added to `AccReward.TotalMinted`, which is the authoritative running total that backs the `kaia_getTotalSupply` API and the protocol's notion of total native-token supply: [8](#0-7) [9](#0-8) 

Because there is no cap check analogous to `ERC20Capped._mint` (`require(totalSupply().add(value) <= _cap, ...)`), an arbitrarily large `reward.mintingamount` value would cause unbounded inflation minted into circulation every block, and a negative value (permitted by `bigIntCanonicalizer`/`noopFormatChecker`) could corrupt the reward computation and the total-supply accounting (subtraction instead of addition, or negative balances/reward specs), since none of the downstream arithmetic (`new(big.Int).Set(config.MintingAmount)`, `.Add(...)`, `.Split(...)`) defends against a negative or unbounded value.

### Impact Explanation
This is a High-severity issue because a single governance parameter change (reachable through the standard governance-vote mechanism the same way `reward.ratio`, `kip71.*`, etc. are reachable) can:
- Inflate the total native KAIA supply arbitrarily beyond any economically intended bound, diluting all holders' value — directly analogous to bypassing the UXP 7B-token supply cap.
- If set negative, corrupt reward distribution math (`Minted`, `Proposer`, `Stakers`, fund allocations) and the `kaiax/supply` total-supply accounting, potentially causing state divergence, incorrect `kaia_getTotalSupply` responses, or malformed reward specs that could crash or misallocate funds during `FinalizeState`.

This satisfies the "supply inflation" and "reward redirection / state divergence" impact criteria in the validation rules.

### Likelihood Explanation
Likelihood depends on governance/GC compromise or misconfiguration (single governance node under Kaia's "single" governance mode can already unilaterally set several such parameters). Given that the vote-submission and format-check layer is the only gate preventing an out-of-range value, and that gate performs zero validation for this specific parameter (in contrast to explicitly-guarded siblings), this is a straightforward, low-effort path to a high-impact outcome once a malicious or erroneous governance vote is cast — no additional cryptographic or consensus-level exploit needed.

### Recommendation
Add a `FormatChecker` for `RewardMintingAmount` analogous to `RewardMinimumStake`'s, at minimum enforcing `v.Sign() >= 0`, and additionally consider an explicit reasonable upper bound (or a governance-configurable cap parameter) to prevent unbounded inflation, mirroring the `ERC20Capped` pattern recommended in the original report. Also add this parameter's bound check to `checkConsistency` in `kaiax/gov/headergov/impl/header.go` if state-dependent validation is deemed appropriate (e.g., relative to previous value or a hardcoded ceiling).

### Proof of Concept
1. Submit a governance vote with `name = "reward.mintingamount"` and `value` set to an extreme `*big.Int` (e.g., `"999999999999999999999999999999"`) or a negative value (e.g., `"-1"`).
2. `NewVoteData`/`bigIntCanonicalizer` accepts both since it merely calls `new(big.Int).SetString(v, 10)` with no bound check (`kaiax/gov/param.go:94-112`).
3. `FormatChecker = noopFormatChecker` for `RewardMintingAmount` performs no rejection (`kaiax/gov/param.go:414-424`).
4. `checkConsistency` for `gov.RewardMintingAmount` returns `nil` unconditionally (`kaiax/gov/headergov/impl/header.go:217-220`).
5. Once the vote is applied at the next epoch/governance block, `RewardConfig.MintingAmount` reflects the malicious value and is minted every subsequent block via `getRewardSummary`/`getDeferredReward*` (`kaiax/reward/impl/getter.go:86-109, 371-390`), inflating `AccReward.TotalMinted` in `kaiax/supply` without bound (`kaiax/supply/impl/getter.go:204-230`).

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

**File:** kaiax/gov/param.go (L379-413)
```go
	RewardKip82Ratio: {
		Canonicalizer: stringCanonicalizer,
		FormatChecker: func(cv any) bool {
			v, ok := cv.(string)
			if !ok {
				return false
			}
			parts := strings.Split(v, "/")
			if len(parts) != 2 {
				return false
			}
			sum := 0
			for _, part := range parts {
				num, err := strconv.Atoi(part)
				if err != nil {
					return false
				}
				if num < 0 {
					return false
				}
				sum += num
			}

			return sum == 100
		},
		ChainConfigValue: func(c *params.ChainConfig) (any, error) {
			// This parameter may be absent in ChainConfig because it was introduced at Kore.
			// However, ChainConfig.SetDefaults() should have set it to the default value.
			if c.Governance == nil || c.Governance.Reward == nil {
				return nil, errors.New("reward is not set")
			}
			return c.Governance.Reward.Kip82Ratio, nil
		},
		DefaultValue: "20/80",
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

**File:** kaiax/reward/impl/getter.go (L86-109)
```go
func getRewardSummary(config *reward.RewardConfig, execFee, blobFee *big.Int) *reward.RewardSummary {
	minted := new(big.Int).Set(config.MintingAmount)

	burntFee := big.NewInt(0)
	if config.IsSimple { // simplified getDeferredRewardSimple
		if config.Rules.IsMagma {
			burntFee = getBurnAmountMagma(execFee)
		}
	} else { // simplified getDeferredRewardFull
		if config.Rules.IsKore {
			burntFee = getBurnAmountKore(config, execFee)
		} else if config.Rules.IsMagma {
			burntFee = getBurnAmountMagma(execFee)
		}
	}

	// Blob fees (KIP-279) are 100% burned during state transition per EIP-4844 spec,
	// in both deferred and non-deferred modes.
	// They are tracked separately because they must not enter the reward-split calculation.
	summary := reward.NewRewardSummary()
	summary.Minted = minted
	summary.TotalFee = new(big.Int).Add(execFee, blobFee)
	summary.BurntFee = new(big.Int).Add(burntFee, blobFee)
	return summary
```

**File:** kaiax/reward/impl/getter.go (L371-390)
```go
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

**File:** kaiax/supply/impl/getter.go (L204-230)
```go
// accumulateRewards accumulates the reward increments from `fromNum` to `toNum`, inclusive.
// If `write` is true, the intermediate results at checkpointInterval will be written to the database.
func (s *SupplyModule) accumulateRewards(fromNum, toNum uint64, fromAccReward *supply.AccReward, write bool) (*supply.AccReward, error) {
	accReward := fromAccReward.Copy() // make a copy because we're updating it in-place.

	for num := fromNum + 1; num <= toNum; num++ {
		if s.quit.Load() == 1 { // Received quit signal
			return nil, supply.ErrSupplyModuleQuit
		}

		summary, err := s.RewardModule.GetRewardSummary(num)
		if err != nil {
			return nil, err
		}
		accReward.TotalMinted.Add(accReward.TotalMinted, summary.Minted)
		accReward.BurntFee.Add(accReward.BurntFee, summary.BurntFee)

		if write && (num%checkpointInterval) == 0 {
			WriteAccReward(s.ChainKv, num, accReward)
			WriteLastAccRewardNumber(s.ChainKv, num)
		}
		if (num % accumulateLogInterval) == 0 {
			logger.Info("Accumulated block rewards", "number", num, "minted", accReward.TotalMinted.String(), "burntFee", accReward.BurntFee.String())
		}
	}
	return accReward, nil
}
```

**File:** kaiax/supply/total_supply.go (L25-64)
```go
// AccReward is a subset of TotalSupply that comprises the minted and burnt amounts by the block reward mechanism.
type AccReward struct {
	TotalMinted *big.Int // Genesis + Minted[1..n]
	BurntFee    *big.Int // BurntFee[1..n]
}

func (ar *AccReward) Copy() *AccReward {
	return &AccReward{
		TotalMinted: new(big.Int).Set(ar.TotalMinted),
		BurntFee:    new(big.Int).Set(ar.BurntFee),
	}
}

func (ar *AccReward) ToTotalSupply(zeroBurn, deadBurn, kip103Burn, kip160Burn *big.Int) *TotalSupply {
	ts := &TotalSupply{
		TotalSupply: nil, // will be filled below

		TotalMinted: new(big.Int).Set(ar.TotalMinted),

		TotalBurnt: nil, // will be filled below
		BurntFee:   new(big.Int).Set(ar.BurntFee),
		ZeroBurn:   zeroBurn,
		DeadBurn:   deadBurn,
		Kip103Burn: kip103Burn,
		Kip160Burn: kip160Burn,
	}

	if ar.TotalMinted != nil && ar.BurntFee != nil && zeroBurn != nil && deadBurn != nil && kip103Burn != nil && kip160Burn != nil {
		totalBurnt := new(big.Int).Set(ar.BurntFee)
		totalBurnt.Add(totalBurnt, zeroBurn)
		totalBurnt.Add(totalBurnt, deadBurn)
		totalBurnt.Add(totalBurnt, kip103Burn)
		totalBurnt.Add(totalBurnt, kip160Burn)
		totalSupply := new(big.Int).Sub(ar.TotalMinted, totalBurnt)

		ts.TotalSupply = totalSupply
		ts.TotalBurnt = totalBurnt
	}
	return ts
}
```
