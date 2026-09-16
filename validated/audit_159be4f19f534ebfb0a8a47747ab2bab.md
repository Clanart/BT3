## Title
Division by zero (panic) in consensus liquidity reward split when both CN and CL staking amounts are zero - (File: kaiax/staking/staking_info.go)

### Summary
`consolidatedNode.Split` divides a reward amount between the CNStaking and consensus-liquidity (CL) staking pools using `cnAmount + clAmount` as the denominator, without checking that this sum is non-zero. This mirrors the TFLite `SVDF` bug class (division by an attacker/state-influenceable value that can legitimately become zero), except here the "denominator" is `totalAmount` derived from staking amounts rather than `params->rank`.

### Finding Description
`Split` computes: [1](#0-0) 

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
	clAmount = clAmount.Div(clAmount, totalAmount)   // <- panics if totalAmount == 0
	...
}
```

`totalAmount` is only guarded against being zero in the callers that gate on `cnTotalStakingAmount > minStake` (`assignStakingRewards`) — but the proposer-reward path calls `Split` unconditionally whenever `cn.CLStakingInfo != nil`, without any minimum-stake check: [2](#0-1) 

```go
cns := si.ConsolidatedNodes()
for _, cn := range cns {
	if cn.RewardAddr != config.Rewardbase {
		continue
	}
	if cn.CLStakingInfo == nil {
		break
	}
	cnAmount, clAmount := cn.Split(proposer)   // no staking-amount guard here
	...
}
```

If the current block proposer's consolidated node has `StakingAmount == 0` (e.g., it fully withdrew its CN stake but remains registered in the AddressBook / NodeIds list) **and** its registered `CLStakingInfo.CLStakingAmount == 0` (e.g., the CL pool was fully unstaked but the CL registry entry was not removed), then `totalAmount` is `0`, and `big.Int.Div` panics with a division-by-zero runtime error (Go's `math/big` explicitly panics on division by zero, unlike returning an error).

### Impact Explanation
This function is invoked from the reward-calculation path (`specWithProposerAndFundsFlex` / the analogous non-flex `specWithProposerAndFunds`), which is used both by `GetDeferredReward`/`GetBlockReward` (RPC-exposed via `kaia_getReward`) and by block finalization reward distribution (`FinalizeState`), per the module's own documentation of its role in `BlockState`/finalization. A panic during block finalization on all honest nodes attempting to process the block would halt the chain (denial of service) rather than merely crash an RPC call; at minimum, it crashes the `kaia_getReward`/`kaia_getRewardsAccumulated` public RPC handlers when queried for such a block, and in the state-transition path it risks aborting block processing for every node computing that block's rewards. This is reachable without any special privileges: any validator can normally reduce its CN stake to zero via ordinary CNStaking withdrawal while remaining in the AddressBook, and a CL pool can be fully unstaked while the CLRegistry association persists — both are legitimate contract operations, not attacker-controlled malformed data, similar in spirit to the TFLite bug where a legitimately-parsed but degenerate model parameter (`rank = 0`) triggers the crash.

### Likelihood Explanation
Requires a specific but plausible governance/economic state: a proposer whose consolidated node has zero CN stake and a registered CL staking association with zero CL stake, occurring exactly when it is selected as proposer (round-robin proposer policy does not require minimum stake, so a zero-staked node can still become proposer). This is a state-configuration edge case rather than a routinely-hit path, but it is deterministically triggerable by any staker/validator operator through ordinary unstaking actions, with no cryptographic or consensus-message manipulation needed.

### Recommendation
Add a zero-check before dividing in `consolidatedNode.Split`, e.g., return `(amount, big.NewInt(0))` (or split evenly/skip CL) when `totalAmount.Sign() == 0`, matching the guard pattern already used in `assignStakingRewards`/`assignStakingRewardsFlex`. Additionally, gate the proposer-reward `Split` call in `specWithProposerAndFundsFlex`/`specWithProposerAndFunds` on `totalAmount > 0` before calling `Split`.

### Proof of Concept
1. Register a validator node in AddressBook with `CNStaking` and a `CLStakingInfo` association (post-Prague).
2. Fully unstake/withdraw both the CNStaking contract balance (`StakingAmount` → 0) and the CL pool stake (`CLStakingAmount` → 0), while keeping both registrations (NodeId/AddressBook entry and CL registry entry) intact.
3. Wait until this node's `RewardAddr` becomes the block proposer for a block being finalized/queried (`kaia_getReward` for that block, or block finalization itself under Prague rules with `istanbul.policy == 2`).
4. `getDeferredRewardFull` → `specWithProposerAndFundsFlex`/`specWithProposerAndFunds` locates the proposer's `consolidatedNode`, sees `CLStakingInfo != nil`, and calls `cn.Split(proposer)`.
5. Inside `Split`, `totalAmount = 0 (StakingAmount) + 0 (CLStakingAmount) = 0`; `clAmount.Div(clAmount, totalAmount)` panics with "division by zero," crashing the RPC handler or block-processing goroutine.

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

**File:** kaiax/reward/impl/getter.go (L572-588)
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
