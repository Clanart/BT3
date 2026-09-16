### Title
Reward-share manipulation via unauthorized native token transfers to CnStaking contracts - ([File: kaiax/staking/impl/getter.go])

### Summary
`kaiax/staking` derives each validator's `StakingAmount` — the figure used to proportionally distribute block rewards — directly from the on-chain **native token balance** of the validator's staking contract address, not from an internally tracked/locked "staked" accounting variable. Because `CnStakingV4` (and legacy CN staking contracts) expose a `payable receive()` fallback with no access control, **any unprivileged transaction sender can send KAIA directly to a staking contract address** and thereby inflate the `StakingAmount` figure that the reward module later uses to compute that validator's share of the per-block/per-interval staking reward pool. This mirrors the reported Lido issue, where anyone could send ETH to `LidoMevTxFeeVault`, which was then folded into a value used for proportional accounting (share price) without an authorization check.

### Finding Description
`GetStakingInfo` → `getFromState` reads `StakingAmounts` via a `MultiCall` to the AddressBook/AddressBookV2, and the resulting numbers correspond 1:1 to the raw account balance of each staking contract, as shown directly in the test fixtures where `GenesisAlloc[...].Balance` for a staking address becomes the corresponding `StakingAmounts` entry: [1](#0-0) 

The staking amount is not filtered against a distinct "locked/staking" ledger variable inside the staking contract; it is the contract's total balance as observed by the state trie at the snapshot block: [2](#0-1) 

`CnStakingV4`'s ABI declares an unconditional `payable receive()`, meaning KAIA can be sent to the contract by any caller, independent of the `delegate()` staking flow: [3](#0-2) 

This inflated `StakingAmounts` figure is subsequently used, unmodified, to compute each validator's proportional share of the block/staking reward pool in `assignStakingRewards` and `assignStakingRewardsFlex`: [4](#0-3) [5](#0-4) 

Because the `StakingInfo` snapshot used for a block is taken from a *historic* block state (the previous block after the Kaia hardfork, or the beginning of the previous staking interval before it), an attacker with knowledge of the snapshot block can time a deposit to a target staking contract (their own, to boost their own share, or someone else's, to dilute a competitor and shift the ratio) so that the inflated balance is captured at the snapshot point and used for reward computation: [6](#0-5) 

### Impact Explanation
The staking reward split (`assignStakingRewards`/`assignStakingRewardsFlex`) is a zero-sum proportional distribution among all eligible validators (`stakersReward`/`budget` is fixed; each validator's cut is `excess * budget / totalExcess`). Since `StakingAmount` can be manipulated by any address sending plain KAIA transfers (not going through `delegate()`), an attacker can:
- Artificially raise a validator's `excess` stake (over `minStake`/`StakingRewardThreshold`) to redirect a larger portion of the fixed staking reward pool toward that validator's `RewardAddr`, diluting the legitimate share of every other validator.
- If the inflated balance is later withdrawable (outside the proper `unstaking`/`approveStakingWithdrawal` locked-stake flow — this specific point could not be fully confirmed since the `CnStakingV4.sol` source was not available in the index, only bindings), the attacker could recover the sent KAIA after the snapshot, effectively renting a large stake for a single measurement without long-term commitment.

This is a concrete reward-redirection/value-movement issue reachable purely by a plain-value transaction from an unprivileged sender to a public system contract address, matching the "reward redirection" acceptance criterion.

### Likelihood Explanation
Medium. The attack requires:
1. Knowledge of the exact snapshot block used to compute `StakingInfo` (deterministic and documented, see `SourceNum` formula above), which is fully public information.
2. A plain-value transfer transaction to a known, public `CnStaking*` contract address (all such addresses are public via the AddressBook contract).
No special privileges, governance access, or validator-only rights are required — only the ability to submit an ordinary transaction with value before the snapshot block. The complexity is timing the transaction to land in the correct block, which is achievable by any actor monitoring the chain.

### Recommendation
- Decouple the reward-distribution `StakingAmount` from the raw native balance of the staking contract. Use an internally tracked, access-controlled "staked" ledger variable (only updated through `delegate()`/authorized staking flows) rather than `address(this).balance`.
- Alternatively, have `CnStaking*` contracts reject or explicitly sweep/burn unsolicited plain KAIA transfers that do not go through the `delegate()` accounting path, so `balance` and "actually staked" amount cannot diverge.
- Add validation in `kaiax/staking`'s `getFromState`/`parseCallResult` to cross-check the AddressBook-reported staking amount against the contract's own internal "staking" accounting variable (already exposed via the `staking()` view function in the ABI) rather than trusting raw balance.

### Proof of Concept
1. Identify a validator's `CnStakingV4` proxy address from the public `AddressBook`/`AddressBookV2` contract.
2. Determine the upcoming `StakingInfo` snapshot block using the documented `SourceNum` rule (previous block post-Kaia HF, or start of previous `StakingInterval` pre-Kaia HF).
3. Submit an ordinary value-transfer transaction (no calldata) sending KAIA to the target `CnStakingV4` address; the contract's unconditional `payable receive()` accepts it with no authorization check.
4. At the snapshot block, `kaiax/staking.GetStakingInfo` reads the inflated balance as `StakingAmounts[i]` via the MultiCall/AddressBook path.
5. `kaiax/reward`'s `assignStakingRewards`/`assignStakingRewardsFlex` computes the validator's `excess` stake using this inflated figure, causing it to receive a disproportionately larger cut of the fixed staking reward pool at the expense of other validators.

Note: I was unable to locate the `CnStakingV4.sol` source in the indexed codebase (only the compiled Go bindings were available), so I could not fully verify whether unsolicited `receive()` deposits are later withdrawable outside the locked-stake accounting, or whether any additional filtering exists between raw balance and reported `StakingAmount` at the AddressBook/MultiCall layer. A Devin session with full repository access would be needed to confirm these details definitively.

### Citations

**File:** kaiax/staking/impl/getter_test.go (L83-110)
```go
			common.HexToAddress("0x0000000000000000000000000000000000000F01"): { // staking1
				Balance: new(big.Int).Mul(big.NewInt(42_000_000), big.NewInt(params.KAIA)),
			},
			common.HexToAddress("0x0000000000000000000000000000000000000f04"): { // staking2
				Balance: new(big.Int).Mul(big.NewInt(99_000_000), big.NewInt(params.KAIA)),
			},
		}
		config = testPragueForkChainConfig(nil)

		// Addresses are already stored in AddressBookMock.sol:AddressBookMockTwoCN
		// The balances are given at the GenesisAlloc above
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
```

**File:** kaiax/staking/impl/getter.go (L101-120)
```go
// Efficiently read addresses and balances from the AddressBook in one EVM call.
// Works by temporarily injecting the MultiCallContract to a copied state.
func (s *StakingModule) getFromState(header *types.Header, statedb *state.StateDB) (*staking.StakingInfo, error) {
	isForPrague := s.ChainConfig.IsPragueForkEnabled(new(big.Int).Add(header.Number, common.Big1))
	isForPermissionless := s.ChainConfig.IsPermissionlessForkEnabled(new(big.Int).Add(header.Number, common.Big1))
	num := header.Number.Uint64()

	// Bail out if AddressBook is not installed.
	// This is a common case for private nets.
	if statedb.GetCode(system.AddressBookAddr) == nil {
		logger.Trace("AddressBook not installed", "sourceNum", num)
		return emptyStakingInfo(num), nil
	}

	// Now we're safe to call the MultiCall contract.
	contract, err := system.NewMultiCallContractCaller(statedb, s.Chain, header)
	if err != nil {
		return nil, staking.ErrMultiCallCall(err)
	}

```

**File:** contracts/bindings/cnstakingv4/CnStakingV4.go (L32-36)
```go
// CnStakingV4MetaData contains all meta data concerning the CnStakingV4 contract.
var CnStakingV4MetaData = &bind.MetaData{
	ABI: "[{\"type\":\"constructor\",\"inputs\":[],\"stateMutability\":\"nonpayable\"},{\"type\":\"receive\",\"stateMutability\":\"payable\"},{\"type\":\"function\",\"name\":\"CONTRACT_TYPE\",\"inputs\":[],\"outputs\":[{\"name\":\"\",\"type\":\"string\",\"internalType\":\"string\"}],\"stateMutability\":\"view\"},{\"type\":\"function\",\"name\":\"STAKE_LOCKUP\",\"inputs\":[],\"outputs\":[{\"name\":\"\",\"type\":\"uint256\",\"internalType\":\"uint256\"}],\"stateMutability\":\"view\"},{\"type\":\"function\",\"name\":\"VERSION\",\"inputs\":[],\"outputs\":[{\"name\":\"\",\"type\":\"uint256\",\"internalType\":\"uint256\"}],\"stateMutability\":\"view\"},{\"type\":\"function\",\"name\":\"approveStakingWithdrawal\",\"inputs\":[{\"name\":\"_to\",\"type\":\"address\",\"internalType\":\"address\"},{\"nam ... (truncated)
	Bin: "0x6080604052348015600e575f80fd5b5060156019565b60c9565b7ff0c57e16840df040f15088dc2f81fe391c3923bec73e23a9662efc9c229c6a00805468010000000000000000900460ff161560685760405163f92ee8a960e01b815260040160405180910390fd5b80546001600160401b039081161460c65780546001600160401b0319166001600160401b0390811782556040519081527fc7f505b2f371ae2175ee4913f4499e1f2633a7b5936321eed1cdaeb6115181d29060200160405180910390a15b50565b611bec806100d65f395ff3fe608060405260043610610155575f3560e01c8063715018a6116100be578063c4d66de811610078578063c4d66de81461039f578063c804b115146103be578063c89e4361146103dd578063d2569eb9146103e5578063e1a12d3514610411578063f2fde38b14610425578063ffa1ad7414610444575f80fd5b8063715018a61461030b578063725c05031461031f5780638cf57cb91461034e5780638da5cb5b1461036257806396106ae414610376578063a006e90 ... (truncated)
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

**File:** kaiax/staking/README.md (L10-20)
```markdown
- When processing a block at `num`, the StakingInfo from a historic block state is used and the historic block (i.e. source block) is determined by the following:

  - If `num` is before Kaia hardfork, then StakingInfo is drawn from the beginning of the previous staking interval. Note that if the `num` is a multiple of StakingInterval, the staking info is drawn from two epochs ahead (e.g. in the example below, the staking info at block 3000 is drawn from block 1000).
    ```go
    SourceNum(num) = RoundDown(num - 1, StakingInterval) - StakingInterval
    RoundDown(n, p) = n - (n % p)
    ```
  - If `num` is after Kaia hardfork, then StakingInfo is drawn from the previous block.
    ```go
    SourceNum(num) = num - 1
    ```
```
