Based on my research, the test evidence in `kaiax/staking/impl/getter_test.go` confirms that legacy (pre-permissionless) `StakingAmounts` are derived directly from the **native KAIA balance** of each CN staking contract address — the tests set `Balance: new(big.Int).Mul(big.NewInt(42_000_000), big.NewInt(params.KAIA))` on the staking-contract accounts in the genesis alloc and expect that exact figure to appear as `StakingAmounts` [1](#0-0) . The actual code path reads this via the MultiCall system contract, dividing the returned raw amount by `params.KAIA` [2](#0-1) .

### Title
Validator Staking Weight Inflatable via Direct KAIA Transfer to CN Staking Contract - (File: kaiax/staking/impl/getter.go)

### Summary
The `kaiax/staking` module derives each validator's `StakingAmounts` (used to compute reward distribution, Gini coefficient, and consolidated-node weighting) from the raw native-token balance held at the CN staking contract address, rather than from an internally tracked, access-controlled ledger of legitimately staked/locked funds.

### Finding Description
`parseCallResult` (legacy/permissioned path, active whenever the Permissionless hardfork has not activated) converts the AddressBook's `amounts` array directly into `StakingAmounts` with a simple unit conversion: `stakingAmounts[i] = big.NewInt(0).Div(a, big.NewInt(params.KAIA)).Uint64()` [2](#0-1) . The unit test for this path demonstrates that the AddressBook/MultiCall result equals the plain account balance of the staking contract address as set in genesis alloc, confirming the amount is sourced from `.balance` of the staking-contract account rather than an accounted "staked" ledger variable [3](#0-2) . Since native KAIA balances of any address (including these staking-contract proxies) can be increased by literally anyone submitting a plain value-transfer transaction to that address — there is no `receive`/`fallback` restriction preventing an unprivileged sender from inflating the balance — an attacker can donate KAIA directly to a validator's staking contract to change that validator's effective `StakingAmounts` without going through the legitimate staking/deposit flow of `CnStakingV4`. This is directly analogous to the Sherlock report's root cause: a critical accounting ratio (`_supplied`/utilization) is derived from `token.balanceOf(contract)` instead of an internally tracked accounted variable, allowing an unprivileged actor to distort it via a direct transfer [4](#0-3) .

`StakingAmounts` feeds `assignStakingRewards`, which computes each validator's share of the staking-reward budget proportionally to `(StakingAmount - MinimumStake)` relative to `totalExcess` across validators [5](#0-4) . Because this is a zero-sum split of a fixed budget among validators, artificially inflating one validator's `StakingAmounts` value redirects a larger share of the shared reward pool to that validator at the expense of the others.

### Impact Explanation
This would constitute unauthorized reward redirection among validators: an attacker (or a validator acting against its peers) could grant itself a disproportionate share of each block's staking reward pool merely by sending native KAIA to its own staking-contract address, diluting rewards legitimately owed to other validators. It also affects the Gini coefficient computation used elsewhere for policy/eligibility purposes, since `StakingAmounts` is the sole input to `computeGini`/consolidated-node weighting logic referenced in `kaiax/staking/staking_info.go` [6](#0-5) .

### Likelihood Explanation
However, I could **not conclusively verify** whether the production/mainnet `CnStakingV4` contract's payable/`receive` function actually permits unrestricted, permissionless direct KAIA transfers that increase the balance used by the MultiCall's real (non-mock) `multiCallStakingInfo()` implementation — I was only able to inspect Go bindings (`CnStakingV4.go`) and Solidity **mocks** (`MockCnStakingOverV2.sol`, `MultiCallContractMock*.sol`) within the indexed portion of the repository; the actual production `CnStakingV4.sol` and the real MultiCall system contract source were not found in the index. The `Staking()`/`staking()` getter exposed by the bindings suggests CnStakingV4 may track an internally accounted `staking` value distinct from `address(this).balance` [7](#0-6) , which — if that is what the real MultiCall queries in production rather than the raw balance — would mean this specific manipulation path is *not* exploitable on mainnet, and only the test-mock/legacy `AddressBookMock` path (used for permissioned/private networks) reflects a raw-balance-based amount.

**This is a limitation of index coverage; the assessment above cannot be fully confirmed without access to the actual production `CnStakingV4.sol` and MultiCall system-contract Solidity source. I recommend starting a Devin session with full repository access to verify whether the real (non-test) contract's staking-amount computation uses `.balance` or an internally accounted ledger, before treating this as a confirmed, exploitable vulnerability.**

### Recommendation
Verify (with full file access) whether production `CnStakingV4`/MultiCall computes `StakingAmounts` from `address(stakingContract).balance` or from an access-controlled internal accounting variable (e.g., `staking()`). If the former, change the computation to use the internally tracked staked amount rather than the raw account balance, exactly as recommended in the source report (use the accounted/deposited amount instead of the live token/native balance).

### Proof of Concept
Not constructible with certainty given the above uncertainty about the production contract's actual balance-vs-ledger semantics; the test in `kaiax/staking/impl/getter_test.go:74-127` demonstrates the balance-to-`StakingAmounts` mapping for the legacy/mocked path only.

### Citations

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

**File:** kaiax/staking/impl/getter.go (L82-99)
```go
func (s *StakingModule) getFromStateByNumber(num uint64) (*staking.StakingInfo, error) {
	header := s.Chain.GetHeaderByNumber(num)
	if header == nil {
		return nil, fmt.Errorf("failed to get header for block number %d", num)
	}

	// If found in side state, no bother getting from the state.
	if si := s.preloadBuffer.GetInfo(header.Root); si != nil { // Try side state
		return si, nil
	}

	// Otherwise bring up the state from the database.
	statedb, err := s.Chain.StateAt(header.Root)
	if err != nil {
		return nil, fmt.Errorf("failed to get state for block number %d: %w", num, err)
	}
	return s.getFromState(header, statedb)
}
```

**File:** kaiax/staking/impl/getter.go (L286-288)
```go
	for i, a := range amounts {
		stakingAmounts[i] = big.NewInt(0).Div(a, big.NewInt(params.KAIA)).Uint64()
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

**File:** kaiax/staking/staking_info.go (L29-58)
```go
var EmptyGini float64 = -1.0

// StakingInfo is the staking info to be used for block processing.
// Token Economy - https://docs.kaia.io/docs/learn/token-economy/
type StakingInfo struct {
	// The source block number where the staking info is captured.
	SourceBlockNum uint64 `json:"blockNum"`

	// The AddressBook triplets
	NodeIds          []common.Address `json:"councilNodeAddrs"`
	StakingContracts []common.Address `json:"councilStakingAddrs"`
	RewardAddrs      []common.Address `json:"councilRewardAddrs"`

	// Treasury fund addresses
	KEFAddr common.Address `json:"kefAddr"` // KEF contract address (or KCF, KIR)
	KIFAddr common.Address `json:"kifAddr"` // KIF contract address (or KFF, KGF, PoC)
	KPFAddr common.Address `json:"kpfAddr"` // KPF contract address

	// Staking amounts of each staking contracts, in KAIA, rounded down. Does not include CL staking amounts.
	StakingAmounts []uint64 `json:"councilStakingAmounts"`

	// Staking info from the consensus liquidity since Prague HF.
	CLStakingInfos CLStakingInfos `json:"clStakingInfos"`

	// Computed fields
	consolidatedNodes  *[]consolidatedNode
	cachedGini         *float64
	cachedGiniMinStake uint64 // The minimum staking amount used to compute Gini coefficient.
}

```

**File:** contracts/bindings/cnstakingv4/CnStakingV4.go (L573-596)
```go
// Staking is a free data retrieval call binding the contract method 0x4cf088d9.
//
// Solidity: function staking() view returns(uint256)
func (_CnStakingV4 *CnStakingV4Caller) Staking(opts *bind.CallOpts) (*big.Int, error) {
	var out []interface{}
	err := _CnStakingV4.contract.Call(opts, &out, "staking")

	if err != nil {
		return *new(*big.Int), err
	}

	out0 := *abi.ConvertType(out[0], new(*big.Int)).(**big.Int)

	return out0, err

}

// Staking is a free data retrieval call binding the contract method 0x4cf088d9.
//
// Solidity: function staking() view returns(uint256)
func (_CnStakingV4 *CnStakingV4Session) Staking() (*big.Int, error) {
	return _CnStakingV4.Contract.Staking(&_CnStakingV4.CallOpts)
}

```
