## Analog Found

### Title
Missing Upper/Lower Bound Validation on Governance-Controlled Fee Parameters (UnitPrice, KIP-71 Base Fee) - ([File: kaiax/gov/param.go])

### Summary
The `nominators-v2` report flags that `op_update` lets a privileged owner set fee parameters with no sane upper bound, silently harming unprivileged participants. Kaia's governance parameter registry has the same class of gap: several fee-defining governance parameters — `governance.unitprice`, `kip71.gastarget`, `kip71.lowerboundbasefee`, `kip71.upperboundbasefee`, and `kip71.maxblockgasusedforbasefee` — are registered with `FormatChecker: noopFormatChecker`, which unconditionally returns `true` and performs no bound check at all.

### Finding Description
Every governance parameter is declared in `Params` with a `Canonicalizer` (type coercion) and a `FormatChecker` (value validation). For the fee-related parameters, the checker is a no-op: [1](#0-0) [2](#0-1) [3](#0-2) 

Only `Kip71BaseFeeDenominator` requires `v != 0`, and only a *relative* consistency check exists between `LowerBoundBaseFee` and `UpperBoundBaseFee` (each must not cross the other) — there is no absolute ceiling/floor on any of them: [4](#0-3) 

Consequently, a governance vote (cast by the governing node in single mode, or by council members) setting `governance.unitprice` to `math.MaxUint64`, or `kip71.lowerboundbasefee`/`kip71.upperboundbasefee` to an extreme value, will pass all format/consistency checks in `checkConsistency` and `Params[...].FormatChecker`, and once epoch-committed via `VerifyGov`, becomes the enforced gas price for every subsequent transaction sender on the network via `NextMagmaBlockBaseFee`: [5](#0-4) 

This mirrors the report's root cause exactly: a privileged actor (contract owner / governing node) can set a fee-controlling value with no upper bound, and the only protections in place are format/consistency checks that don't cap the actual magnitude.

### Impact Explanation
If `governance.unitprice` or the KIP-71 base-fee bounds are set to an unreasonably high value (by mistake or malice), every ordinary transaction sender, fee-delegation payer, gasless-swap user, and auction bidder on the network would be forced to pay an extortionate gas price or have all transactions effectively price-DoS'd, since `SuggestPrice`/`UpperBoundGasPrice`/`LowerBoundGasPrice` and the Magma base-fee computation derive directly from these unclamped values: [6](#0-5) 
Conversely, setting them to near-zero undermines spam protection. Both cases constitute a fee/economic-parameter abuse impacting all chain participants, not just the governance actor.

### Likelihood Explanation
This requires a governance vote from the governing node (single mode) or a validator/council member (ballot mode), so it is not exploitable by a fully unprivileged party in one shot — but the same is true of the original `nominators-v2` report, where the "attacker" is the pool owner. The lack of any FormatChecker means a single typo'd vote (e.g., extra digits, or unit confusion between wei/kei/ston) is sufficient to trigger the impact, with no code-level circuit breaker until the next governance vote/epoch cycle corrects it.

### Recommendation
Add explicit sanity bounds (`FormatChecker`) for `GovernanceUnitPrice`, `Kip71GasTarget`, `Kip71LowerBoundBaseFee`, `Kip71UpperBoundBaseFee`, and `Kip71MaxBlockGasUsedForBaseFee` in `kaiax/gov/param.go`, analogous to the existing `IstanbulCommitteeSize`/`IstanbulPolicy` checkers, so that governance votes proposing economically nonsensical fee values are rejected at `checkConsistency`/`VerifyVote` time rather than being silently accepted.

### Proof of Concept
1. Governing node casts a vote `NewVoteData(governingNode, "governance.unitprice", uint64(math.MaxUint64))`.
2. `VerifyVote` → `checkConsistency` hits the `case gov.GovernanceUnitPrice` branch which simply `return nil` (no format bound is enforced beyond the `noopFormatChecker` used when the vote is added to `PartialParamSet`), see [7](#0-6) .
3. At the next epoch, `VerifyGov` accepts the committed governance since it matches locally derived `expected` data.
4. `GetParamSet` now returns the malicious `UnitPrice`, and `CNAPIBackend.SuggestPrice`/`UpperBoundGasPrice` propagate it to all RPC callers and the mempool, pricing out every ordinary transaction sender.

### Citations

**File:** kaiax/gov/param.go (L259-264)
```go
	GovernanceUnitPrice: {
		Canonicalizer:    uint64Canonicalizer,
		FormatChecker:    noopFormatChecker,
		ChainConfigValue: func(c *params.ChainConfig) (any, error) { return c.UnitPrice, nil },
		DefaultValue:     uint64(250e9),
	},
```

**File:** kaiax/gov/param.go (L324-345)
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
	Kip71LowerBoundBaseFee: {
		Canonicalizer: uint64Canonicalizer,
		FormatChecker: noopFormatChecker,
		ChainConfigValue: func(c *params.ChainConfig) (any, error) {
			if c.Governance == nil || c.Governance.KIP71 == nil {
				return nil, errors.New("kip71 is not set")
			}
			return c.Governance.KIP71.LowerBoundBaseFee, nil
		},
		DefaultValue: uint64(25000000000),
	},
```

**File:** kaiax/gov/param.go (L356-367)
```go
	},
	Kip71UpperBoundBaseFee: {
		Canonicalizer: uint64Canonicalizer,
		FormatChecker: noopFormatChecker,
		ChainConfigValue: func(c *params.ChainConfig) (any, error) {
			if c.Governance == nil || c.Governance.KIP71 == nil {
				return nil, errors.New("kip71 is not set")
			}
			return c.Governance.KIP71.UpperBoundBaseFee, nil
		},
		DefaultValue: uint64(750000000000),
	},
```

**File:** kaiax/gov/headergov/impl/header.go (L188-201)
```go
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

**File:** params/kip71_config.go (L58-68)
```go
func (kc *KIP71Config) NextMagmaBlockBaseFee(parentHeaderNumber *big.Int, parentHeaderBaseFee *big.Int, parentHeaderGasUsed uint64) *big.Int {
	// governance parameters
	lowerBoundBaseFee := new(big.Int).SetUint64(kc.LowerBoundBaseFee)
	upperBoundBaseFee := new(big.Int).SetUint64(kc.UpperBoundBaseFee)
	makeEvenByCeil(lowerBoundBaseFee)
	makeEvenByFloor(upperBoundBaseFee)

	// If the parent is the magma disabled block or genesis, then return the lowerBoundBaseFee (default 25ston)
	if parentHeaderNumber.Cmp(new(big.Int).SetUint64(0)) == 0 || parentHeaderBaseFee == nil {
		return makeEvenByFloor(lowerBoundBaseFee)
	}
```

**File:** node/cn/api_backend.go (L333-351)
```go
func (b *CNAPIBackend) UpperBoundGasPrice(ctx context.Context) *big.Int {
	bignum := b.CurrentBlock().Number()
	pset := b.cn.govModule.GetParamSet(bignum.Uint64() + 1)
	if b.cn.chainConfig.IsMagmaForkEnabled(bignum) {
		return new(big.Int).SetUint64(pset.UpperBoundBaseFee)
	} else {
		return new(big.Int).SetUint64(pset.UnitPrice)
	}
}

func (b *CNAPIBackend) LowerBoundGasPrice(ctx context.Context) *big.Int {
	bignum := b.CurrentBlock().Number()
	pset := b.cn.govModule.GetParamSet(bignum.Uint64() + 1)
	if b.cn.chainConfig.IsMagmaForkEnabled(bignum) {
		return new(big.Int).SetUint64(pset.LowerBoundBaseFee)
	} else {
		return new(big.Int).SetUint64(pset.UnitPrice)
	}
}
```
