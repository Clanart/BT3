### Title
Reward distribution trusts unvalidated CN staking-contract balance, allowing stake-amount inflation via plain native-token transfers - (File: kaiax/staking/impl/getter.go, kaiax/reward/impl/getter.go)

### Summary
The Sherlock report describes `MaverickRewardsAdapter._claimRewards` trusting an externally reported "earned" value (`IReward.earned()`), which an attacker can inflate for a single block via a flash-loan deposit and then claim before reversing the deposit. The root cause is "an unvalidated, externally-influenceable balance is used directly as the basis for a reward payout."

The same root-cause pattern exists in Kaia's staking/reward pipeline: `StakingAmounts`, the figure that directly determines each Consensus Node's (CN) share of block staking rewards, is derived from the **raw native-token balance** of the CN's staking contract at a snapshot block, rather than from a value that verifiably represents genuinely committed/locked stake.

### Finding Description
`kaiax/staking` reads consolidated CN staking information from the AddressBook/MultiCall contract, where the reported "amount" for legacy (pre-permissionless) staking contracts is simply the staking contract's account balance: [1](#0-0) [2](#0-1) 

This is confirmed by the module's own tests, which set up the "staking amount" purely by funding an address with a native-token `Balance`, with no on-chain "staked" bookkeeping involved: [3](#0-2) 

This `StakingAmounts` value is then fed directly into `kaiax/reward`'s proportional staking-reward allocation, which distributes minting rewards to validators strictly in proportion to `StakingAmount` (and `CLStakingAmount`) exceeding the minimum/threshold stake, with no independent verification of how long the funds have been committed: [4](#0-3) [5](#0-4) 

The CN staking contract (`CnStakingV4`) exposes a plain `receive()` payable function that accepts arbitrary native-token transfers from *any* sender, while formally *withdrawing* tracked stake requires going through an approval + timelock flow (`approveStakingWithdrawal` → wait until `withdrawableFrom` → `withdrawApprovedStaking`), gated by `STAKE_LOCKUP`: [6](#0-5) [7](#0-6) [8](#0-7) 

Because reward computation reads the contract's *raw balance* rather than a value gated by the same lockup/approval mechanism that governs withdrawal, any account can inflate a CN's apparent stake for the reward-determining snapshot block by simply sending KAIA to the staking contract address just before that block is captured as the reward "source block". Per the module's own documentation, after the Kaia hardfork the source block is simply the *previous block* (`SourceNum(num) = num - 1`), making the window for this manipulation extremely short and attacker-controllable: [9](#0-8) 

### Impact Explanation
An attacker (a CN operator, or anyone able to fund a CN's staking address, including via self-controlled infrastructure) can send a plain native-token transfer to a CN staking contract in block `N-1` to inflate that CN's `StakingAmount` used for the reward computation in block `N`, thereby claiming a disproportionately larger share of the staking-reward pool (`assignStakingRewards`/`assignStakingRewardsFlex`) than the CN's genuinely locked/committed stake would entitle it to. This is a direct analog of the flash-loan "earned" inflation: an externally-controllable, instantaneously-changeable balance is trusted as the basis for a value-distributing calculation. This constitutes reward redirection/theft of the honest validator set's rightful reward share.

### Likelihood Explanation
Reachable via a single, ordinary native-token transfer transaction from any account with funds — no privileged access, no protocol bug beyond the trust assumption, and exploitable every staking-interval/reward-eligibility snapshot (or every single block after the Kaia hardfork, since the source block is `num-1`). The only mitigating factor — which I could not fully verify from the indexed CnStakingV4 bytecode/bindings alone — is whether the excess (never formally "staked") balance can be swept back out without the full `STAKE_LOCKUP` delay; if it can (e.g., via an owner/admin sweep of un-staked balance), this is a fully reversible free reward-boost; if not, it still allows griefing/reward-skewing at the cost of temporarily locking self-owned capital for less than a full staking interval.

### Recommendation
Do not derive `StakingAmounts` from the raw account balance of the CN staking contract. Instead, have the staking module (and/or the AddressBook/CnStaking contracts) expose and use a value that is provably committed for at least one full staking interval — e.g., the amount that has passed through the same lockup/approval flow used for withdrawals, or a value recorded at the start of the staking interval and not mutable by arbitrary incoming transfers just before the snapshot block.

### Proof of Concept
1. Identify a CN's staking contract address (`StakingContracts[i]` from `AddressBook`/`StakingInfo`).
2. In block `N-1` (or, pre-Kaia-hardfork, at the start of the relevant staking interval), send a large native-token transfer directly to that staking contract address via its `receive()` function — no special permission required: [6](#0-5) 
3. At block `N`, `kaiax/staking.GetStakingInfo` reads the inflated balance as `StakingAmounts[i]` from the source block `N-1` state: [10](#0-9) 
4. `kaiax/reward.FinalizeState` at block `N` computes the deferred reward using this inflated `StakingInfo`, over-allocating staking rewards to the manipulated CN via `assignStakingRewards`: [11](#0-10) [4](#0-3) 
5. The attacker then attempts to reclaim the deposited excess balance; the extent to which this is possible without the `STAKE_LOCKUP` delay is the remaining open question that could not be confirmed from the available Go bindings, since the full Solidity source of `CnStakingV4` is not present in this index.

### Citations

**File:** kaiax/staking/impl/getter.go (L48-79)
```go
func (s *StakingModule) GetStakingInfo(num uint64) (*staking.StakingInfo, error) {
	isKaia := s.ChainConfig.IsKaiaForkEnabled(new(big.Int).SetUint64(num))
	sourceNum := sourceBlockNum(num, isKaia, s.stakingInterval)

	// Try cache first
	if si, ok := s.stakingInfoCache.Get(sourceNum); ok {
		return si.(*staking.StakingInfo), nil
	}

	// Only before Kaia, try the database
	if !isKaia {
		if si := ReadStakingInfo(s.ChainKv, sourceNum); si != nil {
			s.stakingInfoCache.Add(sourceNum, si)
			return si, nil
		}
	}

	// Read from the state
	si, err := s.getFromStateByNumber(sourceNum)
	if err != nil {
		return nil, err
	}

	// Only before Kaia, write to database
	if !isKaia {
		WriteStakingInfo(s.ChainKv, sourceNum, si)
	}

	// Cache it
	s.stakingInfoCache.Add(sourceNum, si)
	return si, nil
}
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

**File:** kaiax/staking/impl/getter.go (L159-179)
```go
	abRes, err := contract.MultiCallStakingInfo(callOpts)
	if err != nil {
		return nil, staking.ErrAddressBookCall(err)
	}

	var clRes clRegistryResult
	if isForPrague {
		clRes, err = readCLInfo()
		if err != nil {
			return nil, err
		}
	}

	return parseCallResult(
		num,
		abRes.TypeList,
		abRes.AddressList,
		abRes.StakingAmounts,
		clRes,
		abRes.SpareAddress,
	)
```

**File:** kaiax/staking/impl/getter_test.go (L74-127)
```go
func TestGetStakingInfo_Uncached(t *testing.T) {
	log.EnableLogForTest(log.LvlCrit, log.LvlWarn)
	var (
		db    = database.NewMemoryDBManager()
		alloc = blockchain.GenesisAlloc{
			system.AddressBookAddr: {
				Code:    system.AddressBookMockTwoCNCode,
				Balance: big.NewInt(0),
			},
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
			CLStakingInfos: nil,
		}
	)

	backend := backends.NewSimulatedBackendWithDatabase(db, alloc, config)

	// Test GetStakingInfo()
	mStaking := NewStakingModule()
	mStaking.Init(&InitOpts{
		ChainKv:     db.GetMiscDB(),
		ChainConfig: config,
		Chain:       backend.BlockChain(),
	})
	si, err := mStaking.GetStakingInfo(0)
	assert.NoError(t, err)
	assert.Equal(t, expected, si)
}
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

**File:** kaiax/reward/impl/getter.go (L486-534)
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
}
```

**File:** contracts/bindings/cnstakingv4/CnStakingV4.go (L239-254)
```go
// STAKELOCKUP is a free data retrieval call binding the contract method 0x96106ae4.
//
// Solidity: function STAKE_LOCKUP() view returns(uint256)
func (_CnStakingV4 *CnStakingV4Caller) STAKELOCKUP(opts *bind.CallOpts) (*big.Int, error) {
	var out []interface{}
	err := _CnStakingV4.contract.Call(opts, &out, "STAKE_LOCKUP")

	if err != nil {
		return *new(*big.Int), err
	}

	out0 := *abi.ConvertType(out[0], new(*big.Int)).(**big.Int)

	return out0, err

}
```

**File:** contracts/bindings/cnstakingv4/CnStakingV4.go (L332-361)
```go
// GetApprovedStakingWithdrawalInfo is a free data retrieval call binding the contract method 0x725c0503.
//
// Solidity: function getApprovedStakingWithdrawalInfo(uint256 _index) view returns(address to, uint256 value, uint256 withdrawableFrom, uint8 state)
func (_CnStakingV4 *CnStakingV4Caller) GetApprovedStakingWithdrawalInfo(opts *bind.CallOpts, _index *big.Int) (struct {
	To               common.Address
	Value            *big.Int
	WithdrawableFrom *big.Int
	State            uint8
}, error) {
	var out []interface{}
	err := _CnStakingV4.contract.Call(opts, &out, "getApprovedStakingWithdrawalInfo", _index)

	outstruct := new(struct {
		To               common.Address
		Value            *big.Int
		WithdrawableFrom *big.Int
		State            uint8
	})
	if err != nil {
		return *outstruct, err
	}

	outstruct.To = *abi.ConvertType(out[0], new(common.Address)).(*common.Address)
	outstruct.Value = *abi.ConvertType(out[1], new(*big.Int)).(**big.Int)
	outstruct.WithdrawableFrom = *abi.ConvertType(out[2], new(*big.Int)).(**big.Int)
	outstruct.State = *abi.ConvertType(out[3], new(uint8)).(*uint8)

	return *outstruct, err

}
```

**File:** contracts/bindings/cnstakingv4/CnStakingV4.go (L897-902)
```go
// Receive is a paid mutator transaction binding the contract receive function.
//
// Solidity: receive() payable returns()
func (_CnStakingV4 *CnStakingV4Transactor) Receive(opts *bind.TransactOpts) (*types.Transaction, error) {
	return _CnStakingV4.contract.RawTransact(opts, nil) // calldata is disallowed for receive function
}
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

**File:** kaiax/reward/impl/blockstate.go (L29-57)
```go
// Distribute the deferred rewards at the end of block processing.
func (r *RewardModule) FinalizeState(header *types.Header, state *state.StateDB, txs []*types.Transaction, receipts []*types.Receipt) error {
	if r.GovModule.GetParamSet(header.Number.Uint64()).ProposerPolicy == uint64(istanbul.WeightedRandom) && common.EmptyHash(header.Root) {
		qualified, err := r.ValsetModule.GetQualifiedValidators(header.Number.Uint64())
		if err != nil {
			return err
		}
		useRewardAddress := valset.NewAddressSet(qualified).Contains(r.NodeAddress)

		if rewardAddr := r.GetRewardAddress(header.Number.Uint64(), r.NodeAddress); useRewardAddress && rewardAddr != (common.Address{}) {
			header.Rewardbase = rewardAddr
			logger.Trace("Use reward address for nodeValidator", "header.Number", header.Number.Uint64(), "nodeAddress", r.NodeAddress, "rewardbase", header.Rewardbase)
		} else {
			logger.Trace("No reward address for nodeValidator. Use node's rewardbase.", "header.Number", header.Number.Uint64(), "nodeAddress", r.NodeAddress, "rewardbase", header.Rewardbase)
		}
	}

	spec, err := r.GetDeferredReward(header, txs, receipts)
	if err != nil {
		return err
	}
	if err := spec.Validate(); err != nil {
		return err
	}
	for addr, amount := range spec.Rewards {
		state.AddBalance(addr, amount)
	}
	return nil
}
```
