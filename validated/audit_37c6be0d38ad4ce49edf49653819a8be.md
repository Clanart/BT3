### Title
Small stakers can receive zero staking reward due to integer-division rounding in `assignStakingRewards`/`assignStakingRewardsFlex`, silently redirecting reward value to the proposer - (File: `kaiax/reward/impl/getter.go`)

### Summary
The reward class of "rounding to zero causes a party to receive nothing while their contribution is still consumed" reported against `FM_BC_Bancor_Redeeming_VirtualSupply_v1` has a structural analog in Kaia's `kaiax/reward` module. When per-CN staking rewards are computed with `excess * budget / totalExcess`, a validator whose staked "excess" (stake above `minStake`/threshold) is small relative to the sum of all other validators' excess can have its computed reward round down to exactly `0`. That validator's stake is still counted toward `totalExcess` (i.e., it dilutes the pool and is used in the proposer/reward accounting), but it is excluded from `alloc` and the amount is silently swept into the "remainder," which is subsequently credited to the block proposer.

### Finding Description
`assignStakingRewards` computes each eligible CN's share of the staking reward budget proportionally to its stake in excess of `minStake`: [1](#0-0) 

The per-CN reward is `Div(Mul(excess, stakersReward), totalExcess)`. Integer division in Go truncates toward zero, so any CN with `excess * stakersReward < totalExcess` produces `reward == 0`, which fails the `reward.Sign() > 0` guard and the CN is entirely skipped in `alloc`. The unallocated amount remains in `remaining`, which is returned as `kip82Remainder`: [2](#0-1) 

The caller then explicitly redirects that remainder to the proposer instead of the entitled staker: [3](#0-2) 

The same pattern exists for the flexible-reward variant, `assignStakingRewardsFlex`, which additionally splits between CN and Consensus-Liquidity (CL) pools via `consolidatedNode.Split`, another integer-truncating division: [4](#0-3) [5](#0-4) 

The module's own README documents this as intended fallback behavior ("Remainder from distributing staking rewards among validators is sent to the proposer"), which confirms the rounding-to-zero case is a known but unmitigated edge case rather than a deliberate zero-reward policy for legitimately staked validators: [6](#0-5) 

### Impact Explanation
A validator that satisfies all staking eligibility requirements (`StakingAmount >= minStake`) and is therefore counted as an eligible staker contributing to `totalExcess` can nonetheless receive `0` reward for a distribution epoch purely due to truncation, whenever its own excess stake is a very small fraction of the aggregate excess across all validators (a scenario made more likely as the total validator set's excess stake grows, e.g., after governance increases `CommitteeSize` or with many validators near `minStake`). The value that should have gone to that validator is instead credited to the block proposer via `kip82Remainder`, i.e., unauthorized value redirection away from legitimately entitled stakers toward the proposer, without any error, revert, or visibility to the affected staker.

### Likelihood Explanation
This is reachable purely through normal, unprivileged usage: any account can become a staker by staking KAIA in a CN staking contract at or slightly above `minStake`/`reward.minstake`. As more validators join or as governance-configured `reward.minstake`/`reward.stakingrewardthreshold` and reward ratios change, the relative excess of a small staker versus the aggregate can easily fall into the truncation range. No malicious node, consensus-message, or privileged action is required — it is purely a function of publicly known staking parameters and each block's reward computation, which runs deterministically on every node.

### Recommendation
In `assignStakingRewards` and `assignStakingRewardsFlex` (`kaiax/reward/impl/getter.go`), track the sum of per-CN rounding losses and either (a) distribute the residual back proportionally in a second pass instead of assigning it wholesale to the proposer, or (b) accumulate small dust amounts across epochs per staker so a validator is not permanently zeroed out by rounding. At minimum, document and verify that this "proposer captures rounding remainder" behavior is an accepted design tradeoff rather than an oversight, since the current behavior structurally transfers value away from small-but-eligible stakers every epoch.

### Proof of Concept
1. Configure a chain with several CNs staked such that one CN's excess (`StakingAmount - minStake`) is tiny relative to the sum of all CNs' excess, e.g., `totalExcessInt = 10_000_000` (in KAIA units) and one CN's `excess = 1`, `stakersReward = 999_999` (kei).
2. Compute `reward = Div(Mul(1, 999_999), 10_000_000) = 0` (Go big.Int truncating division).
3. Observe in `assignStakingRewards` (`kaiax/reward/impl/getter.go:509-533`) that this CN is skipped in `alloc` (no entry), while `remaining` (which becomes `kip82Remainder`) retains the `1`-unit-of-precision loss.
4. Trace the caller `getDeferredRewardFullKore` (`kaiax/reward/impl/getter.go:334-357`): `proposer.Add(proposer, kip82Remainder)` credits this remainder to the proposer, confirming the reward that should have accrued to the small staker is instead paid to the proposer.
5. Existing unit tests such as `TestAssignStakingRewards` in `kaiax/reward/impl/getter_test.go` (see the "remainder" test case at lines 1263-1279) already assert non-zero remainders are produced and absorbed elsewhere, corroborating that this rounding-driven redirection is a real, observable code path rather than a purely theoretical scenario.

### Citations

**File:** kaiax/reward/impl/getter.go (L334-357)
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
```

**File:** kaiax/reward/impl/getter.go (L421-484)
```go
// assignStakingRewardsFlex assigns staking rewards to stakers according to their staking amounts.
// Returns the allocation and the remainder.
func assignStakingRewardsFlex(config *reward.RewardConfig, budget *big.Int, si *staking.StakingInfo) (map[common.Address]*big.Int, *big.Int) {
	var (
		minStake  = config.MinimumStake.Uint64()
		threshold = config.StakingRewardThreshold.Uint64()
		isPrague  = config.Rules.IsPrague

		cns            = si.ConsolidatedNodes()
		excessInt      = make(map[common.Address]uint64)
		totalExcessInt = uint64(0)
	)

	// Calculate the excess stakes (the amount over the threshold) for each CN.
	for _, cn := range cns {
		// If the CNStaking is less than minStake, skip it. Even if (CNStaking + CLStaking) could be more than minStake,
		// the CNStaking alone must be at least minStake to be eligible.
		if cn.StakingAmount < minStake {
			continue
		}

		amount := cn.StakingAmount
		if isPrague && cn.CLStakingInfo != nil {
			amount += cn.CLStakingInfo.CLStakingAmount
		}

		// Excess is the amount over the threshold (not over minStake).
		if amount > threshold {
			excessInt[cn.RewardAddr] = amount - threshold
			totalExcessInt += excessInt[cn.RewardAddr]
		}
	}

	// Distribute the budget to the CNs based on the excess stakes.
	var (
		totalExcess = new(big.Int).SetUint64(totalExcessInt)
		remaining   = new(big.Int).Set(budget)
		alloc       = make(map[common.Address]*big.Int)
	)
	for _, cn := range cns {
		if excessInt[cn.RewardAddr] <= 0 {
			continue
		}
		excess := new(big.Int).SetUint64(excessInt[cn.RewardAddr])

		// The KAIA unit will cancel out:
		// reward (kei) = excess (KAIA) * budget (kei) / totalExcess (KAIA)
		reward := new(big.Int).Div(new(big.Int).Mul(excess, budget), totalExcess)
		if reward.Sign() <= 0 {
			continue
		}

		// If Prague and CL is configured for this CN, split the reward between CN and CL.
		if isPrague && cn.CLStakingInfo != nil {
			cnAmount, clAmount := cn.Split(reward)
			alloc[cn.RewardAddr] = cnAmount
			alloc[cn.CLStakingInfo.CLPoolAddr] = clAmount
		} else {
			alloc[cn.RewardAddr] = reward
		}
		remaining.Sub(remaining, reward)
	}
	return alloc, remaining
}
```

**File:** kaiax/reward/impl/getter.go (L486-507)
```go
// assignStakingRewards assigns staking rewards to stakers according to their staking amounts.
// Returns the allocation and the remainder.
func assignStakingRewards(config *reward.RewardConfig, stakersReward *big.Int, si *staking.StakingInfo) (map[common.Address]*big.Int, *big.Int) {
	var (
		cns               = si.ConsolidatedNodes()
		minStake          = config.MinimumStake.Uint64()
		totalExcessInt    = uint64(0) // sum of excess stakes (the amount over minStake) over all stakers
		cnTotalStakingMap = make(map[common.Address]uint64)
		isPrague          = config.Rules.IsPrague
	)
	for _, cn := range cns {
		// If the CNStaking is less than minStake, skip it.
		if cn.StakingAmount >= minStake {
			// Calculate total staking amount once
			cnTotalStakingAmount := cn.StakingAmount
			if isPrague && cn.CLStakingInfo != nil {
				cnTotalStakingAmount += cn.CLStakingInfo.CLStakingAmount
			}
			totalExcessInt += cnTotalStakingAmount - minStake
			cnTotalStakingMap[cn.RewardAddr] = cnTotalStakingAmount
		}
	}
```

**File:** kaiax/reward/impl/getter.go (L509-533)
```go
	var (
		totalExcess = new(big.Int).SetUint64(totalExcessInt)
		remaining   = new(big.Int).Set(stakersReward)
		alloc       = make(map[common.Address]*big.Int)
	)
	for _, cn := range cns {
		cnTotalStakingAmount := cnTotalStakingMap[cn.RewardAddr]
		if cnTotalStakingAmount > minStake {
			// The KAIA unit will cancel out:
			// reward (kei) = excess (KAIA) * stakersReward (kei) / totalExcess (KAIA)
			excess := new(big.Int).SetUint64(cnTotalStakingAmount - minStake)
			if reward := new(big.Int).Div(new(big.Int).Mul(excess, stakersReward), totalExcess); reward.Sign() > 0 {
				if isPrague && cn.CLStakingInfo != nil {
					// The remaining amount will be added to the cnAmount.
					cnAmount, clAmount := cn.Split(reward)
					alloc[cn.RewardAddr] = cnAmount
					alloc[cn.CLStakingInfo.CLPoolAddr] = clAmount
				} else {
					alloc[cn.RewardAddr] = reward
				}
				remaining.Sub(remaining, reward)
			}
		}
	}
	return alloc, remaining
```

**File:** kaiax/staking/staking_info.go (L169-187)
```go
func (c consolidatedNode) Split(amount *big.Int) (*big.Int, *big.Int) {
	if c.CLStakingInfo == nil {
		return amount, big.NewInt(0)
	}

	var (
		cnAmountBig = big.NewInt(int64(c.StakingAmount))
		clAmountBig = big.NewInt(int64(c.CLStakingInfo.CLStakingAmount))
		totalAmount = new(big.Int).Add(cnAmountBig, clAmountBig)
	)

	clAmount := new(big.Int).Mul(clAmountBig, amount)
	clAmount = clAmount.Div(clAmount, totalAmount)

	// The remaining amount is for the CN.
	cnAmount := big.NewInt(0).Sub(amount, clAmount)

	return cnAmount, clAmount
}
```

**File:** kaiax/reward/README.md (L66-76)
```markdown
- **Kore rule (KIP-82)**: The rule since the KIP-82 hardfork.
  - MR: M is distributed according to the reward ratio and KIP-82 ratio.
    - The rewards allocated to stakers is further distributed by their relative staking amounts. The staker rewards are proportional to their staking amounts exceeding the minimum staking amount. The minimum staking amount refers to the `reward.minstake` parameter which determines the staking requirement to be a validator.
    - If no validator has staked more than the minimum staking amount, all staking rewards are sent to the proposer.
    - Remainders from the reward ratio and KIP-82 proposer/staker ratio divisions are sent to Fund1. The remainder from distributing staking rewards among validators is sent to the proposer.
  - NDF: Same as the previous rule.
  - DF: Proposer receives `max(0, F/2 - gpM)` and rest of the fees are burnt.
    - The proposer's minting reward is fixed to a product of minting amount (M), validator's reward ratio (g) and KIP-82 proposer ratio (p). This amount is considered the minimum operation cost of a validator.
    - Among the fees (F), half is always burnt since Magma. The other half (F/2) is burnt up to the proposer's minting reward (gpM), but the exceeding part (F/2 - gpM) is granted to the proposer.
    - Summing up, the proposer is guaranteed a minimum even if transaction fees are no enough to support the validator operating cost, yet incentivized to include as many transactions as possible for more reward.
    - As a special case, if the proposer reward ratio is zero `p=0`, then proposer receives `F/2` and the other `F/2` is burnt.
```
