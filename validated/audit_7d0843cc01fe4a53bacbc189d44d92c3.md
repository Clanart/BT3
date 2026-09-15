## Title
Division-by-zero panic in `consolidatedNode.Split` during proposer reward finalization when a validator's CN and CL stakes are both zero - (File: `kaiax/staking/staking_info.go`)

## Summary
This is the same bug class as the C4 finding: an unguarded division whose divisor can become `0` once a specific state (all relevant stake withdrawn) is reached, permanently breaking a core operation — here, block finalization/reward distribution rather than a Basket's `mint`.

## Finding Description
`consolidatedNode.Split` divides by `totalAmount = cnAmountBig + clAmountBig` whenever `c.CLStakingInfo != nil`: [1](#0-0) 

This function is called in two different contexts within `kaiax/reward/impl/getter.go`:

1. Inside `assignStakingRewards`/`assignStakingRewardsFlex`, the call is guarded: `cn.Split(...)` is only invoked after establishing `cnTotalStakingAmount > minStake` (or excess `> threshold`), which mathematically guarantees `cnTotalStakingAmount = StakingAmount + CLStakingAmount > 0`. [2](#0-1) 

2. Inside `specWithProposerAndFunds` / `specWithProposerAndFundsFlex`, which finalize the **proposer's own** reward, the call is *not* guarded by any minimum-stake check — it only checks that the CN's `RewardAddr` matches the block's `Rewardbase` and that `CLStakingInfo != nil`: [3](#0-2) [4](#0-3) 

The module's own README states that after Prague/KIP-226, "Otherwise, validator will not be eligible for rewards" when CNStaking is below `reward.minstake` — but this eligibility rule is enforced only in the staker-allocation path, not in the proposer-path `Split` call: [5](#0-4) 

If the block proposer's consolidated node has `StakingAmount == 0` (CNStaking fully withdrawn) and its registered `CLStakingInfo.CLStakingAmount == 0` (consensus-liquidity pool also fully withdrawn), then `totalAmount` becomes `0`, and `clAmount.Div(clAmount, totalAmount)` in `Split` performs an integer division by zero, which panics in Go (`runtime error: integer divide by zero`), unlike Solidity's `SafeMath.div` that reverts gracefully.

## Impact Explanation
Because `FinalizeState`/reward computation runs identically and deterministically on every node processing the block, a panic here does not merely diverge state between honest nodes — it crashes the finalization routine for the entire network on that block, functionally halting the chain until intervention, exactly analogous to how the original Basket became "unusable" once `totalSupply` hit zero and `handleFees` reverted for everyone. This is a chain-level denial of service rather than a contract-level one.

## Likelihood Explanation
The precondition is reachable by an unprivileged staker: a validator whose reward address is currently the proposer can withdraw its CNStaking down to zero via ordinary staking-contract transactions, while its consensus-liquidity (CL) pool is also drained to zero (also via ordinary withdrawal transactions from CL depositors) — all without any privileged/consensus role beyond being a normal staker/CL participant. Because AddressBook/CL registration removal is not synchronous with stake withdrawal, the window in which `CLStakingInfo != nil` but both stakes are zero is plausible, especially right after full unstaking but before deregistration.

## Recommendation
Add an explicit guard in `consolidatedNode.Split` (or in its two callers `specWithProposerAndFunds`/`specWithProposerAndFundsFlex`) to check `totalAmount.Sign() == 0` before dividing, falling back to routing the entire amount to the CN (or to a designated fund) instead of performing the division. This mirrors the C4 recommendation of adding a "if total == 0, return/skip" guard rather than dividing unconditionally.

## Proof of Concept
1. A validator `V` registers CL for its node (`CLStakingInfo` created, `CLPoolAddr` set) and is elected/round-robin selected as proposer for block `N`.
2. `V`'s CNStaking balance is withdrawn to `0` (via normal CNStaking contract withdrawal transactions) and the CL pool backing `V` is also fully withdrawn to `0` (via normal CL withdrawal transactions), while `V` remains registered as a CL-participating node (`CLStakingInfo != nil`) and is still the block's `Rewardbase` for block `N` before deregistration processes.
3. During `FinalizeState` for block `N`, `getDeferredRewardFullKore`/`Flex` calls `specWithProposerAndFunds(Flex)`, which finds `cn.RewardAddr == config.Rewardbase` and `cn.CLStakingInfo != nil`, then calls `cn.Split(proposer)`.
4. Inside `Split`, `totalAmount = 0 (StakingAmount) + 0 (CLStakingAmount) = 0`, and `clAmount.Div(clAmount, totalAmount)` panics with "integer divide by zero", crashing block finalization on every node that processes block `N`. [1](#0-0) [6](#0-5)

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

**File:** kaiax/reward/impl/getter.go (L514-530)
```go
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
```

**File:** kaiax/reward/impl/getter.go (L566-592)
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

	newSpec.IncRecipient(config.Rewardbase, proposer)
	return newSpec
}
```

**File:** kaiax/reward/impl/getter.go (L622-638)
```go
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

**File:** kaiax/reward/README.md (L80-84)
```markdown
- **Prague rule (KIP-226)**: The rule since the KIP-226 hardfork with `istanbul.policy == 2` and `reward.deferredtxfee = true`.
  - MR: The consensus liquidity is introduced. The validator's total staking amount is summed up with the KAIA staked in consensus liquidity.
    - If the validator has staked more than `reward.minstake` in staking-only (CNStaking) contract, the validator's total staking amount will be summed up with the consensus liquidity.
    - In this case, the reward between staking-only and consensus liquidity will be distributed proportionally to their staking amounts. The consensus liquidity portion is rounded down, and the remainder goes to the validator's AddressBook reward address.
    - Otherwise, validator will not be eligible for rewards.
```
