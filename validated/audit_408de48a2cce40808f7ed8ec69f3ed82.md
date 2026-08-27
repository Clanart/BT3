[1](#0-0) [2](#0-1) [3](#0-2)

### Citations

**File:** wombat/WombatStaking.sol (L729-737)
```text
        if (!isPoolFeeFree[_lpToken]) {
            for (uint256 i = 0; i < feeInfos.length; i++) {
                Fees storage feeInfo = feeInfos[i];

                if (feeInfo.isActive) {
                    address rewardToken = _rewardToken;
                    uint256 feeAmount = (originalRewardAmount * feeInfo.value) / DENOMINATOR;
                    _amount -= feeAmount;
                    uint256 feeTosend = feeAmount;
```

**File:** wombat/WombatStaking.sol (L767-770)
```text
        IERC20(_rewardToken).safeApprove(_rewarder, 0);
        IERC20(_rewardToken).safeApprove(_rewarder, _amount);
        IBaseRewardPool(_rewarder).queueNewRewards(_amount, _rewardToken);
    }
```

**File:** wombat/mWOM.sol (L86-127)
```text
    function deposit(uint256 _amount) whenNotPaused external {
        _convert(_amount, false, false);
    }

    // if reward ratio is turned on, reward wom converter with vlMGP with the ratio, the ratio can be more than 100%
    function incentiveDeposit(uint256 _amount, bool _stake) whenNotPaused external {
        if (rewardRatio == 0) revert NoIncentive();

        _convert(_amount, _stake, false);
        uint256 vlMGPAmount = _amount * rewardRatio / DENOMINATOR;
        IERC20(mgp).safeApprove(address(vlMGP), vlMGPAmount);
        IVLMGP(vlMGP).lockFor(vlMGPAmount, msg.sender);
        emit VlmgpRewarded(msg.sender, vlMGPAmount);
    }

    /* ============ Internal Functions ============ */

    function _convert(uint256 _amount, bool _forStake, bool _doConvert) whenNotPaused nonReentrant internal {
        if (_doConvert) {
            if (wombatStaking == address(0))
                revert WombatStakingNotSet();
            IERC20(wom).safeTransferFrom(msg.sender, wombatStaking, _amount);
            _lockWom(_amount, false);

        } else {
            IERC20(wom).safeTransferFrom(msg.sender, address(this), _amount);
        }

        if(_forStake) {
            if (helper == address(0))
                revert HelperNotSet();
            _mint(address(this), _amount);
            IERC20(address(this)).safeApprove(helper, _amount);
            ISimpleHelper(helper).depositFor(_amount, address(msg.sender));
            IERC20(address(this)).safeApprove(helper, 0);
        } else {
            _mint(msg.sender, _amount);
        }

        totalConverted = totalConverted + _amount;
        emit mWomMinted(msg.sender, _amount);
    }
```
