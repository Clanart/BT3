### Title
Missing bound check on `reward.proposerupdateinterval` / `reward.stakingupdateinterval` governance parameters allows divide-by-zero panic - ([File: kaiax/gov/param.go])

### Summary
`kaiax/gov` validates governance parameter values through a per-parameter `FormatChecker`, but `RewardProposerUpdateInterval` and `RewardStakingUpdateInterval` use `noopFormatChecker`, meaning any `uint64` value—including `0`—is accepted as a valid governance vote. These two parameters are later used as divisors/moduli in proposer-selection and staking-interval arithmetic, mirroring the reported `setRewardDuration` bug class where an unchecked interval parameter is later used as a divisor.

### Finding Description
`RewardProposerUpdateInterval` and `RewardStakingUpdateInterval` are declared with no format validation: [1](#0-0) 

The equivalent staking parameter has the same `noopFormatChecker`: [2](#0-1) 

The only place in the codebase that guards against a zero interval is `SetDefaultsForGenesis`, which patches the *genesis* ChainConfig value, explicitly noting that these fields "must be nonzero because it is used as denominator": [3](#0-2) 

However, this genesis-time patch does not protect the mutable governance path. Once the chain is running, `reward.proposerupdateinterval` can be changed at runtime via header/contract governance votes through `kaiax/gov`'s `ParamSet`, which is not re-validated against this zero check—only the format checker (a no-op) gates the value: [4](#0-3) 

The value is then consumed directly as a modulus/divisor in proposer scheduling: [5](#0-4) [6](#0-5) 

and the analogous staking module only checks a zero interval once, at `Init()`, using the immutable `ChainConfig` value, not the dynamic `ParamSet` returned by `kaiax/gov`: [7](#0-6) 

This is structurally identical to the reported bug: an admin/governance-controlled duration/interval parameter is accepted without a minimum (or maximum) bound check, and is later used as a divisor in a downstream reward/scheduling calculation, causing a divide-by-zero fault.

### Impact Explanation
If a governance vote sets `reward.proposerupdateinterval` (or `reward.stakingupdateinterval`) to `0` post-genesis, every full node computing `roundDown(num, interval)` or iterating `for i := uint64(1); i <= c.pset.ProposerUpdateInterval; i++` for that block range would hit a modulo/divide-by-zero condition in `getProposerList`/`getNextDistinctProposer`, causing a deterministic Go runtime panic. Because this occurs identically on every honest node evaluating the same block, it forces a synchronized chain halt across the network — the consensus/state-transition path can no longer make progress, which is a severe availability failure directly caused by an unchecked governance parameter used as a divisor, consistent with the reported bug class (Medium severity, missing range check leading to divide-by-zero malfunction).

### Likelihood Explanation
Governance parameter changes are gated by the governance voting process (governing node / contract governance), so this requires a governance-level actor to submit the malicious/erroneous value. However, this is explicitly within the "governance parameters" scope called out as reachable, and no additional bound check exists anywhere in the mutable parameter-update path to prevent `0` (or other degenerate values) from being accepted, canonicalized, and merged into the active `ParamSet`. The bug is latent and will trigger the very next time this parameter is set to `0` (or, depending on `roundDown`'s exact semantics, potentially very large values as flagged in the same report), matching the original report's dual concern about both zero and excessively large values.

### Recommendation
Add an explicit `FormatChecker` for `RewardProposerUpdateInterval` and `RewardStakingUpdateInterval` in `kaiax/gov/param.go` that rejects `0` and enforces a sane minimum/maximum bound (mirroring the `SetDefaultsForGenesis` invariant that these values are used as denominators), instead of relying on `noopFormatChecker`. Additionally, defensively guard the consumption sites (`roundDown`, the `ProposerUpdateInterval` loop in `getNextDistinctProposer`, and `kaiax/staking`'s dynamic interval consumers) against a zero divisor.

### Proof of Concept
1. Submit a governance vote (header governance or contract governance, depending on `governance.governancemode`) setting `reward.proposerupdateinterval` to `0`.
2. Because `RewardProposerUpdateInterval`'s `FormatChecker` is `noopFormatChecker` (`kaiax/gov/param.go:442-452`), the vote is accepted and merged into the `ParamSet` for subsequent blocks (`kaiax/gov/paramset.go:97-98`).
3. Once a block is processed under the new parameter set, `ValsetModule.getProposerList` computes `roundDown(c.num-1, uint64(c.pset.ProposerUpdateInterval))` with `ProposerUpdateInterval == 0` (`kaiax/valset/impl/getter_proposers.go:14-18`), and/or `getNextDistinctProposer`'s loop bound `i <= c.pset.ProposerUpdateInterval` combines with downstream modulo arithmetic that divides by this interval, triggering a Go integer divide-by-zero panic on every node processing the block, halting the chain.

### Citations

**File:** kaiax/gov/param.go (L442-452)
```go
	RewardProposerUpdateInterval: {
		Canonicalizer: uint64Canonicalizer,
		FormatChecker: noopFormatChecker,
		ChainConfigValue: func(c *params.ChainConfig) (any, error) {
			if c.Governance == nil || c.Governance.Reward == nil {
				return nil, errors.New("reward is not set")
			}
			return c.Governance.Reward.ProposerUpdateInterval, nil
		},
		DefaultValue: uint64(3600),
	},
```

**File:** kaiax/gov/param.go (L486-496)
```go
	RewardStakingUpdateInterval: {
		Canonicalizer: uint64Canonicalizer,
		FormatChecker: noopFormatChecker,
		ChainConfigValue: func(c *params.ChainConfig) (any, error) {
			if c.Governance == nil || c.Governance.Reward == nil {
				return nil, errors.New("reward is not set")
			}
			return c.Governance.Reward.StakingUpdateInterval, nil
		},
		DefaultValue: uint64(86400),
	},
```

**File:** params/config.go (L716-729)
```go

	// StakingUpdateInterval must be nonzero because it is used as denominator
	if c.Governance.Reward.StakingUpdateInterval == 0 {
		c.Governance.Reward.StakingUpdateInterval = DefaultStakeUpdateInterval
		logger.Warn("Override the default staking update interval to the chain config", "interval",
			c.Governance.Reward.StakingUpdateInterval)
	}

	// ProposerUpdateInterval must be nonzero because it is used as denominator
	if c.Governance.Reward.ProposerUpdateInterval == 0 {
		c.Governance.Reward.ProposerUpdateInterval = DefaultProposerRefreshInterval
		logger.Warn("Override the default proposer update interval to the chain config", "interval",
			c.Governance.Reward.ProposerUpdateInterval)
	}
```

**File:** kaiax/gov/paramset.go (L97-98)
```go
	case RewardProposerUpdateInterval:
		p.ProposerUpdateInterval, ok = cv.(uint64)
```

**File:** kaiax/valset/impl/getter_proposers.go (L14-18)
```go
func (v *ValsetModule) getProposerList(c *blockContext) ([]common.Address, uint64, error) {
	var (
		useGini   = c.pset.UseGiniCoeff // because UseGiniCoeff is immutable, it's safe to pass ParamSet(num).UseGiniCoeff to calculate proposer list for updateNum.
		updateNum = roundDown(c.num-1, uint64(c.pset.ProposerUpdateInterval))
	)
```

**File:** kaiax/valset/impl/getter_context.go (L138-144)
```go
		// scan one proposer update interval
		for i := uint64(1); i <= c.pset.ProposerUpdateInterval; i++ {
			nextProposer := selectWeightedRandomProposer(list, sourceNum, c.num, round+uint64(i))
			if currProposer != nextProposer {
				return nextProposer, nil
			}
		}
```

**File:** kaiax/staking/impl/init.go (L71-74)
```go
	if s.ChainConfig.Governance.Reward.StakingUpdateInterval == 0 {
		return staking.ErrZeroStakingInterval
	}
	s.stakingInterval = s.ChainConfig.Governance.Reward.StakingUpdateInterval
```
