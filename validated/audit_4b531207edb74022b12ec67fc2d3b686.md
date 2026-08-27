### Title
Incorrect Minting in `mWOM` due to Trusting `_amount` Instead of Actual Received Balance on `transferFrom` - ([File: wombat/mWOM.sol])

### Summary
`mWOM.convert()` / `mWOM.deposit()` (both routed through the internal `_convert` function) call `IERC20(wom).safeTransferFrom(msg.sender, ..., _amount)` and then unconditionally `_mint(msg.sender, _amount)` mWOM, using the caller-supplied `_amount` rather than the actual balance change observed by the contract. This is exactly the bug class described in the reference report (Gearbox `PoolService.sol` USDT fee issue): if the underlying `wom` transfer results in the contract receiving less than `_amount` (fee-on-transfer/deflationary token behavior, which the ERC-20 standard does not prohibit for whatever token is configured as `wom`), the contract mints mWOM 1:1 for an amount of WOM it never actually custodies.

### Finding Description
`mWOM` is documented as "minted when 1 wom is locked in Magpie" — i.e. a strict 1:1-backed wrapper token. The core internal function is: [1](#0-0) 

Specifically:
- Line 111: `IERC20(wom).safeTransferFrom(msg.sender, address(this), _amount);` — the contract never checks its own `wom` balance before/after the transfer.
- Line 122: `_mint(msg.sender, _amount);` — mints exactly the nominal `_amount` regardless of what was actually received.
- Line 125: `totalConverted = totalConverted + _amount;` — the accounting invariant that is supposed to track WOM backing is also based on the nominal `_amount`, compounding the discrepancy.

This contrasts with the pattern correctly used elsewhere in the same protocol, e.g. `WombatStaking.deposit()`, which computes the actual amount to credit via a balance-difference check: [2](#0-1) 

`mWOM._convert()` (and by extension `convert`, `convertAndStake`, `deposit`, `incentiveDeposit`) has no such balance-difference safeguard, so any transfer-fee, rebasing, or otherwise non-standard behavior on the `wom` token address (which is set once at `__mWom_init` and can be re-pointed by the owner via `setWombatStaking`-style admin setters, but the `wom` address itself is fixed at init and not attacker-controlled) directly causes mWOM to be minted in excess of the WOM actually held/locked by the contract. `SmartWomConvert._convertFor()` has the analogous pattern (`wombat/SmartWomConvert.sol` lines 149-220): it transfers `_amount` of `wom` via `safeTransferFrom` and then computes `buybackAmount`/`convertAmount` purely from the nominal `_amount` parameter rather than from the balance actually received.

### Impact Explanation
Because mWOM is meant to be a 1:1-backed claim on locked WOM, any shortfall between transferred-in `_amount` and actually-received balance causes protocol insolvency: total mWOM supply/`totalConverted` exceeds the real WOM balance backing it. Later mWOM holders (including honest, unprivileged users) would be unable to fully redeem/convert their tokens 1:1 as intended, and the discrepancy compounds with every deposit that experiences a fee — this is a protocol-insolvency / permanent value-freezing issue for other users' claims, not merely a griefing of the depositor.

### Likelihood Explanation
This requires the `wom` ERC-20 configured for the contract to exhibit fee-on-transfer/deflationary/rebasing behavior on `transferFrom`. Under the standard WOM token deployment this is not currently the case, so likelihood under the current token configuration is low; however, the vulnerability is a structural code defect (missing balance-difference accounting) reachable by any ordinary wallet calling `convert`/`deposit`/`convertAndStake`, matching the exact bug class flagged in the reference report, and would trigger with certainty the moment any such non-standard token behavior is present.

### Recommendation
Compute the amount to mint/credit from the actual balance delta observed on the `wom` token, mirroring the pattern already used in `WombatStaking.deposit()`:
```solidity
uint256 before = IERC20(wom).balanceOf(address(this));
IERC20(wom).safeTransferFrom(msg.sender, address(this), _amount);
uint256 received = IERC20(wom).balanceOf(address(this)) - before;
// use `received` for _mint(...) and totalConverted accounting instead of _amount
```
Apply the same fix to `SmartWomConvert._convertFor()`.

### Proof of Concept
1. Assume `wom` is (or is upgraded/redeployed to be) a token that charges a transfer fee, e.g. 1% burned on `transferFrom`.
2. User calls `mWOM.deposit(1000)`. `safeTransferFrom` moves 1000 nominal WOM but the contract only receives 990 due to the fee.
3. `_mint(msg.sender, 1000)` mints 1000 mWOM to the user and `totalConverted += 1000`, even though the contract only holds 990 additional WOM.
4. Repeating this creates a growing gap between mWOM total supply and actual WOM backing held/locked, so the last redeemers cannot obtain their full 1:1 backing — a protocol insolvency condition.

### Citations

**File:** wombat/mWOM.sol (L103-127)
```text
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

**File:** wombat/WombatStaking.sol (L255-268)
```text
        uint256 beforeBalance = IERC20(poolInfo.lpAddress).balanceOf(address(this));
        IWombatPool(poolInfo.depositTarget).deposit(
            depositToken,
            _amount,
            _minimumLiquidity,
            address(this),
            block.timestamp,
            false
        );

        uint256 lpReceived = IERC20(poolInfo.lpAddress).balanceOf(address(this)) - beforeBalance;
        _toMasterWomAndSendReward(_lpAddress, lpReceived, true); // triggers harvest from wombat exchange
        // update variables
        IMintableERC20(poolInfo.receiptToken).mint(msg.sender, lpReceived);
```
