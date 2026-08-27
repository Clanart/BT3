### Title
Caller-supplied `_convertRatio` applied to the entire pooled reward balance in `ManualCompound.compound()` allows theft of other users' rewards via reentrancy - (File: rewards/ManualCompound.sol)

### Summary
`ManualCompound.compound()` determines the amount to convert by reading `IERC20(_tokenAddress).balanceOf(address(this))` rather than tracking the amount claimed for `msg.sender` in the current call, and forwards the caller's own `_convertRatio`/`_minRec` to `IConverter(_convertor).convertFor(...)`, sending all proceeds to `msg.sender`. [1](#0-0)  Because `ManualCompound.sol` has no reentrancy guard, and the reward token transfer that lands funds in the contract (via `multiclaimOnBehalf`) can trigger an external call if the token has a transfer hook, an attacker can reenter `compound()` mid-way through another user's claim and convert the pooled balance — which includes the victim's freshly claimed, not-yet-processed reward tokens — using an attacker-chosen `_convertRatio`, routing the proceeds to themselves.

### Finding Description
`compound()` first calls `IMasterMagpie(masterMagpie).multiclaimOnBehalf(_lps, _rewards, msg.sender)`, which pulls the caller's claimable rewards into the `ManualCompound` contract. [2](#0-1)  After the claim, for every *registered* reward token (not scoped to the specific `_lps`/`_rewards` supplied by this caller), it reads the entire current token balance of the contract:

```solidity
uint256 receivedBalance = IERC20(_tokenAddress).balanceOf(address(this));
...
IConverter(_convertor).convertFor(receivedBalance, _convertRatio, _minRec, msg.sender, 2);
``` [3](#0-2) 

`_convertRatio` is taken directly from the caller with no validation in `ManualCompound.sol` (the only bound check, `_convertRatio > DENOMINATOR`, lives downstream in `SmartWomConvert._convertFor`, which merely rejects values >10000, not values that misallocate someone else's share). [4](#0-3)  `SmartWomConvert._convertFor` then uses this ratio to split the *entire* `receivedBalance` between a buyback swap and a direct conversion and sends 100% of the output to `_for` (== `msg.sender` of the outer `compound()` call). [5](#0-4) 

Because the accounting unit is `balanceOf(address(this))` — a shared, mutable pot — rather than a per-call, per-user delta, any balance sitting in the contract at the moment `compound()` executes (including a victim's already-transferred-but-not-yet-processed claim) is swept up and converted according to the *current caller's* ratio and sent entirely to the current caller. `ManualCompound.sol` has no `nonReentrant` modifier anywhere in the file (confirmed by the absence of any `ReentrancyGuard`/`nonReentrant` reference in this file, unlike most other reward contracts in the repo such as `MasterMagpie.sol`, `VLMGP.sol`, `vlMGPBaseRewarder.sol`, etc.). If a registered reward token has a transfer hook the attacker controls (the stated precondition), the token transfer performed inside `multiclaimOnBehalf` (or inside `safeTransfer`/`safeApprove`+`convertFor` for a victim's transaction) can call back into the attacker's contract, which reenters `compound()` with the attacker's own `_lps`/`_rewards` (possibly empty) and an attacker-chosen `_convertRatio`/`_minRec`, causing the pooled `receivedBalance` — including the victim's in-flight claimed tokens — to be converted per the attacker's ratio and paid out to the attacker.

### Impact Explanation
This allows an attacker to redirect another user's claimed/compounding reward value to themselves by controlling the AMM routing ratio (`_convertRatio`) applied against a shared balance rather than an isolated, per-user amount. This is a direct theft of user funds (unclaimed/compounding yield), matching the Critical Immunefi impact class for direct theft of user funds.

### Likelihood Explanation
The exploit requires: (1) a registered reward token with a transfer/callback hook the attacker controls (stated precondition — not universally true of all reward tokens used, e.g., plain WOM/MGP ERC-20s do not have hooks, so exploitability is conditional on which token is registered via `addReward`), and (2) timing a reentrant call while a victim's `compound()` transaction is executing. Given the owner controls which tokens are added as compoundable rewards (`addReward`), this is only exploitable if a hook-bearing token is present in the reward set; assuming that precondition holds, the attack requires no special capital or privilege — any EOA/contract can call `compound()`.

### Recommendation
- Do not use `balanceOf(address(this))` as the conversion amount; instead track and convert only the balance amount actually resulting from `msg.sender`'s own claim in this call (e.g., diff of balance before/after `multiclaimOnBehalf`, or have `multiclaimOnBehalf` return per-user claimed amounts).
- Add a `nonReentrant` modifier to `compound()` in `ManualCompound.sol`.
- Consider disallowing/whitelisting reward tokens with transfer hooks, or process transfers using pull-based patterns that don't allow mid-transfer reentrancy.

### Proof of Concept
Hardhat/Foundry test plan:
1. Deploy `ManualCompound` with `masterMagpie` mock, and register a mock ERC-777-like reward token (`HookToken`) with a transfer hook via `addReward`, plus a mock `IConverter`.
2. Deploy an attacker contract implementing the token's hook callback, which calls `ManualCompound.compound([], [[]], attackerRatio, 0, false)` when notified of an incoming transfer to `ManualCompound`.
3. Simulate victim calling `compound(victimLps, victimRewards, victimRatio, victimMinRec, false)`; `multiclaimOnBehalf` transfers `HookToken` to `ManualCompound`, triggering the hook mid-transfer, which reenters `compound()` from the attacker.
4. Assert: the attacker's reentrant call converts (via the mock `IConverter.convertFor`) a `receivedBalance` that includes the victim's just-transferred tokens, using `attackerRatio` instead of `victimRatio`, and the resulting output is sent to the attacker's address — demonstrating `_convertRatio supplied by the caller` is not reconciled with `the value being converted for other users`.
5. Assert final state: victim receives less than expected mWom/converted output; attacker receives disproportionate output relative to their own zero claimed input.

### Citations

**File:** rewards/ManualCompound.sol (L123-125)
```text
    function compound(address[] calldata _lps, address[][] calldata _rewards, uint256 _convertRatio, uint256 _minRec, bool _lockMgp) external {
        uint256 rewardTokensLength = rewards.length;        
        IMasterMagpie(masterMagpie).multiclaimOnBehalf(_lps, _rewards, msg.sender);
```

**File:** rewards/ManualCompound.sol (L139-159)
```text
        for (uint256 i; i< rewardTokensLength; i++) {
            address _tokenAddress = rewards[i].tokenAddress;
            address _helperAddress = rewards[i].tokenHelper;
            address _convertor = rewards[i].convertor;
            address _locker = rewards[i].locker;
            uint256 receivedBalance = IERC20(_tokenAddress).balanceOf(address(this));

            if (receivedBalance > 0) {
                if (_convertor != address(0)) {
                    IERC20(_tokenAddress).safeApprove(_convertor, receivedBalance);
                    IConverter(_convertor).convertFor(receivedBalance, _convertRatio, _minRec, msg.sender, 2);
                } else if (_locker != address(0) && _lockMgp) {
                    IERC20(_tokenAddress).safeApprove(_locker, receivedBalance);
                    ILocker(_locker).lockFor(receivedBalance, msg.sender);                        
                } else if (_helperAddress != address(0)) { 
                    IERC20(_tokenAddress).safeApprove(_helperAddress, receivedBalance);
                    ISimpleHelper(_helperAddress).depositFor(receivedBalance, msg.sender);
                } else {
                    IERC20(_tokenAddress).safeTransfer(msg.sender, receivedBalance);
                }
            }
```

**File:** wombat/SmartWomConvert.sol (L175-217)
```text
    function _convertFor(uint256 _amount, uint256 _convertRatio, uint256 _minRec, address _for, uint256 _mode)
        internal returns (uint256 obtainedmWomAmount) {

        if (_convertRatio > DENOMINATOR)
            revert IncorrectRatio();

        IERC20(wom).safeTransferFrom(msg.sender, address(this), _amount);
        uint256 buybackAmount = _amount - (_amount * _convertRatio / DENOMINATOR);
        uint256 convertAmount = _amount - buybackAmount;
        uint256 amountRec = 0;

        if (buybackAmount > 0) {
            address[] memory tokenPath = new address[](2);
            tokenPath[0] = wom;
            tokenPath[1] = mWom;
            address[] memory poolPath = new address[](1);
            poolPath[0] = womMWomPool;
        
            IERC20(wom).safeApprove(router, buybackAmount);
            amountRec = IWombatRouter(router).swapExactTokensForTokens(
                tokenPath, poolPath, buybackAmount, 0, address(this), block.timestamp
            );
        }

        if (convertAmount > 0) {
            IERC20(wom).safeApprove(mWom, convertAmount);
            IMWom(mWom).deposit(convertAmount);
        }

        if (convertAmount + amountRec < _minRec)
            revert MinRecNotMatch();

        obtainedmWomAmount = convertAmount + amountRec;

        if (_mode == 1) {
            IERC20(mWom).safeApprove(masterMagpie, obtainedmWomAmount);
            IMasterMagpie(masterMagpie).depositFor(mWom, obtainedmWomAmount, _for);
        } else if (_mode == 2) {
            IERC20(mWom).safeApprove(address(mWomSV), obtainedmWomAmount);
            mWomSV.lockFor(obtainedmWomAmount, _for);
        } else {
            IERC20(mWom).safeTransfer(_for, obtainedmWomAmount);
        }
```
