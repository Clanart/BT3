### Title
DoS via division-by-zero panic in `consolidatedNode.Split()` when proposer has zero total (CN+CL) staking - ([File: kaiax/staking/staking_info.go])

### Summary
The reward module's Flex reward path calls `consolidatedNode.Split()` for the block proposer without verifying that the proposer's combined CN staking amount and consensus-liquidity (CL) staking amount are non-zero. If both are zero, the function performs an unguarded big.Int division by zero, which panics and crashes every full node that finalizes the block — a deterministic, network-wide chain halt.

### Finding Description
`consolidatedNode.Split()` computes `totalAmount = cnAmountBig + clAmountBig` and then divides by it unconditionally whenever `c.CLStakingInfo != nil`: [1](#0-0) 

This function is called from `specWithProposerAndFundsFlex`, which is invoked from `getDeferredRewardFullFlex` at every block's `FinalizeState()` when the Osaka/Flex reward policy is active. Crucially, the call for the *proposer* is guarded only by `cn.CLStakingInfo != nil` — it is **not** guarded by any minimum-stake or non-zero-staking check, unlike the parallel call sites in `assignStakingRewards`/`assignStakingRewardsFlex`, which only call `Split()` inside a branch that is only reached when `cnTotalStakingAmount > minStake` (i.e. guaranteed non-zero): [2](#0-1) 

A validator's `consolidatedNode.StakingAmount` can legitimately be `0` while still being a `Council`/`QualifiedValidators` member and even the block proposer: per the valset module's own rules, "If no validator meets the minimum staking requirement, all council members are qualified," and WeightedRandom proposer selection draws from qualified validators: [3](#0-2) 

If that same validator has previously registered a `CLStakingInfo` (a consensus-liquidity pool address) whose current `CLStakingAmount` has also dropped to zero (e.g., after CL withdrawals), then when this validator becomes the block proposer, `cn.CLStakingInfo != nil` is true but both `cnAmountBig` and `clAmountBig` are zero, making `totalAmount == 0`. The subsequent `clAmount.Div(clAmount, totalAmount)` panics with "division by zero."

### Impact Explanation
Because `FinalizeState()` is executed deterministically by every full node processing the block (not just the proposer), the panic is not isolated to one node — every honest node that finalizes this block crashes identically. This halts the entire network's block processing (chain liveness failure), a Medium/High severity DoS matching the classification requirement of "acceptance of an invalid transaction or block" / network-wide state processing failure caused by a reachable protocol state, analogous to the KeeperGauge division-by-zero DoS in the referenced report (an accumulator/denominator hitting zero under conditions reachable by normal protocol operation).

### Likelihood Explanation
This requires: (1) Osaka hardfork active with `reward.useflexreward = true`, (2) Prague hardfork active with consensus liquidity enabled, (3) a validator that has registered a `CLStakingInfo` pool, and (4) that validator's combined CN + CL staking dropping to zero while still remaining part of the council and being selected as proposer (achievable via the valset "all-demoted-then-none-demoted" fallback rule, or gradual unstaking down to exactly zero while other validators are also below the threshold). This is a state condition reachable through legitimate staking/withdrawal operations by validators and CL pool depositors, without requiring any malicious node/validator collusion — only ordinary economic behavior (full withdrawal) combined with existing protocol fallback rules.

### Recommendation
In `consolidatedNode.Split()`, check whether `totalAmount.Sign() == 0` before dividing and short-circuit to return `(amount, big.NewInt(0))` (or another well-defined fallback) in that case, mirroring the safe-guard pattern already used in `assignStakingRewards`/`assignStakingRewardsFlex`.

### Proof of Concept
1. Enable Prague + Osaka hardforks with `reward.useflexreward = true` and consensus liquidity.
2. Register a CL pool (`CLStakingInfo`) for validator V via the CL registry.
3. Have validator V fully withdraw its CNStaking stake to 0, and have CL depositors fully withdraw from V's CL pool so `CLStakingAmount` is also 0, while V remains in `Council`.
4. Ensure (per valset fallback rule) V remains "qualified" — e.g., all council members' stakes fall below `reward.minstake`, making the whole council qualified regardless of stake — and V is selected as proposer for the next block via WeightedRandom.
5. When `FinalizeState()` runs `getDeferredRewardFullFlex` → `specWithProposerAndFundsFlex` → `cn.Split(proposer)` for V, `totalAmount = 0`, causing a big.Int divide-by-zero panic in every node finalizing that block, halting the chain.

### Citations

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

**File:** kaiax/reward/impl/getter.go (L566-588)
```go
	newSpec.Proposer = proposer
	if !config.Rules.IsPrague || si.CLStakingInfos == nil {
		newSpec.IncRecipient(config.Rewardbase, proposer)
		return newSpec
	}

	// Handle CLStakingInfo for proposer after Prague
	cns := si.ConsolidatedNodes()
	for _, cn := range cns {
		if cn.RewardAddr != config.Rewardbase {
			continue
		}
		if cn.CLStakingInfo == nil {
			// Early exit if there's no CL for proposer
			break
		}

		cnAmount, clAmount := cn.Split(proposer)

		newSpec.IncRecipient(cn.RewardAddr, cnAmount)
		newSpec.IncRecipient(cn.CLStakingInfo.CLPoolAddr, clAmount)
		return newSpec
	}
```

**File:** kaiax/valset/README.md (L49-56)
```markdown
- RoundRobin: All council members are qualified.
- Sticky: All council members are qualified.
- WeightedRandom: Validators are qualified if it stakes no less than the minimum staking amount.
  - For genesis block, all council members are qualified.
  - For blocks before Istanbul hardfork, all council members are qualified.
  - If no validator meets the minimum staking requirement, all council members are qualified.
  - If the governance mode is "single", the governing node is unconditionally qualified to allow governance parameter change under any circumstances.
  - The minimum staking amount refers to the `reward.minstake` parameter, the governance moce is the `governance.governancemode` parameter and governing node is the `governance.governingnode` parameter.
```
