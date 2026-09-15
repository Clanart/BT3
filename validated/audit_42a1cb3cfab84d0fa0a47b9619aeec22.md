### Title
Unbounded `AddressBook.getAllAddress()/getAllAddressInfo()` array returned via `MultiCallContract` can revert on out-of-gas as permissionless CN registrations grow, breaking staking-info retrieval used in block processing - (File: kaiax/staking/impl/getter.go)

### Summary
`kaiax/staking/impl/getter.go`'s `getFromState()` is called on every block to derive `StakingInfo` (validator node IDs, staking contracts, reward addresses, staking amounts) used for reward distribution and governance calculations [1](#0-0) . It does this by invoking a single `MultiCallContract` call (`MultiCallStakingInfo` / `MultiCallStakingInfoPermissionless`), which internally aggregates the full `AddressBook.getAllAddress()` / `getAllAddressInfo()` (or ABv2 equivalent) array of every registered CN/validator in one unbounded return value [2](#0-1) . This mirrors the reported `GovNFTFactory.govNFTs()` bug class: a registry-style getter that returns an ever-growing array with no pagination, which will eventually exceed gas/return-size limits as more validators/CNs are permissionlessly registered (e.g., via `CnStakingV4Factory.deployCnStaking`) [3](#0-2) .

### Finding Description
The legacy `AddressBook` contract (see `getAllAddress()` returning `(uint8[] typeList, address[] addressList)` sized `cnNodeIdList.length * 3 + 2`, and `getAllAddressInfo()` returning the full `cnNodeIdList`/`cnStakingContractList`/`cnRewardAddressList` arrays) has no bounded/paginated accessor [4](#0-3) . `AddressBookV2` (the permissionless successor) exposes the same unbounded pattern with `GetAllAddress`/`GetAllAddressInfo`/`GetAllBlsInfo` [5](#0-4) .

Critically, this getter is not merely an off-chain convenience call — it is invoked from consensus-adjacent code on every block via `StakingModule.getFromState`, which uses `system.NewMultiCallContractCaller` and calls `contract.MultiCallStakingInfo(callOpts)` / `contract.MultiCallStakingInfoPermissionless(callOpts)` to fetch the entire validator/CN set in one EVM call [2](#0-1) . As the number of permissionlessly-registered CNs/validators grows (registration is reachable by any staker/contract deployer through `CnStakingV4Factory.deployCnStaking` and subsequent registration in the Registry/AddressBook) [6](#0-5) , the size of the array returned by `getAllAddress`/`getAllAddressInfo` (and therefore the work done inside the `MultiCall*` aggregation call) grows unboundedly with no way to page through it.

### Impact Explanation
If the aggregated call exceeds the gas or return-size ceiling used for these read-only EVM invocations, `getFromState` returns an error (`staking.ErrAddressBookCall` / `staking.ErrCLRegistryCall`) instead of a valid `StakingInfo` [7](#0-6) . Because `StakingInfo` feeds reward distribution and validator-set/staking-amount determination for the epoch, a persistent failure here would break reward distribution and staking-amount accounting chain-wide once the registered validator/CN count grows past the threshold this call can service — a liveness/availability failure of core protocol accounting rather than a simple off-chain integrator inconvenience, since this specific getter is consumed inside core node logic, not only by external dApps.

### Likelihood Explanation
Likelihood depends on real-world validator/CN counts staying below whatever gas ceiling is used internally by the `MultiCallContract`/EVM call in `getFromState`; growth is driven purely by permissionless registrations (`deployCnStaking`), which any staker can trigger without special privilege. This is a scaling/gas-exhaustion class issue (same root cause acknowledged as valid in the referenced Velodrome/Cantina report for `GovNFTFactory`), but I could not fully confirm the exact gas ceiling used for the `MultiCall*` EVM call within the available index (the `MultiCallContract.sol` implementation details for `multiCallStakingInfo`/`multiCallStakingInfoPermissionless` were not retrievable), so the precise validator count required to trigger failure is unverified.

### Recommendation
Add bounded/paginated accessors to `AddressBook` / `AddressBookV2` (e.g., `getAddressesByIndex(start, end)` or a length + per-index getter) and have `MultiCallContract`/`kaiax/staking/impl/getter.go` iterate in bounded batches rather than requiring a single unbounded call, consistent with the fix pattern already applied to `GovNFTFactory.govNFTs()` in the referenced report.

### Proof of Concept
Not independently reproducible from the indexed code alone — reproducing requires deploying and registering CN/validator counts sufficient to exceed the internal EVM call's gas/return-size limits inside `MultiCallContract`'s `multiCallStakingInfo`/`multiCallStakingInfoPermissionless`, whose Solidity implementation was not available in the retrieved index; a Devin session with full repository access would be needed to locate `contracts/system_contracts/MultiCallContract.sol` (or equivalent) and construct a concrete failing scenario.

### Citations

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

**File:** kaiax/staking/impl/getter.go (L145-180)
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

	// Permissioned: read from legacy AddressBook.
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
}
```

**File:** blockchain/system/permissionless.go (L266-290)
```go
// deployCnStakingPerValidator deploys a CnStaking proxy per validator via Factory and
// stakes KAIA via delegate() (step 3).
// Switches cfg.Origin to each validator's manager so that Factory tracks the correct deployer.
func deployCnStakingPerValidator(cfg *runtime.Config, config *AllocPermissionlessConfig, result *allocPermissionlessResult) error {
	factoryABI, _ := cnstakingv4factory.CnStakingV4FactoryMetaData.GetAbi()
	cnStakingABI, _ := cnstakingv4.CnStakingV4MetaData.GetAbi()

	for i := range config.NodeIds {
		cfg.Origin = config.NodeInfos[i].Manager
		retData, err := evmCallABIReturn(cfg, result.factory, factoryABI, "deployCnStaking", config.NodeInfos[i].Manager)
		if err != nil {
			return fmt.Errorf("deployCnStaking[%d]: %w", i, err)
		}
		proxyAddr := common.BytesToAddress(retData[12:32])

		cfg.Value = config.StakeAmts[i]
		if err := evmCallABI(cfg, proxyAddr, cnStakingABI, "delegate"); err != nil {
			return fmt.Errorf("delegate[%d]: %w", i, err)
		}
		cfg.Value = new(big.Int)

		config.NodeInfos[i].StakingContract = proxyAddr
	}
	return nil
}
```

**File:** contracts/testing/reward/AddressBookMock.sol (L282-313)
```text
    function getAllAddress() external view returns (uint8[], address[]) {
        uint8[] memory typeList;
        address[] memory addressList;
        if (isActivated == false) {
            typeList = new uint8[](0);
            addressList = new address[](0);
        } else {
            typeList = new uint8[](cnNodeIdList.length * 3 + 2);
            addressList = new address[](cnNodeIdList.length * 3 + 2);
            uint256 cnNodeCnt = cnNodeIdList.length;
            for (uint256 i = 0; i < cnNodeCnt; i++) {
                //add node id and its type number to array
                typeList[i * 3] = uint8(CN_NODE_ID_TYPE);
                addressList[i * 3] = address(cnNodeIdList[i]);
                //add staking address and its type number to array
                typeList[i * 3 + 1] = uint8(CN_STAKING_ADDRESS_TYPE);
                addressList[i * 3 + 1] = address(cnStakingContractList[i]);
                //add reward address and its type number to array
                typeList[i * 3 + 2] = uint8(CN_REWARD_ADDRESS_TYPE);
                addressList[i * 3 + 2] = address(cnRewardAddressList[i]);
            }
            typeList[cnNodeCnt * 3] = uint8(POC_CONTRACT_TYPE);
            addressList[cnNodeCnt * 3] = address(pocContractAddress);
            typeList[cnNodeCnt * 3 + 1] = uint8(KIR_CONTRACT_TYPE);
            addressList[cnNodeCnt * 3 + 1] = address(kirContractAddress);
        }
        return (typeList, addressList);
    }

    function getAllAddressInfo() external view returns (address[], address[], address[], address, address) {
        return (cnNodeIdList, cnStakingContractList, cnRewardAddressList, pocContractAddress, kirContractAddress);
    }
```

**File:** contracts/bindings/addressbookv2/AddressBookV2.go (L524-627)
```go
// GetAllAddress is a free data retrieval call binding the contract method 0x715b208b.
//
// Solidity: function getAllAddress() view returns(uint8[] typeList, address[] addressList)
func (_AddressBookV2 *AddressBookV2Caller) GetAllAddress(opts *bind.CallOpts) (struct {
	TypeList    []uint8
	AddressList []common.Address
}, error) {
	var out []interface{}
	err := _AddressBookV2.contract.Call(opts, &out, "getAllAddress")

	outstruct := new(struct {
		TypeList    []uint8
		AddressList []common.Address
	})
	if err != nil {
		return *outstruct, err
	}

	outstruct.TypeList = *abi.ConvertType(out[0], new([]uint8)).(*[]uint8)
	outstruct.AddressList = *abi.ConvertType(out[1], new([]common.Address)).(*[]common.Address)

	return *outstruct, err

}

// GetAllAddress is a free data retrieval call binding the contract method 0x715b208b.
//
// Solidity: function getAllAddress() view returns(uint8[] typeList, address[] addressList)
func (_AddressBookV2 *AddressBookV2Session) GetAllAddress() (struct {
	TypeList    []uint8
	AddressList []common.Address
}, error) {
	return _AddressBookV2.Contract.GetAllAddress(&_AddressBookV2.CallOpts)
}

// GetAllAddress is a free data retrieval call binding the contract method 0x715b208b.
//
// Solidity: function getAllAddress() view returns(uint8[] typeList, address[] addressList)
func (_AddressBookV2 *AddressBookV2CallerSession) GetAllAddress() (struct {
	TypeList    []uint8
	AddressList []common.Address
}, error) {
	return _AddressBookV2.Contract.GetAllAddress(&_AddressBookV2.CallOpts)
}

// GetAllAddressInfo is a free data retrieval call binding the contract method 0x160370b8.
//
// Solidity: function getAllAddressInfo() view returns(address[], address[], address[], address, address)
func (_AddressBookV2 *AddressBookV2Caller) GetAllAddressInfo(opts *bind.CallOpts) ([]common.Address, []common.Address, []common.Address, common.Address, common.Address, error) {
	var out []interface{}
	err := _AddressBookV2.contract.Call(opts, &out, "getAllAddressInfo")

	if err != nil {
		return *new([]common.Address), *new([]common.Address), *new([]common.Address), *new(common.Address), *new(common.Address), err
	}

	out0 := *abi.ConvertType(out[0], new([]common.Address)).(*[]common.Address)
	out1 := *abi.ConvertType(out[1], new([]common.Address)).(*[]common.Address)
	out2 := *abi.ConvertType(out[2], new([]common.Address)).(*[]common.Address)
	out3 := *abi.ConvertType(out[3], new(common.Address)).(*common.Address)
	out4 := *abi.ConvertType(out[4], new(common.Address)).(*common.Address)

	return out0, out1, out2, out3, out4, err

}

// GetAllAddressInfo is a free data retrieval call binding the contract method 0x160370b8.
//
// Solidity: function getAllAddressInfo() view returns(address[], address[], address[], address, address)
func (_AddressBookV2 *AddressBookV2Session) GetAllAddressInfo() ([]common.Address, []common.Address, []common.Address, common.Address, common.Address, error) {
	return _AddressBookV2.Contract.GetAllAddressInfo(&_AddressBookV2.CallOpts)
}

// GetAllAddressInfo is a free data retrieval call binding the contract method 0x160370b8.
//
// Solidity: function getAllAddressInfo() view returns(address[], address[], address[], address, address)
func (_AddressBookV2 *AddressBookV2CallerSession) GetAllAddressInfo() ([]common.Address, []common.Address, []common.Address, common.Address, common.Address, error) {
	return _AddressBookV2.Contract.GetAllAddressInfo(&_AddressBookV2.CallOpts)
}

// GetAllBlsInfo is a free data retrieval call binding the contract method 0x6968b53f.
//
// Solidity: function getAllBlsInfo() view returns(address[] nodeIdList, (bytes,bytes)[] pubkeyList)
func (_AddressBookV2 *AddressBookV2Caller) GetAllBlsInfo(opts *bind.CallOpts) (struct {
	NodeIdList []common.Address
	PubkeyList []BlsPublicKeyInfo
}, error) {
	var out []interface{}
	err := _AddressBookV2.contract.Call(opts, &out, "getAllBlsInfo")

	outstruct := new(struct {
		NodeIdList []common.Address
		PubkeyList []BlsPublicKeyInfo
	})
	if err != nil {
		return *outstruct, err
	}

	outstruct.NodeIdList = *abi.ConvertType(out[0], new([]common.Address)).(*[]common.Address)
	outstruct.PubkeyList = *abi.ConvertType(out[1], new([]BlsPublicKeyInfo)).(*[]BlsPublicKeyInfo)

	return *outstruct, err

}
```
