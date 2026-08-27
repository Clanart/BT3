## Title
Unvalidated vesting `percent` schedule allows `TokenVesting.release()` to distribute more or less tokens than allocated - (File: `rewards/TokenVesting.sol`)

### Summary
`TokenVesting`'s constructor accepts arbitrary `schedule` and `percent` arrays for each release milestone but never validates that the sum of `percent` values equals `DENOMINATOR` (10000). This is the same bug class as the reported VTVLVesting issue: a rate/ratio parameter (`_releaseIntervalSecs` there, `percent` array here) is never cross-checked against the total vested amount, so unprivileged beneficiaries calling the normal claim path can receive more or less than their allocated tokens.

### Finding Description
The constructor only checks array-length consistency, never that percentages sum to `DENOMINATOR`: [1](#0-0) 

`vestedAmount()` computes the claimable amount purely as `amount * percentSum / DENOMINATOR`, trusting that `percentSum` (the running sum of `_percent` entries) is bounded by `DENOMINATOR`: [2](#0-1) 

`getClaimable()` and `release()` (called directly by any beneficiary — an ordinary, unprivileged wallet) then transfer this value without any additional sanity check against the beneficiary's total allocation: [3](#0-2) 

If the deployer's `percent` array sums to more than 10000 (e.g. due to an off-by-one or duplicated milestone), every beneficiary's `vestedAmount()` will exceed their `amount`, letting each of them drain more tokens than allocated from the shared `_token` balance — starving other beneficiaries and potentially causing `safeTransfer` reverts once the contract's balance is exhausted. If the array sums to less than 10000, beneficiaries can never claim their full `amount`, and the remainder is permanently stuck in the contract with no recovery function (no owner sweep/rescue function exists), since the contract's only external mutating function is `release()`.

### Impact Explanation
Because there is no reconciliation between `percent` and the beneficiaries' `amount`, either:
- Protocol insolvency / theft of other beneficiaries' funds occurs when `percentSum` can exceed `DENOMINATOR`, because whichever beneficiary claims first drains disproportionately more of the shared token balance, or
- Permanent freezing of unclaimed vested tokens occurs when `percentSum` is below `DENOMINATOR`, since `release()` is the only accessor and there is no owner/admin function to correct the schedule or rescue the shortfall.

Both outcomes are triggered purely by ordinary beneficiaries calling `release()` — no privileged action is required to realize the impact once the schedule is misconfigured.

### Likelihood Explanation
The bug is latent from deployment and is guaranteed to manifest if the `percent` array does not sum to exactly `DENOMINATOR`; there is no test enforced on-chain nor any other contract in the codebase that validates or corrects this input, and no other contract references `TokenVesting` to perform such a check.

### Recommendation
Add a constructor-time check that `sum(percent) == DENOMINATOR`, and add a rescue/owner function to recover any residual token balance after all vesting periods complete, mirroring the intent of validating `_releaseIntervalSecs` against `_linearVestAmount`/duration in the original VTVLVesting report.

### Proof of Concept
1. Deploy `TokenVesting` with `schedule = [t1, t2]`, `percent = [6000, 6000]` (sum = 12000 > `DENOMINATOR`).
2. After `t2`, any beneficiary calls `release()`.
3. `vestedAmount()` returns `amount * 12000 / 10000 = 1.2 * amount`, i.e. 20% more tokens than allocated are transferred to that beneficiary, at the expense of the shared token pool meant for other beneficiaries.

### Citations

**File:** rewards/TokenVesting.sol (L49-67)
```text
    constructor (IERC20 token, address[] memory receiver, uint256[] memory amount, uint256[] memory schedule,
        uint256[] memory percent) {
        // require(receiver != address(0), "TokenVesting: beneficiary is the zero address");
        require(receiver.length == amount.length, "TokenVesting: Incorrect receiver mapping");

        require(schedule.length == percent.length, "TokenVesting: Incorrect release schedule");
        require(schedule.length <= 255, "TokenVesting: Incorrect schedule length");

        _token = token;
        _schedule = schedule;
        _percent = percent;

        for(uint i = 0; i < receiver.length; i++) {
            require(receiver[i] != address(0), "TokenVesting: beneficiary is the zero address");
            Beneficiary memory benef = Beneficiary(receiver[i], 0, amount[i]);
            _beneficiaries[receiver[i]] = benef;
            _beneficiaryList.push(receiver[i]);
        }
    }
```

**File:** rewards/TokenVesting.sol (L119-129)
```text
    function vestedAmount(uint256 ts, address receiver) public view returns (uint256) {
        int256 unreleasedIdx = _releasableIdx(ts);
        if(unreleasedIdx < 0) return 0;
        
        uint256 percentSum = 0;
        for (uint256 i = 0; i <= uint256(unreleasedIdx); i++) {
            percentSum += _percent[i];
        }

        return _beneficiaries[receiver].amount * percentSum / DENOMINATOR;
    }
```

**File:** rewards/TokenVesting.sol (L131-148)
```text
    function getClaimable(address receiver) public view returns (uint256) {
        uint256 vestedAmountNow = vestedAmount(block.timestamp, receiver);
        return vestedAmountNow - _beneficiaries[receiver].released;
    }

    /**
     * @notice Transfers vested tokens to beneficiary.
     */
    function release() public onlyBeneficiary {
        uint256 claimable = getClaimable(_msgSender());
        if (claimable > 0) {
            Beneficiary storage _beneficiary = _beneficiaries[_msgSender()];
            _beneficiary.released += claimable;
            _token.safeTransfer(_msgSender(), claimable);
        }

        emit TokensReleased(address(_token), msg.sender, claimable);
    }
```
