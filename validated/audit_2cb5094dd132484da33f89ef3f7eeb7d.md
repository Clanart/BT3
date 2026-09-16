[1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4)

### Citations

**File:** contracts/testing/system_contracts/TreasuryRebalance.sol (L81-127)
```text
    function registerRetired(
        address _retiredAddress
    ) public onlyOwner onlyAtStatus(Status.Initialized) {
        require(
            !retiredExists(_retiredAddress),
            "Retired address is already registered"
        );
        Retired storage retired = retirees.push();
        retired.retired = _retiredAddress;
        emit RetiredRegistered(retired.retired);
    }

    /**
     * @dev remove the retired details from the array
     * @param _retiredAddress is the address of the retired
     */
    function removeRetired(
        address _retiredAddress
    ) public onlyOwner onlyAtStatus(Status.Initialized) {
        uint256 retiredIndex = getRetiredIndex(_retiredAddress);
        require(retiredIndex != type(uint256).max, "Retired not registered");
        retirees[retiredIndex] = retirees[retirees.length - 1];
        retirees.pop();

        emit RetiredRemoved(_retiredAddress);
    }

    /**
     * @dev registers newbie address and its fund distribution
     * @param _newbieAddress is the address of the newbie
     * @param _amount is the fund to be allocated to the newbie
     */
    function registerNewbie(
        address _newbieAddress,
        uint256 _amount
    ) public onlyOwner onlyAtStatus(Status.Initialized) {
        require(
            !newbieExists(_newbieAddress),
            "Newbie address is already registered"
        );
        require(_amount != 0, "Amount cannot be set to 0");

        Newbie memory newbie = Newbie(_newbieAddress, _amount);
        newbies.push(newbie);

        emit NewbieRegistered(_newbieAddress, _amount);
    }
```

**File:** contracts/testing/system_contracts/TreasuryRebalance.sol (L151-178)
```text
    function approve(
        address _retiredAddress
    ) public onlyAtStatus(Status.Registered) {
        require(
            retiredExists(_retiredAddress),
            "retired needs to be registered before approval"
        );

        //Check whether the retired address is EOA or contract address
        bool isContract = isContractAddr(_retiredAddress);
        if (!isContract) {
            //check whether the msg.sender is the retired if its a EOA
            require(
                msg.sender == _retiredAddress,
                "retiredAddress is not the msg.sender"
            );
            _updateApprover(_retiredAddress, msg.sender);
        } else {
            (address[] memory adminList, ) = _getState(_retiredAddress);
            require(adminList.length != 0, "admin list cannot be empty");

            //check if the msg.sender is one of the admin of the retiredAddress contract
            require(
                _validateAdmin(msg.sender, adminList),
                "msg.sender is not the admin"
            );
            _updateApprover(_retiredAddress, msg.sender);
        }
```

**File:** contracts/testing/system_contracts/TreasuryRebalance.sol (L267-295)
```text
    function checkRetiredsApproved() public view {
        for (uint256 i = 0; i < retirees.length; i++) {
            Retired memory retired = retirees[i];
            bool isContract = isContractAddr(retired.retired);
            if (isContract) {
                (address[] memory adminList, uint256 req) = _getState(
                    retired.retired
                );
                require(
                    retired.approvers.length >= req,
                    "min required admins should approve"
                );
                //if min quorom reached, make sure all approvers are still valid
                address[] memory approvers = retired.approvers;
                uint256 validApprovals = 0;
                for (uint256 j = 0; j < approvers.length; j++) {
                    if (_validateAdmin(approvers[j], adminList)) {
                        validApprovals++;
                    }
                }
                require(
                    validApprovals >= req,
                    "min required admins should approve"
                );
            } else {
                require(retired.approvers.length == 1, "EOA should approve");
            }
        }
    }
```

**File:** contracts/testing/system_contracts/TreasuryRebalance.sol (L464-473)
```text
    /**
     * @dev Helper function to check the address is contract addr or EOA
     */
    function isContractAddr(address _addr) public view returns (bool) {
        uint256 size;
        assembly {
            size := extcodesize(_addr)
        }
        return size > 0;
    }
```

**File:** blockchain/types/tx_internal_data.go (L759-821)
```go
func validate7702(stateDB StateDB, txType TxType, from, to common.Address) error {
	switch txType {
	// Group 1: Recipient must be EOA without code
	case TxTypeValueTransfer,
		TxTypeFeeDelegatedValueTransfer,
		TxTypeFeeDelegatedValueTransferWithRatio,
		TxTypeValueTransferMemo,
		TxTypeFeeDelegatedValueTransferMemo,
		TxTypeFeeDelegatedValueTransferMemoWithRatio:
		acc := stateDB.GetAccount(to)
		if acc == nil {
			return nil
		}
		if acc.Type() == account.SmartContractAccountType {
			return kerrors.ErrToMustBeEOAWithoutCode
		}
		eoa, ok := acc.(*account.ExternallyOwnedAccount)
		if !ok || !bytes.Equal(eoa.GetCodeHash(), emptyCodeHash) {
			return kerrors.ErrToMustBeEOAWithoutCode
		}

		return nil

	// Group 2: From must be EOA without code
	case TxTypeAccountUpdate,
		TxTypeFeeDelegatedAccountUpdate,
		TxTypeFeeDelegatedAccountUpdateWithRatio:
		acc := stateDB.GetAccount(from)
		if acc == nil {
			return nil
		}
		if acc.Type() == account.SmartContractAccountType {
			return kerrors.ErrFromMustBeEOAWithoutCode
		}
		eoa, ok := acc.(*account.ExternallyOwnedAccount)
		if !ok || !bytes.Equal(eoa.GetCodeHash(), emptyCodeHash) {
			return kerrors.ErrFromMustBeEOAWithoutCode
		}

		return nil

	// Group 3: Recipient must be EOA with code or SCA
	case TxTypeSmartContractExecution,
		TxTypeFeeDelegatedSmartContractExecution,
		TxTypeFeeDelegatedSmartContractExecutionWithRatio:
		acc := stateDB.GetAccount(to)
		if acc == nil {
			return kerrors.ErrToMustBeEOAWithCodeOrSCA
		}
		if acc.Type() == account.SmartContractAccountType {
			return nil
		}
		eoa, ok := acc.(*account.ExternallyOwnedAccount)
		if !ok || !bytes.Equal(eoa.GetCodeHash(), emptyCodeHash) {
			return nil
		}

		return kerrors.ErrToMustBeEOAWithCodeOrSCA

	default:
		return nil
	}
}
```
