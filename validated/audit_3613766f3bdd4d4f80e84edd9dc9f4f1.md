Based on my research, I found a strong analog to the SynthVault reward-gaming report: the `CLStakingAmount` used in Kaia's staking-reward split is derived directly from a consensus-liquidity pool's **native KAIA balance**, which — like the SynthVault spot-price weight — is a spot-measured value fed into a proportional reward-distribution formula.

### Title
Consensus Liquidity (CL) staking weight is derived from a spot KAIA balance, allowing reward-share inflation via transient deposits - (File: `kaiax/staking/impl/getter.go`, `kaiax/reward/impl/getter.go`)

### Summary
`StakingInfo.CLStakingInfo.CLStakingAmount` — the amount used to proportionally split KIP-226/Flex staking rewards between a validator's CN and its CL pool — is computed straight from `CLPoolAddr`'s on-chain balance at the staking snapshot block [1](#0-0) , rather than from a time-weighted or lock-committed amount. This is structurally the same pattern as the SynthVault bug: a reward "weight" computed from an instantaneously readable value that is cheap to manipulate around a single measurement point.

### Finding Description
`GetStakingInfo`/`getFromState` reads CL info via `MultiCallDPStakingInfo`, filling `CLStakingInfo.CLStakingAmount` from the pool's raw balance (see test fixtures where `CLPool1..3` balances are set directly and mirrored 1:1 into `CLStakingAmount`) [2](#0-1) . This snapshot, taken once per staking interval, is then used by the reward module to split staking rewards between the CN and the CL pool address in proportion to `StakingAmount : CLStakingAmount`: [3](#0-2) 

and to compute eligibility/excess-stake weighting for both the Kore and Flex reward paths: [4](#0-3) [5](#0-4) 

If depositing into a `CLPoolAddr` is permissionless (unprivileged transaction sender sends KAIA to the pool) and the pool's balance is read verbatim as `CLStakingAmount` without any lock-up, cooldown, or time-weighted averaging, an attacker can:
1. Identify the upcoming staking-info source block (staking info is captured once per `reward.stakingupdateinterval`, e.g. every 60 blocks per the observed governance defaults).
2. Send/hold a large amount of KAIA in the target `CLPoolAddr` at that exact snapshot block, inflating `CLStakingAmount` for the entire subsequent interval.
3. Withdraw the funds immediately after the snapshot is taken, while continuing to receive an inflated share of `assignStakingRewards`/`assignStakingRewardsFlex` payouts and the CN/CL split from `consolidatedNode.Split` for the whole interval.

This exactly parallels the referenced report's root cause: a reward-weight input (spot balance/spot price) that is not economically bound to sustained collateralization, while the reward payout is measured against a much larger, unrelated pool of protocol funds (minted block rewards / fee pool), making the manipulation profitable when the transient deposit cost is lower than the extra reward captured over the interval.

### Impact Explanation
If exploitable, this allows redirection of validator staking rewards (minted KAIA + fee-based staking rewards) toward an attacker-controlled CL pool without providing real, sustained economic security to the network — a direct reward-redirection/inflation-of-share issue matching the "Medium/High, reward redistribution" class explicitly allowed by the validation rules. The attacker effectively "borrows" a large balance for a single snapshot block to claim a full staking-interval's worth of disproportionate rewards.

### Likelihood Explanation
Likelihood depends on two facts I could not fully verify from the indexed code:
1. Whether `CLPoolAddr` deposits are genuinely permissionless and instantaneous (no lock/vesting) for arbitrary senders.
2. The exact length of the staking-info measurement window (`reward.stakingupdateinterval`) and whether the balance is sampled once (spot) versus averaged.

Given that the getter code reads the balance directly at a single header/state root with no apparent time-weighting (`clRes.StakingAmounts[i]` divided by `params.KAIA` with no averaging logic in `parseCallResult`/`parsePermissionlessCallResult`) [1](#0-0) , and the value directly and proportionally determines reward capture, the mechanism appears structurally susceptible unless the actual CL pool contract (not included in the indexed code, likely a proprietary/system contract) enforces deposit lock-ups or reads a TWAP internally. This is the same "recommended mitigation" gap (TWAP vs spot) called out in the original SynthVault report.

### Recommendation
- Verify (and if absent, enforce) that the CL pool contracts underlying `CLPoolAddr` require deposits to be locked for at least one full `reward.stakingupdateinterval` before being counted toward `CLStakingAmount`, or compute a time-weighted average balance over the interval rather than a point-in-time balance.
- Consider requiring a minimum holding duration/cooldown for CL pool withdrawals so that inflating `CLStakingAmount` cannot be done cheaply within a single snapshot block.
- Add explicit invariant checks/logging in `kaiax/staking` when `CLStakingAmount` changes drastically block-to-block relative to the staking interval, to detect flash-style manipulation attempts.

### Proof of Concept
Not independently reproducible from the indexed codebase alone: the CL pool contract implementation (deposit/withdraw semantics, lock-up rules) is not present in the indexed files (only mocks like `CLRegistryMock.sol` and `WrappedKaiaMock` are visible, and these are test-only stand-ins) [6](#0-5) . A concrete PoC would require:
1. Confirming the real `CLPool`/`WrappedKaia` deposit contract's lock-up behavior (not available in the index).
2. Sending a large KAIA transfer to a target `CLPoolAddr` immediately before the `sourceBlockNum` used by `kaiax/staking.GetStakingInfo`.
3. Observing the resulting `CLStakingAmount` and reward split via `kaia_getStakingInfo`/`kaia_getReward` APIs over the following interval, then withdrawing.

Given the inability to confirm the CL pool's deposit/lock semantics from the available index, I recommend a Devin session with full repository/contract access to verify the actual `CLPool` contract logic (likely a separate contract package not indexed here) before treating this as a confirmed, exploitable vulnerability rather than a structural analog.

### Citations

**File:** kaiax/staking/impl/getter.go (L290-302)
```go
	// Collect the CL registry results to StakingInfo fields.
	// If there is no CL registry result, it will be nil.
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

**File:** kaiax/staking/impl/getter_test.go (L147-190)
```go
			common.HexToAddress("0x0000000000000000000000000000000000000e00"): { // CLPool1
				Balance: new(big.Int).Mul(big.NewInt(20_000_000), big.NewInt(params.KAIA)),
			},
			common.HexToAddress("0x0000000000000000000000000000000000000e01"): { // CLPool2
				Balance: new(big.Int).Mul(big.NewInt(23_000_000), big.NewInt(params.KAIA)),
			},
			common.HexToAddress("0x0000000000000000000000000000000000000e02"): { // CLPool3
				Balance: new(big.Int).Mul(big.NewInt(30_000_000), big.NewInt(params.KAIA)),
			},
		}
		expected = &staking.StakingInfo{
			SourceBlockNum: 0,
			NodeIds: []common.Address{
				common.HexToAddress("0x0000000000000000000000000000000000000F00"),
				common.HexToAddress("0x0000000000000000000000000000000000000F03"),
			},
			StakingContracts: []common.Address{
				common.HexToAddress("0x0000000000000000000000000000000000000F01"),
				common.HexToAddress("0x0000000000000000000000000000000000000f04"),
			},
			RewardAddrs: []common.Address{
				common.HexToAddress("0x0000000000000000000000000000000000000f02"),
				common.HexToAddress("0x0000000000000000000000000000000000000f05"),
			},
			KIFAddr:        common.HexToAddress("0x0000000000000000000000000000000000000F06"),
			KEFAddr:        common.HexToAddress("0x0000000000000000000000000000000000000f07"),
			StakingAmounts: []uint64{42_000_000, 99_000_000},
			CLStakingInfos: staking.CLStakingInfos{
				{
					CLNodeId:        common.HexToAddress("0x0000000000000000000000000000000000000F00"),
					CLPoolAddr:      common.HexToAddress("0x0000000000000000000000000000000000000e00"),
					CLStakingAmount: 20_000_000,
				},
				{
					CLNodeId:        common.HexToAddress("0x0000000000000000000000000000000000000F03"),
					CLPoolAddr:      common.HexToAddress("0x0000000000000000000000000000000000000e01"),
					CLStakingAmount: 23_000_000,
				},
				{
					CLNodeId:        common.HexToAddress("0x0000000000000000000000000000000000000F06"),
					CLPoolAddr:      common.HexToAddress("0x0000000000000000000000000000000000000e02"),
					CLStakingAmount: 30_000_000,
				},
			},
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

**File:** kaiax/reward/impl/getter.go (L421-483)
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

**File:** contracts/testing/reward/CLRegistryMock.sol (L1-79)
```text
// Copyright 2024 The klaytn Authors
// This file is part of the klaytn library.
//
// The klaytn library is free software: you can redistribute it and/or modify
// it under the terms of the GNU Lesser General Public License as published by
// the Free Software Foundation, either version 3 of the License, or
// (at your option) any later version.
//
// The klaytn library is distributed in the hope that it will be useful,
// but WITHOUT ANY WARRANTY; without even the implied warranty of
// MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
// GNU Lesser General Public License for more details.
//
// You should have received a copy of the GNU Lesser General Public License
// along with the klaytn library. If not, see <http://www.gnu.org/licenses/>.

pragma solidity ^0.4.24;

import "./AddressBookMock.sol";

/**
 * @title CLRegistryMockTwoCL
 */

contract CLRegistryMockThreeCL is MockValues {
    function getAllCLs()
        external
        view
        returns (address[] memory, uint256[] memory, address[] memory)
    {
        address[] memory nodeIds = new address[](3);
        uint256[] memory gcIds = new uint256[](3);
        address[] memory clPools = new address[](3);

        nodeIds[0] = nodeId0;
        nodeIds[1] = nodeId1;
        nodeIds[2] = nodeId2; // Doesn't exist in AddressBookMockTwoCN

        gcIds[0] = 1;
        gcIds[1] = 2;
        gcIds[2] = 3;

        clPools[0] = 0x0000000000000000000000000000000000000e00;
        clPools[1] = 0x0000000000000000000000000000000000000e01;
        clPools[2] = 0x0000000000000000000000000000000000000e02;

        return (nodeIds, gcIds, clPools);
    }
}

contract RegistryMockForCL {
    // This is a mock implementation of the Registry contract
    // It returns a fixed address for the CLRegistryMockThreeCL address
    function getActiveAddr(string name) external view returns (address) {
        address clRegistryAddr = 0x0000000000000000000000000000000000000Ff0;
        address wrappedKaiaAddr = 0x0000000000000000000000000000000000000Ff1;

        if (keccak256(name) == keccak256("CLRegistry")) {
            return clRegistryAddr;
        } else if (keccak256(name) == keccak256("WrappedKaia")) {
            return wrappedKaiaAddr;
        }
        return address(0);
    }
}

contract RegistryMockZero {
    // This is a mock implementation of the Registry contract
    // It returns a fixed address for the CLRegistryMockThreeCL address
    function getActiveAddr(string name) external view returns (address) {
        return address(0);
    }
}

contract WrappedKaiaMock {
    function balanceOf(address account) external view returns (uint256) {
        return account.balance;
    }
}
```
