### Title
Suspended validator keeps earning consensus rewards because reward eligibility is keyed only on `NodeState`, not the `Suspended` flag - (File: `kaiax/valset/types.go`, `kaiax/staking/impl/getter.go`)

### Summary
`Suspended` is a per-node flag that is meant to strip a validator of consensus privileges: it is subtracted from the committee/qualified set in `committeeWithFallback` [1](#0-0) , and its own test explicitly documents that "Suspended is informational, not a violation exemption" (i.e., it is a penalty state) [2](#0-1) . However, reward eligibility is computed purely from `NodeState.IsRewardEligible()`, which only checks `s == ValActive` and never inspects `Suspended` [3](#0-2) . Because `Suspended` is a boolean overlay on top of `ValActive` rather than a distinct `NodeState`, a validator can be `State: ValActive, Suspended: true` and still pass `IsRewardEligible()`.

### Finding Description
The staking module builds the `StakingInfo` used for reward distribution by filtering AddressBookV2 profiles on `NodeState.IsRewardEligible()` only: [4](#0-3) 
This filtered `StakingInfo` (containing `RewardAddrs`/`StakingAmounts`) is exactly what `assignStakingRewards`/`assignStakingRewardsFlex` in the reward module use to allocate the KIP-82/flex staking-reward budget proportionally to each validator's stake [5](#0-4) .

Meanwhile, `Suspended` is treated as an orthogonal penalty on `ValActive` nodes: `committeeWithFallback` computes `Committee = {ValActive} − {Suspended}` and is used to exclude suspended nodes from both `GetCommittee` and `GetQualifiedValidators` [6](#0-5) . A node with `Suspended: true` is thus deliberately barred from proposing/voting on blocks (the analog of PartyA being blocked by `notSuspended`), but its `State` remains `ValActive` — and `IsRewardEligible()` only checks `State`, so `parsePermissionlessCallResult` still includes that node's `RewardAddr`/stake in the reward-eligible set.

The suspending party in this system is the `Suspender` role in AddressBookV2 (`suspendValidator`/`unsuspendValidator`, exposed via the `kcn valops suspend-validator` CLI) [7](#0-6) [8](#0-7) . The validator's node-identity key is what gets suspended from consensus, but the reward flow pays out to the separate `RewardAddr`, an address fully controlled by the same validator operator — i.e., the operator uses a "counterparty" address it controls (its own reward address) to keep collecting the economic benefit that the suspension was supposed to withhold, exactly mirroring the PartyA/PartyB pattern in the source report (a restricted principal continues to receive value through an address/role that the restriction check does not cover).

### Impact Explanation
This is a reward-redirection / unauthorized value movement bug: a validator that has been suspended (presumably for misbehavior, e.g., PFS violations or governance-driven suspension) is excluded from block production and voting, yet continues to accumulate staking rewards proportional to its stake at the normal committee's expense. Over many blocks this represents ongoing incorrect distribution of newly minted KAIA and fees to an entity the protocol has explicitly decided should be penalized/excluded, undermining the incentive/penalty design of the permissionless validator set and effectively letting the suspended party extract value it should not be entitled to.

### Likelihood Explanation
No transaction crafting is required — the divergence is purely a consequence of normal `FinalizeState`/reward-distribution processing once any validator is suspended while its `State` is still `ValActive`. Since `Suspended` is an independent bit that does not change `State`, any suspension event (which is expected to happen periodically for genuine validator misbehavior) triggers this behavior deterministically on every reward-distribution block, with no attacker interaction needed. Likelihood is high given suspension is a designed, regularly-exercised part of validator lifecycle management.

### Recommendation
Make `IsRewardEligible()` (or the call sites in `parsePermissionlessCallResult` / `getFromState`) also check the `Suspended` flag, e.g. change the filter to `valset.NodeState(p.State).IsRewardEligible() && !p.Suspended`, or fold "reward eligibility" into the same set computation used for `getQualifiedValidators`/`committeeWithFallback` so that suspended validators are consistently excluded from both consensus duties and reward payouts.

### Proof of Concept
1. A validator node `V` (associated `RewardAddr = R`, controlled by the same operator) is registered in AddressBookV2 with `State = ValActive`.
2. The Suspender calls `suspendValidator(V)` [9](#0-8) , setting `Suspended = true` for `V`, without changing `State`.
3. `committeeWithFallback` now excludes `V` from `Committee`/`QualifiedValidators`, so `V` cannot propose or vote [1](#0-0) .
4. On the next reward-distribution block, `StakingModule.getFromState` → `parsePermissionlessCallResult` still includes `V`'s profile because `p.State == ValActive` satisfies `IsRewardEligible()` [4](#0-3) [3](#0-2) .
5. `assignStakingRewards`/`assignStakingRewardsFlex` computes `V`'s share of the staking-reward budget from its `StakingAmount` and pays it to `RewardAddr = R` [5](#0-4) , even though `V` performed no consensus work that epoch and was explicitly suspended.

**Uncertainty**: I could not find code that treats `Suspended` explicitly as a "should also lose rewards" concept (e.g., no README text or comment states the intended reward-eligibility semantics for `Suspended`). It is possible the protocol designers consider "suspension" to only pertain to consensus committee membership and intentionally leave reward eligibility untouched (e.g., because staking rewards are meant to compensate capital lock-up rather than active participation). Confirming the intended design would require checking KIP-286/287 specs or governance documentation not present in the indexed code, so this should be validated against the KIP-286 specification before treating it as a confirmed bug rather than a design choice.

### Citations

**File:** kaiax/valset/impl/getter_permissionless.go (L33-59)
```go
// committeeWithFallback returns the committee at the given block.
// Committee = {VA} - {Suspended}.
//
// Safety fallback: when len(committee)=0 due to {ValActive} ⊆ SuspendedSet, fallback to all VA.
func committeeWithFallback(nodes valset.NodeMap) (committee valset.NodeMap, fellBack bool) {
	committee = nodes.Committee()
	if len(committee) > 0 {
		return committee, false
	}

	// ignore SuspendedSet
	return nodes.FilterByState(valset.ValActive), true
}

// getQualifiedValidators: qualified = committee = {VA} − {Suspended} (with safety fallback).
func (v *ValsetModule) getQualifiedValidators(num uint64) (*valset.AddressSet, error) {
	nodes, err := v.getNodes(num)
	if err != nil {
		return nil, err
	}
	committee, fellBack := committeeWithFallback(nodes)
	if fellBack && v.lastSuspendFallbackLog != num {
		logger.Warn("all ValActive are suspended, ignoring suspended set for committee", "num", num)
		v.lastSuspendFallbackLog = num
	}
	return valset.NewAddressSet(committee.Addresses()), nil
}
```

**File:** kaiax/valset/impl/transition_context_test.go (L355-375)
```go
// TestApplyViolationTransition_SuspendedNotExempt verifies that suspended
// ValActive validators are subject to the same violation rules as non-suspended
// ones — Suspended is informational, not a violation exemption.
func TestApplyViolationTransition_SuspendedNotExempt(t *testing.T) {
	m := NodeMap{
		addr1: {State: ValActive, StakingAmount: belowMinStake, Suspended: true},  // minStake violation
		addr2: {State: ValActive, StakingAmount: aboveMinStake, Suspended: true},  // PFS severe
		addr3: {State: ValActive, StakingAmount: aboveMinStake, Suspended: false}, // no violation
	}
	ctx := violationCtx(t, ctxOpts{
		PfsThreshold:     2,
		MaxSlotAvailable: noSlotLimit,
		MinActiveCount:   noMinActive,
		PFS:              map[common.Address]uint64{addr2: 3},
		PfReport:         []common.Address{addr1},
	})
	out := ctx.applyViolationTransition(m)
	assert.Equal(t, ValExiting, out[addr1].State, "suspended + low staking → ValExiting")
	assert.Equal(t, ValExiting, out[addr2].State, "suspended + PFS severe → ValExiting")
	assert.Equal(t, ValActive, out[addr3].State, "non-suspended, no violation → unchanged")
}
```

**File:** kaiax/valset/types.go (L60-63)
```go
// IsRewardEligible returns true if the state is eligible for block rewards.
func (s NodeState) IsRewardEligible() bool {
	return s == ValActive
}
```

**File:** kaiax/staking/impl/getter.go (L197-209)
```go
	nodeIds := make([]common.Address, 0, len(profiles))
	stakingContracts := make([]common.Address, 0, len(profiles))
	rewardAddrs := make([]common.Address, 0, len(profiles))
	stakingAmounts := make([]uint64, 0, len(profiles))
	for i, p := range profiles {
		if !valset.NodeState(p.State).IsRewardEligible() {
			continue
		}
		nodeIds = append(nodeIds, p.NodeId)
		stakingContracts = append(stakingContracts, p.StakingContract)
		rewardAddrs = append(rewardAddrs, p.RewardAddress)
		stakingAmounts = append(stakingAmounts, new(big.Int).Div(amounts[i], big.NewInt(params.KAIA)).Uint64())
	}
```

**File:** kaiax/reward/impl/getter.go (L486-533)
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

**File:** contracts/bindings/addressbookv2/AddressBookV2.go (L1887-1899)
```go
// SuspendValidator is a paid mutator transaction binding the contract method 0xa41b6000.
//
// Solidity: function suspendValidator(address nodeId) returns()
func (_AddressBookV2 *AddressBookV2Transactor) SuspendValidator(opts *bind.TransactOpts, nodeId common.Address) (*types.Transaction, error) {
	return _AddressBookV2.contract.Transact(opts, "suspendValidator", nodeId)
}

// SuspendValidator is a paid mutator transaction binding the contract method 0xa41b6000.
//
// Solidity: function suspendValidator(address nodeId) returns()
func (_AddressBookV2 *AddressBookV2Session) SuspendValidator(nodeId common.Address) (*types.Transaction, error) {
	return _AddressBookV2.Contract.SuspendValidator(&_AddressBookV2.TransactOpts, nodeId)
}
```

**File:** cmd/kcn/README.md (L26-33)
```markdown
### Suspender role

These commands require the caller to hold the suspender role in AddressBookV2.

```
kcn valops suspend-validator --node-id <address>
kcn valops unsuspend-validator --node-id <address>
```
```
