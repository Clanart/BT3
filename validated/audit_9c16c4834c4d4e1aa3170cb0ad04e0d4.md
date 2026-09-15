### Title
Silent uint64 truncation of on-chain staking amounts in `parsePermissionlessCallResult` can corrupt reward distribution - ([File: kaiax/staking/impl/getter.go])

### Summary
The reported sudoswap `XykCurve` bug is caused by casting a `uint256` computation result to `uint128` without checking for overflow, so `newSpotPrice`/`newDelta` silently wrap instead of reverting. The analogous root cause exists in Kaia's staking/reward pipeline: `*big.Int` staking amounts read from on-chain state (CNStaking / CLStaking / AddressBookV2 balances) are converted to `uint64` via `.Uint64()` without ever checking `big.Int.IsUint64()`, exactly like the unchecked `uint128(...)` downcast in the report. Go's `big.Int.Uint64()` has the same "silently return only the low 64 bits" semantics as Solidity's unchecked `uint128(uint256)` cast.

### Finding Description
In `kaiax/staking/impl/getter.go`, `parsePermissionlessCallResult` computes each validator's effective stake as: [1](#0-0) 

```go
stakingAmounts = append(stakingAmounts, new(big.Int).Div(amounts[i], big.NewInt(params.KAIA)).Uint64())
```

and for consensus-liquidity staking: [2](#0-1) 

```go
CLStakingAmount: big.NewInt(0).Div(clRes.StakingAmounts[i], big.NewInt(params.KAIA)).Uint64(),
```

Both call `.Uint64()` directly on a `*big.Int` derived from a value returned by an arbitrary, permissionlessly-registrable staking/CL-pool contract (`amounts[i]` and `clRes.StakingAmounts[i]` come from `MultiCallStakingInfoPermissionless`/`MultiCallDPStakingInfo`, which call into user-deployed staking contracts under KIP-286/287). Just like the report's `uint128(spotPrice + inputValueWithoutFee)`, there is no bounds check before the narrowing conversion; `big.Int.Uint64()` (per its documented behavior) "returns the uint64 representation of x. If x cannot be represented in a uint64, the result is undefined" — in practice it returns `x mod 2^64`, i.e. a silent wraparound identical in spirit to the unsafe Solidity downcast in the report.

These `uint64` `StakingAmount`/`CLStakingAmount` fields then feed directly into the consensus-critical reward-splitting logic in `kaiax/reward/impl/getter.go`, e.g.: [3](#0-2) 

```go
totalExcessInt += cnTotalStakingAmount - minStake
...
excess := new(big.Int).SetUint64(cnTotalStakingAmount - minStake)
reward := new(big.Int).Div(new(big.Int).Mul(excess, stakersReward), totalExcess)
```

If a malicious/self-registered staking or CL-pool contract returns a `uint256` balance whose value (after dividing by `params.KAIA`, i.e. 10^18) still exceeds `math.MaxUint64` (~1.8×10^19), the truncation silently produces an arbitrary small (attacker-influenced) 64-bit remainder instead of the true stake. Because both the excess-stake numerator and `totalExcessInt` denominator are derived from this truncated value, an attacker who controls a staking/CL contract can choose a return value whose low 64 bits are large relative to other validators' honest values, skewing `assignStakingRewards`/`assignStakingRewardsFlex` reward allocation in their favor, or causing `totalExcessInt` itself to wrap to a tiny or zero value (leading to division anomalies or reward misallocation across the whole validator set).

### Impact Explanation
This directly maps to "reward redirection" in the accepted impact categories. Reward calculation is consensus-critical (`FinalizeState`/`getDeferredRewardFull*`) and every node computes the same corrupted numbers deterministically, so it would not cause a chain split, but it would let an attacker who deploys/controls a permissionless staking or CL-pool contract manipulate their own (or another validator's) share of the block's minting/fee reward pool — i.e., unauthorized value redirection away from honest stakers. This is Medium severity because it requires a privileged action (registering a staking contract able to report an extreme balance) which is gated by KIP-286/287 permissionless registration rather than a simple RPC/tx call, but it is reachable without validator/node compromise, purely through the staking-info-reading code path triggered every reward interval.

### Likelihood Explanation
Reaching the bug requires a validator/CN operator (or CL pool operator) to register a staking contract whose `balanceOf`/`getEffectiveStake`-style view returns a value ≥ `2^64 * 10^18` kei — an enormous but not technically forbidden number for an attacker-authored contract (it need not correspond to a real token balance; the contract's return value is fully attacker-controlled code, not an actual KAIA balance enforced by the protocol). Given the permissionless nature of KIP-286/287 staking registration, this is realistically reachable by any address willing to deploy a custom staking/CL contract and register as a candidate, without needing to compromise any node or validator key.

### Recommendation
Replace all unchecked `.Uint64()` conversions on attacker-influenced `*big.Int` staking amounts with explicit bounds checks (mirroring the report's recommended fix), e.g.:

```go
amt := new(big.Int).Div(amounts[i], big.NewInt(params.KAIA))
if !amt.IsUint64() {
    return nil, staking.ErrStakingAmountOverflow // or clamp/reject the candidate
}
stakingAmounts = append(stakingAmounts, amt.Uint64())
```

Apply the same guard to `CLStakingAmount` conversion, and audit `assignStakingRewards`/`assignStakingRewardsFlex` for additional unchecked `uint64` arithmetic (`totalExcessInt += ...`) that could itself overflow even with valid inputs summed across many validators.

### Proof of Concept
1. Under a chain with `PermissionlessCompatibleBlock` enabled, register a candidate node whose staking contract's balance-reporting call (used by `MultiCallStakingInfoPermissionless`) returns `amounts[i] = (2**64) * params.KAIA + 1` (a value fully controlled by the attacker's contract code, not an actual on-chain KAIA balance).
2. `parsePermissionlessCallResult` computes `new(big.Int).Div(amounts[i], big.NewInt(params.KAIA)).Uint64()`, which equals `1` instead of the intended enormous `2**64+...` value — but crucially, an attacker can choose the exact "reduced" numerator/denominator combination (e.g., returning `k*2^64*KAIA + X` for a chosen `X`) to make their post-truncation `StakingAmount` disproportionately larger than their true relative stake compared to other validators.
3. At the next reward-distribution block, `assignStakingRewards` computes `excess`/`totalExcessInt` using this manipulated `uint64`, shifting a larger share of `stakersReward` to the attacker's `RewardAddr` than their genuine economic stake would justify — confirmed by tracing the flow from `getFromState` → `parsePermissionlessCallResult` → `assignStakingRewards` shown in [4](#0-3)  and [5](#0-4) .

### Citations

**File:** kaiax/staking/impl/getter.go (L145-156)
```go
	// Permissionless: read from AddressBookV2 (effective stake, reward-eligible only).
	if isForPermissionless {
		res, err := contract.MultiCallStakingInfoPermissionless(callOpts)
		if err != nil {
			return nil, staking.ErrAddressBookCall(err)
		}
		clRes, err := readCLInfo()
		if err != nil {
			return nil, err
		}
		return parsePermissionlessCallResult(num, res.Profiles, res.StakingAmounts, res.KefAddr, res.KifAddr, res.KpfAddr, clRes)
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

**File:** kaiax/staking/impl/getter.go (L211-221)
```go
	var clStakingInfos staking.CLStakingInfos
	if len(clRes.NodeIds) > 0 {
		clStakingInfos = make(staking.CLStakingInfos, len(clRes.NodeIds))
		for i := range clRes.NodeIds {
			clStakingInfos[i] = &staking.CLStakingInfo{
				CLNodeId:        clRes.NodeIds[i],
				CLPoolAddr:      clRes.ClPools[i],
				CLStakingAmount: big.NewInt(0).Div(clRes.StakingAmounts[i], big.NewInt(params.KAIA)).Uint64(),
			}
		}
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
