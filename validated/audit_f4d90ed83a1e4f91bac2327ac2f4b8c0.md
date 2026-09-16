Confirmed: `bigIntCanonicalizer` accepts any parseable integer (including negative, since `big.Int.SetString` allows a leading `-`) with no range restriction, and `RewardMintingAmount`'s `FormatChecker` is `noopFormatChecker` (always returns true), unlike sibling params like `RewardMinimumStake` which explicitly require `v.Sign() >= 0`.

### Title
Unbounded `reward.mintingamount` governance parameter allows arbitrary token-supply inflation - (File: kaiax/gov/param.go)

### Summary
### Finding Description
The governance parameter `reward.mintingamount` (`RewardMintingAmount`) is registered in `Params` with `Canonicalizer: bigIntCanonicalizer` and `FormatChecker: noopFormatChecker`, which unconditionally returns `true` and performs no bound or sign check on the value. [1](#0-0) 
This is in sharp contrast to the neighboring `RewardMinimumStake` parameter, which explicitly validates `v.Sign() >= 0` in its `FormatChecker`, showing that other numeric governance parameters are expected to be range-checked while `RewardMintingAmount` is not. [2](#0-1) 

The `bigIntCanonicalizer` used for this parameter simply parses any decimal string or byte value into a `*big.Int` with no magnitude constraint. [3](#0-2) 

When a vote/header-governance change or contract-governance change for `reward.mintingamount` is processed, `checkConsistency` in the header governance module performs no additional validation for this parameter name — it simply falls through to `return nil` alongside other "no more checks here" parameters. [4](#0-3) 

The resulting `MintingAmount` is loaded directly into `RewardConfig.MintingAmount` from the effective parameter set every block, [5](#0-4) 
and is minted every single block without any sanity cap, both in the "Simple" reward path and the "Full" (Kore/Flex) reward path: [6](#0-5) [7](#0-6) 

This minted amount is subsequently credited to validators/funds via `IncRecipient` and ultimately applied through `StateDB.AddBalance`, which has no overflow/sanity guard beyond 256-bit wraparound. [8](#0-7) 

This is the direct analog of the reported `DAO.setAdvanceIncentive()` bug: a governance/admin-controlled numeric parameter that directly drives token minting has no maximum-value enforcement, so a single vote (in `governancemode=single`, cast by the `GoverningNode`; or via `ContractGov`'s `setParamIn`) can set an arbitrarily large minting amount, causing every subsequent block to mint that huge amount to validators/funds.

### Impact Explanation
If the governing node (single-mode) or the party controlling `GovParamContract` sets `reward.mintingamount` to an extremely large value (e.g. near `2^256`), every future block mints that amount to the proposer, stakers, and funds until the parameter is changed again. This is a direct token-supply-inflation / rug-pull vector — the same class of impact called out in the source report ("owner could set the incentive to an exorbitant amount with the goal of minting a lot of tokens for an exit scam"). Because minting happens automatically every block via `FinalizeState`/`GetDeferredReward`, the damage compounds extremely quickly (potentially draining all economic value of the native KAIA token within a single block or few blocks), and honest nodes will accept these blocks since the parameter passed all format/consistency checks.

### Likelihood Explanation
This requires control of governance (the `GoverningNode` in single mode, or the entity managing `GovParamContract`), which is a privileged actor — but it is exactly the scenario the reference report calls Medium severity: "contingent on the admin's action" yet still concretely exploitable through a single governance transaction/vote with no additional protocol-level safeguard, distinct from a purely off-chain/social/operator-only concern. No other kaia numeric reward/governance parameter with token-minting effect lacks a `FormatChecker` bound in this way, reinforcing that this is a missing-validation bug rather than intended design.

### Recommendation
Add a `FormatChecker` for `RewardMintingAmount` (and any other minting-linked parameters) that enforces a sane upper bound (e.g., relative to genesis minting amount, or a hard cap such as a fixed multiple of the default value), mirroring the non-negativity check already present on `RewardMinimumStake`. Additionally, consider adding an explicit case in `checkConsistency` for `gov.RewardMintingAmount` that rejects a proposed change if it deviates too far (e.g. more than some percentage) from the current value, providing defense-in-depth against a single malicious/compromised governance vote causing catastrophic inflation.

### Proof of Concept
1. Deploy/operate a Kaia chain in `governance.governancemode = "single"`.
2. As the `GoverningNode`, submit a header-governance vote setting `reward.mintingamount` to an extremely large decimal string, e.g. `"115792089237316195423570985008687907853269984665640564039457584007913129639935"` (max uint256).
3. `NewVoteData`/`Set` accepts it because `bigIntCanonicalizer` parses any big integer and `noopFormatChecker` always returns `true`. [1](#0-0) 
4. `checkConsistency` performs no extra validation for `gov.RewardMintingAmount`, so `VerifyGov` accepts the header. [4](#0-3) 
5. Once the vote takes effect (at the next epoch/governance block), `RewardConfig.MintingAmount` reflects this huge value for every subsequent block. [5](#0-4) 
6. On the very next block, `getDeferredRewardSimple`/`getDeferredRewardFull*` mint this amount and distribute it to the proposer/stakers/funds via `spec.IncRecipient`, which is applied to state via `AddBalance`, instantly inflating the token supply to an attacker-chosen level. [6](#0-5)

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

**File:** kaiax/reward/config.go (L59-66)
```go
	paramset := govModule.GetParamSet(header.Number.Uint64())
	rc.IsSimple = paramset.ProposerPolicy != uint64(istanbul.WeightedRandom)
	rc.UnitPrice = new(big.Int).SetUint64(paramset.UnitPrice)
	rc.MintingAmount = new(big.Int).Set(paramset.MintingAmount)
	rc.MinimumStake = new(big.Int).Set(paramset.MinimumStake)
	rc.DeferredTxFee = paramset.DeferredTxFee
	rc.StakingRewardThreshold = new(big.Int).Set(paramset.StakingRewardThreshold)
	rc.UseFlexReward = paramset.UseFlexReward
```

**File:** kaiax/reward/impl/getter.go (L224-235)
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
```

**File:** kaiax/reward/impl/getter.go (L299-311)
```go
// getDeferredRewardFullFlex is for non-Simple policy, after Kore, and with UseFlexReward enabled.
func getDeferredRewardFullFlex(config *reward.RewardConfig, execFee, burntFee, blobFee *big.Int, si *staking.StakingInfo) (*reward.RewardSpec, error) {
	var (
		spec             = reward.NewRewardSpec()
		minted           = new(big.Int).Set(config.MintingAmount)
		distributableFee = new(big.Int).Sub(execFee, burntFee)
	)

	// Distribute using RewardRatio (4-part) first. Unlike Legacy, fees are not distributed here
	// because fees are exclusively allocated to proposer. By the way, remainder goes to KIF.
	validators, kif, kef, kpf := config.RewardRatio.SplitFlex(minted)
	proposer, stakers := config.Kip82Ratio.Split(validators)
	ratioRemainder := calcRemainder(minted, proposer, stakers, kif, kef, kpf)
```

**File:** blockchain/state/statedb.go (L491-497)
```go
// AddBalance adds amount to the account associated with addr.
func (s *StateDB) AddBalance(addr common.Address, amount *big.Int) {
	stateObject := s.GetOrNewStateObject(addr)
	if stateObject != nil {
		stateObject.AddBalance(amount)
	}
}
```
