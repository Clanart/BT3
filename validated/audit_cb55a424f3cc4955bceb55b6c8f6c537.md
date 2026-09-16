## Analog Vulnerability Found

### Title
Stake-based reward and validator-eligibility calculations rely on raw contract balance, allowing anyone to inflate a validator's `StakingAmount` by donating KAIA directly to its CN staking or CL pool contract - ([File: kaiax/staking/impl/getter.go])

### Summary
The external report describes price inflation in `VotiumStrategy` because the vault's share price is computed from `ILockedCvx.lockedBalanceOf(address(this))`, a value that any unprivileged actor can inflate by locking CVX "on behalf of" the vault (a donation with no corresponding change in `totalSupply`). The pro-rata math (`total / supply`) is then corrupted.

Kaia's `kaiax/staking` module has the same root-cause pattern: a validator's `StakingAmount` — the quantity later used in proportional reward splitting and in minimum-stake eligibility checks — is derived directly from the native-token *balance* of the registered CN staking contract (and, since Prague, the CL pool contract), not from an internally tracked "amount actually bonded via the staking protocol." Any account can send a plain value-transfer transaction to increase that balance.

### Finding Description
`StakingModule.getFromState` calls the `MultiCallContract` to fetch `StakingAmounts` for every registered CN, and this is a direct read of AddressBook/CLRegistry-reported balances: [1](#0-0) 

The unit test `TestGetStakingInfo_Uncached` proves the amount is exactly the account balance set in genesis alloc for the staking contract address, divided by `KAIA`, with no other bonding/lockup accounting involved: [2](#0-1) 

The same balance-based pattern applies to consensus-liquidity pools (`CLPoolAddr`), whose `CLStakingAmount` is likewise the raw balance of the pool address: [3](#0-2) 

This `StakingAmount`/`CLStakingAmount` value feeds two consensus-critical computations that are exact analogs of the vulnerable `cvxPerVotium()`/`price()` pro-rata math in the report:

1. **Reward distribution** — `assignStakingRewards` / `assignStakingRewardsFlex` allocate the staker reward budget proportionally to each consolidated node's excess stake over `minStake`/`threshold`: [4](#0-3) [5](#0-4) 

2. **Validator eligibility (demotion)** — `getDemotedValidatorsIstanbul`/`collectStakingAmounts` decide whether a validator meets `minStake` using the same consolidated `StakingAmount`: [6](#0-5) 

Because `StakingAmount` is simply "balance of the registered staking address," any unprivileged sender can transfer KAIA directly to a CN's staking contract (or a CL pool contract) to inflate the reported stake — exactly analogous to calling `ILockedCvx.lock(votiumStrategyAddress, ...)` to donate to `VotiumStrategy` without minting shares. This donated balance did not go through the CN staking contract's proper "stake" bonding/lockup logic (`CnStakingV4.staking()` is tracked as a distinct accounting variable from the contract's raw balance, as seen in the ABI binding), yet it is fully counted by the reward and eligibility modules.

### Impact Explanation
- **Reward redirection**: A validator operator (a "staker" - an explicitly allowed unprivileged actor per the mapping rules) can inflate their own consolidated `StakingAmount` by sending plain KAIA to their own registered staking/CL pool address, without using the intended stake-lock mechanism. This skews `assignStakingRewards`'s proportional split (`excess * stakersReward / totalExcess`) in their favor, redirecting minting rewards away from honestly-staked validators.
- **Eligibility manipulation**: The same donation can push a node's `StakingAmount` over `minStake`, converting an otherwise-demoted/ineligible validator into a reward-eligible or even qualified validator (`getDemotedValidatorsIstanbul`), affecting the active validator set / consensus composition — a form of unauthorized value/privilege gain via unaccounted "donation," directly mirroring the report's rounding/inflation impact class.
- **Gini/threshold distortion**: `StakingInfo.Gini()` and `StakingRewardThreshold`-based flex rewards are similarly affected since they consume the same raw-balance-derived `StakingAmounts`.

### Likelihood Explanation
Sending a value-transfer transaction to any address (including a CN's staking or CL pool contract, both of which are publicly known via AddressBook/CLRegistry) requires no special privilege — it is reachable by any transaction sender. The only precondition is that the target staking/CL-pool contract accepts a bare KAIA transfer to its balance, which is inherent to how these contracts function (they must be able to receive staked KAIA). This makes the analog highly reachable, though the practical benefit is realized primarily by a validator operator inflating their own registered contract's balance, since donating to someone else's stake helps that other validator rather than the sender.

### Recommendation
Do not equate the raw native-token balance of the CN staking/CL pool contract with the "eligible staking amount" used for reward and eligibility calculations. Instead, use an internally tracked staking accounting value (e.g., `CnStakingV4.staking()` or equivalent bonded-amount accounting from CLRegistry) that only increases through the contract's sanctioned stake/deposit functions, and is unaffected by arbitrary incoming transfers to the contract's address. If the raw balance must remain the source of truth for backward compatibility, add explicit reconciliation/tracking so unsolicited transfers are excluded from `StakingAmounts`/`CLStakingAmount`.

### Proof of Concept
1. Identify a CN's registered staking contract address (or CL pool address) via `kaia_getStakingInfo` / AddressBook `getAllAddress()`.
2. From any account, send a plain value-transfer transaction of `X` KAIA to that address.
3. At the next `StakingInterval`/block boundary, `StakingModule.GetStakingInfo` will report `StakingAmount += X` for that node (per the balance-read logic verified in `TestGetStakingInfo_Uncached`), as no internal bonding step was required.
4. On the following reward-distribution block, `assignStakingRewards`/`assignStakingRewardsFlex` will compute a larger `excess` for that node, granting it a disproportionately larger share of the staker reward budget at the expense of other validators — and/or `getDemotedValidatorsIstanbul` will keep/promote the node past `minStake` when it otherwise would not qualify.

### Citations

**File:** kaiax/staking/impl/getter.go (L101-119)
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

**File:** kaiax/staking/impl/getter_test.go (L129-196)
```go
func TestGetStakingInfo_Prague_Uncached(t *testing.T) {
	log.EnableLogForTest(log.LvlCrit, log.LvlWarn)
	var (
		alloc = blockchain.GenesisAlloc{
			system.AddressBookAddr: {
				Code:    system.AddressBookMockTwoCNCode,
				Balance: big.NewInt(0),
			},
			system.RegistryAddr: {
				Code:    system.RegistryMockForCLCode,
				Balance: big.NewInt(0),
			},
			common.HexToAddress("0x0000000000000000000000000000000000000F01"): { // staking1
				Balance: new(big.Int).Mul(big.NewInt(42_000_000), big.NewInt(params.KAIA)),
			},
			common.HexToAddress("0x0000000000000000000000000000000000000f04"): { // staking2
				Balance: new(big.Int).Mul(big.NewInt(99_000_000), big.NewInt(params.KAIA)),
			},
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
		}
	)

	testGetStakingInfo_CL_NoCLRegistry(t, alloc)
	testGetStakingInfo_CL(t, alloc, expected)
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

**File:** kaiax/valset/impl/getter_demote.go (L78-139)
```go
func getDemotedValidatorsIstanbul(council *valset.AddressSet, si *staking.StakingInfo, pset gov.ParamSet) *valset.AddressSet {
	var (
		demoted        = valset.NewAddressSet(nil)
		singleMode     = pset.GovernanceMode == "single"
		governingNode  = pset.GoverningNode
		minStake       = pset.MinimumStake.Uint64() // in KAIA
		stakingAmounts = collectStakingAmounts(council.List(), si)
	)

	// First filter by staking amounts.
	for _, node := range council.List() {
		if uint64(stakingAmounts[node]) < minStake {
			demoted.Add(node)
		}
	}

	// If all validators are demoted, then no one is demoted.
	if demoted.Len() == len(council.List()) {
		demoted = valset.NewAddressSet(nil)
	}

	// Under single governance mode, governing node cannot be demoted.
	if singleMode && demoted.Contains(governingNode) {
		demoted.Remove(governingNode)
	}
	return demoted
}

// TODO-kaiax: move the feature into staking_info.go
// collectStakingAmounts returns the staking amounts for the given nodes(i.e., the registered validator set).
// The staking amount for each node is determined based on the consolidated nodes,
// which aggregate stakes across multiple staking contracts that share the same reward address.
// Under a single RewardAddr, only one representative NodeId is included in the validator set,
// and that NodeId will be assigned the total aggregated stake.
//
// for example, consolidated nodes:
//
//	CN1 = {[N1,N2], [S1,S2], R1, A1+A2}
//	CN3 = {[N3],    [S3],    R3, A3}
//
// Given nodes = [N2, N3], the resulting stakingAmounts will be:
//
//	{N2: A1 + A2, N3: A3}
//
// Note: This function assumes that validator registration is controlled,
// and that only one NodeId per reward address can be part of the validator set.
// If this assumption changes, this logic may need to be revisited.
func collectStakingAmounts(nodes []common.Address, si *staking.StakingInfo) map[common.Address]float64 {
	cns := si.ConsolidatedNodes()
	stakingAmounts := make(map[common.Address]float64, len(nodes))
	for _, node := range nodes {
		stakingAmounts[node] = 0
	}
	for _, cn := range cns {
		for _, node := range cn.NodeIds {
			if _, ok := stakingAmounts[node]; ok {
				stakingAmounts[node] = float64(cn.StakingAmount)
			}
		}
	}
	return stakingAmounts
}
```
