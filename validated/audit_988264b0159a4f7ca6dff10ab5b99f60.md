### Title
Unbounded spot-price swap output in `ArbWomUp2._bullMGP` allows flash-loan price manipulation to inflate `mgpAmountToLcok` locked to the attacker - (File: `wombat/ArbWomUp2.sol`)

### Summary
`incentiveDeposit(_amount, _minMGPRec, true)` calls the internal `_bullMGP` function, which swaps the protocol's BUSD reward for MGP via `ROUTER.swapExactTokensForTokens` and then locks `amounts[1] * (DENOMINATOR + bullBonusRatio) / DENOMINATOR` MGP into `vlMGP` on behalf of `msg.sender`. `_minMGPRec` only bounds the *minimum* output, so an attacker can manipulate the BUSD/MGP pool spot price with a flash loan immediately before the call to make MGP artificially cheap, causing the swap to return an inflated `amounts[1]`, then reverse the manipulation afterward.

### Finding Description
`_bullMGP` uses the AMM spot price with no TWAP/oracle bound and no cap on the swap output: [1](#0-0) 

```
uint256[] memory amounts = ROUTER.swapExactTokensForTokens(
    _busdAmount, _minRec, path, address(this), block.timestamp
);
uint256 mgpAmountToLcok = amounts[1] * (DENOMINATOR + bullBonusRatio) / DENOMINATOR;
IERC20(mgp).approve(address(vlMGP), mgpAmountToLcok);
vlMGP.lockFor(mgpAmountToLcok, _account);
```

`_busdAmount` (the `rewardToSend`) is a fixed amount computed from the caller's cumulative WOM deposit tier via `getRewardAmount`, and it is paid out of the ArbWomUp2 contract's own BUSD balance (a protocol-funded reward), not the attacker's funds. The only user-supplied guard, `_minMGPRec`, protects against receiving *too little* MGP; it does nothing to cap the amount received when the pool is skewed to make MGP artificially cheap. An attacker can, in one atomic transaction: flash-loan capital, sell MGP into (or pull BUSD out of) the BUSD/MGP pair used by `ROUTER` to push the MGP price down, call `incentiveDeposit(_amount, 0 /*or low*/, true)` so `_bullMGP` swaps the protocol's fixed BUSD reward for MGP at the manipulated (cheap) price, receive an inflated `amounts[1]`, and thus have `vlMGP.lockFor` lock an inflated `mgpAmountToLcok` for themselves, then unwind the price skew and repay the flash loan. `nonReentrant` on `incentiveDeposit` only prevents re-entrant calls back into the contract itself; it does not prevent the attacker from manipulating an external pool before/inside the same transaction.

Note: the MGP locked via `vlMGP.lockFor` is genuinely transferred from ArbWomUp2 into `VLMGP` (`_lock` performs `MGP.safeTransferFrom`, per `VLMGP.sol:461-470`), so the vlMGP position is technically "backed" by real MGP tokens — but those tokens were obtained by the protocol's own BUSD paying an attacker-manipulated, unfair exchange rate, i.e., the protocol/BUSD reward pool overpays MGP relative to fair value, and the excess accrues to the attacker's vlMGP lock (extra voting power and yield entitlement) at the expense of the protocol's MGP holdings. [2](#0-1) 

### Impact Explanation
This is a flash-loan price-manipulation exploit that lets an attacker extract more MGP (and thus more locked voting/yield power in vlMGP) than the fair-value conversion of the protocol's fixed BUSD reward would allow, at the direct expense of the protocol's MGP reserves used to fund `bullBonusRatio` bonuses and BUSD reward conversions. This matches an Immunefi "theft of protocol funds" / "protocol insolvency" impact class, since it siphons MGP value out of the protocol using manipulated AMM pricing rather than fair-market swap execution.

### Likelihood Explanation
Requires: (1) capital sufficient to meaningfully skew the specific BUSD/MGP liquidity pool used by `ROUTER` via flash loan, (2) that pool having tractable liquidity relative to available flash-loan capital, and (3) the attacker holding/depositing enough WOM to trigger a non-trivial `rewardToSend` via `getRewardAmount`. Given BSC/Pancake-style pools and typical flash loan availability, this is feasible and repeatable per transaction as long as the ArbWomUp2 BUSD/MGP balances are non-zero.

### Recommendation
Do not rely solely on spot AMM output for a value-critical calculation. Bound `amounts[1]` (or the resulting `mgpAmountToLcok`) against a TWAP or independent oracle-derived fair value, revert if the swap execution price deviates beyond an acceptable tolerance from that reference price, and/or have governance set `_minMGPRec` server-side rather than accepting an attacker-supplied value.

### Proof of Concept
Foundry fork test plan:
1. Fork BSC mainnet at a block where the target BUSD/MGP pool (used by `ArbWomUp2.ROUTER`) exists with realistic liquidity.
2. Deploy/attach `ArbWomUp2` with real `busd`, `mgp`, `vlMGP`, `ROUTER` addresses; ensure the contract holds a BUSD balance and sufficient WOM reward-tier configuration so `incentiveDeposit` yields a non-trivial `rewardToSend`.
3. In one test transaction, simulate a flash loan: swap a large amount of MGP into the BUSD/MGP pool (or otherwise skew reserves) to depress MGP's price.
4. Call `incentiveDeposit(_amount, 0, true)` from the attacker address; record `amounts[1]`/`mgpAmountToLcok` emitted in `VLMGPRewarded`.
5. Reverse the pool skew (swap back) to restore price and simulate flash-loan repayment.
6. Compare `mgpAmountToLcok` against a TWAP/fair-value-computed expected amount (using pre-skew reserves) — assert the manipulated `mgpAmountToLcok` significantly exceeds the fair-value amount, demonstrating the protocol overpaid MGP to the attacker's vlMGP lock.

### Citations

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

**File:** VLMGP.sol (L461-470)
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
    }
```
