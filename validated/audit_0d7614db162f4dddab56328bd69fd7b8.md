### Title
Missing price-manipulation protection in `_bullMGP` allows attacker to inflate `mgpAmountToLcok` and drain protocol MGP reserve via sandwich attack - ([File: wombat/ArbWomUp2.sol])

### Summary
`incentiveDeposit(_amount, _minMGPRec, true)` swaps a fixed BUSD reward for MGP through an unprotected AMM call, then computes `mgpAmountToLcok = amounts[1] * (DENOMINATOR + bullBonusRatio) / DENOMINATOR` and locks that full amount into `vlMGP` via `lockFor`, which pulls the tokens from `ArbWomUp2`'s own MGP balance. Because the caller (the attacker) supplies `_minMGPRec` themselves, there is no protocol-controlled slippage/price check, so an attacker can sandwich their own `incentiveDeposit` call to depress the MGP price in the swap, inflate `amounts[1]`, and thereby inflate the bonus MGP pulled from the contract's pre-funded MGP reserve.

### Finding Description
`incentiveDeposit` in [1](#0-0)  computes a deterministic BUSD `rewardToSend` from WOM-deposit tiers and, if `_bullMode` is true, forwards it to `_bullMGP`.

`_bullMGP` in [2](#0-1)  swaps `_busdAmount` BUSD for MGP through `ROUTER.swapExactTokensForTokens(_busdAmount, _minRec, path, ...)`, where `_minRec` is the `_minMGPRec` value the caller passed to `incentiveDeposit`. It then computes `mgpAmountToLcok = amounts[1] * (DENOMINATOR + bullBonusRatio) / DENOMINATOR` and calls `vlMGP.lockFor(mgpAmountToLcok, _account)`.

`VLMGP.lockFor` → `_lock` performs `MGP.safeTransferFrom(spender, address(this), _amount)` where `spender` is the `ArbWomUp2` contract itself [3](#0-2) . Since the swap only delivers `amounts[1]` MGP to `ArbWomUp2`, the bonus fraction `bullBonusRatio/DENOMINATOR * amounts[1]` on top of that must be covered by MGP tokens the contract already held (a protocol-funded reserve), not by the swap output itself.

Because `_minRec` is attacker-supplied rather than derived from a trusted price oracle/TWAP, it provides no protection against the caller manipulating the pool in the same transaction. An attacker can:
1. Front-run: sell MGP into the BUSD/MGP pool (or buy BUSD with MGP) to depress the MGP price.
2. Call `incentiveDeposit(_amount, 0, true)` — the fixed BUSD `rewardToSend` now buys an inflated `amounts[1]` of MGP at the depressed price.
3. Back-run: restore/arbitrage the pool back to fair price.

The inflated `amounts[1]` directly and proportionally inflates `mgpAmountToLcok`'s bonus component, which is paid out of the contract's own MGP holdings (protocol reserve) rather than tokens actually purchased. No modifier, oracle check, or reward-index mechanism in `incentiveDeposit`/`_bullMGP` guards against this; `nonReentrant`/`whenNotPaused` do not address price manipulation.

### Impact Explanation
This causes real economic loss to the protocol: the bonus MGP locked on behalf of the attacker (funded from `ArbWomUp2`'s own MGP reserve, separate from the swap proceeds) is inflated beyond what the fixed BUSD reward should fairly purchase. This matches "theft of unclaimed yield paid from protocol MGP reserves" — the excess vlMGP position is extracted from protocol-held funds rather than fairly-priced market proceeds.

### Likelihood Explanation
Requires only unprivileged capital sufficient to move the specific BUSD/MGP pool price within one or a few blocks (front-run/back-run or same-block via flash loan), which is realistic for pools with moderate/thin liquidity, and is fully repeatable on every `incentiveDeposit(..., true)` call. No special privileges are needed — only holding tokens and calling public functions.

### Recommendation
Do not let the caller control the slippage bound used to protect protocol funds. Compute `_minRec` internally from a trusted price source (e.g., TWAP oracle or a protocol-configured max-deviation check against an oracle price) rather than accepting `_minMGPRec` as attacker input, and/or cap the bonus calculation to a value independent of instantaneous swap output (e.g., base the bonus on `_busdAmount` converted at an oracle price rather than `amounts[1]` from the spot swap).

### Proof of Concept
Foundry/Hardhat fork test plan:
1. Fork BSC at a block with an active BUSD/MGP pool used by `ArbWomUp2.ROUTER`.
2. Baseline run: call `incentiveDeposit(amount, expectedMinOut, true)` under normal pool conditions; record `mgpAmountToLcok` from the `VLMGPRewarded` event.
3. Attack run: in the same block, (a) attacker sells MGP into the pool to depress price, (b) attacker calls `incentiveDeposit(amount, 0, true)`, (c) attacker arbitrages the pool back.
4. Assert the attack-run `mgpAmountToLcok` (and specifically the bonus portion `mgpAmountToLcok - amounts[1]`) is materially larger than the baseline, while `ArbWomUp2`'s own pre-funded MGP balance decreases by the excess bonus amount, demonstrating drain of protocol MGP reserve disproportionate to the fixed BUSD reward spent.

### Citations

**File:** wombat/ArbWomUp2.sol (L82-97)
```text
    function incentiveDeposit(
        uint256 _amount, uint256 _minMGPRec, bool _bullMode
    ) external _checkAmount(_amount) whenNotPaused nonReentrant {
        if (_amount == 0) return;

        uint256 rewardToSend = this.getRewardAmount(_amount, msg.sender);
        _deposit(_amount);
        claimedReward[msg.sender] += rewardToSend;
        
        if (_bullMode) {
            _bullMGP(rewardToSend, _minMGPRec, msg.sender);
        } else {
            IERC20(busd).transfer(msg.sender, rewardToSend);
            emit BUSDRewarded(msg.sender, rewardToSend);
        }
    }
```

**File:** wombat/ArbWomUp2.sol (L162-181)
```text
    function _bullMGP(uint256 _busdAmount, uint256 _minRec, address _account) internal {
        IERC20(busd).safeApprove(address(ROUTER), _busdAmount);
        
        address[] memory path = new address[](2);
        path[0] = busd;
        path[1] = mgp;
        uint256[] memory amounts = ROUTER.swapExactTokensForTokens(
            _busdAmount,
            _minRec,
            path,
            address(this),
            block.timestamp
        );

        uint256 mgpAmountToLcok = amounts[1] * (DENOMINATOR + bullBonusRatio) / DENOMINATOR; // get bull mode bonus
        IERC20(mgp).approve(address(vlMGP), mgpAmountToLcok);
        vlMGP.lockFor(mgpAmountToLcok, _account);

        emit VLMGPRewarded(_account, _busdAmount, mgpAmountToLcok);
    }
```

**File:** VLMGP.sol (L461-469)
```text
    function _lock(
        address spender,
        address _for,
        uint256 _amount
    ) internal {
        MGP.safeTransferFrom(spender, address(this), _amount);
        IMasterMagpie(masterMagpie).depositVlMGPFor(_amount, _for);
        totalAmount += _amount; // trigers update pool share, so happens after toal amount increase
        if (referralStorage != address(0)) IReferralStorage(referralStorage).updateTotalFactor(_for);
```
