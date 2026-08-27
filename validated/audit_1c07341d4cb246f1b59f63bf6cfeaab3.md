### Title
Attacker-controlled `_convertRatio`/`_minRec` in `ManualCompound.compound()` forces the entire shared WOM balance through `SmartWomConvert.convertFor`'s unchecked AMM leg - (File: wombat/SmartWomConvert.sol, rewards/ManualCompound.sol)

### Summary
`SmartWomConvert._convertFor()` only validates `_convertRatio > DENOMINATOR` and otherwise fully trusts the caller-supplied ratio and `_minRec` slippage bound. `ManualCompound.compound()` forwards these two attacker-controlled parameters unmodified to `convertFor()` while operating on `receivedBalance = IERC20(_tokenAddress).balanceOf(address(this))` [1](#0-0)  — a raw snapshot of the whole contract's token balance rather than an amount strictly scoped to what was claimed for the current caller in this call. This lets an unprivileged caller route the contract's entire reward-token balance through the AMM leg of `convertFor` with a self-chosen ratio and a zero/low `_minRec`, defeating the slippage protection that is supposed to guard the conversion.

### Finding Description
`SmartWomConvert._convertFor()` has a single guard:
```solidity
if (_convertRatio > DENOMINATOR)
    revert IncorrectRatio();
``` [2](#0-1) 

Any value `0 <= _convertRatio <= DENOMINATOR` is accepted, letting the caller decide the exact split between the "mint 1:1" leg (`convertAmount`) and the "swap via `womMWomPool`" leg (`buybackAmount`) [3](#0-2) . This is in contrast to `smartConvert()`, the intended "safe" entrypoint, which computes `convertRatio` itself from `currentRatio()`, `buybackThreshold`, and `maxSwapAmount()` rather than accepting it from the caller [4](#0-3) .

`convertFor()` is a plain external function with no access control, reachable both directly and via `ManualCompound.compound()`:
```solidity
IConverter(_convertor).convertFor(receivedBalance, _convertRatio, _minRec, msg.sender, 2);
``` [1](#0-0) 

Crucially, `receivedBalance` is computed as `IERC20(_tokenAddress).balanceOf(address(this))` for every registered reward token in `rewards[]`, independent of the specific `_lps`/`_rewards` array the caller passed to `multiclaimOnBehalf` in that same call [5](#0-4) . `ManualCompound` is a single shared contract used by all compounding users; nothing in `compound()` reconciles `receivedBalance` against only the newly claimed amount for `msg.sender`, so any WOM sitting in the contract at call time (residue from other reward tokens' claim flows, rounding dust from prior `_convertFor` calls, or unrelated inbound transfers) is swept in full and forced through the attacker-chosen `_convertRatio`/`_minRec` path, with the resulting mWom locked exclusively to `msg.sender` via `mWomSV.lockFor(obtainedmWomAmount, _for)` (mode 2) [6](#0-5) .

### Impact Explanation
By setting `_convertRatio = 0` and `_minRec = 0`, an attacker forces 100% of `receivedBalance` through the `womMWomPool` swap with no minimum-output protection (`convertAmount + amountRec < _minRec` never reverts) [7](#0-6) . Combined with `receivedBalance` being an unscoped `balanceOf()` read of a shared contract, this allows an unprivileged caller to direct any token balance not exclusively resulting from their own current claim (i.e., value that can belong to the shared/protocol balance rather than solely to the caller) through an AMM leg whose ratio and slippage bound they fully control, and receive the output for themselves. This matches "routing parameter that decides how much protocol value is traded must not be caller-supplied on a shared-balance path."

### Likelihood Explanation
No privileged role is required — `compound()` and `convertFor()` are both unrestricted external functions. The attacker only needs to call `compound()` (optionally with empty/minimal `_lps`/`_rewards`) supplying arbitrary `_convertRatio`/`_minRec`, which is a single-transaction, repeatable action with no special capital requirement beyond gas.

### Recommendation
- In `SmartWomConvert`, do not accept an arbitrary caller-supplied `_convertRatio`/`_minRec` on any path that can be invoked with pooled/shared balances; require compounding flows to go through `smartConvert()` (or equivalent protocol-computed ratio) instead of `convertFor()`.
- In `ManualCompound.compound()`, scope `receivedBalance` strictly to the amount actually claimed for `msg.sender` in the current call (e.g., use balance deltas measured immediately before/after `multiclaimOnBehalf`) instead of the full `balanceOf(address(this))`.

### Proof of Concept
Hardhat/Foundry plan:
1. Deploy `SmartWomConvert`, `ManualCompound`, mock `MasterMagpie`, mock `IWombatRouter`/`womMWomPool`, and `mWomSV` locker.
2. Register WOM as a compoundable reward with `convertor = SmartWomConvert`.
3. Simulate residual WOM balance in `ManualCompound` not attributable to the attacker's own claim (e.g., have the mock `multiclaimOnBehalf` leave WOM in the contract for a different token/pool than the attacker's `_lps` request, or pre-fund the contract).
4. As an unprivileged attacker EOA, call `compound(_lps, _rewards, _convertRatio=0, _minRec=0, false)`.
5. Assert: `convertFor` is invoked with `_amount == balanceOf(ManualCompound)` (not limited to attacker's own claimed share), the swap is executed with `_minRec=0`, and the resulting mWom is locked entirely to the attacker via `mWomSV.lockFor`.
6. Assert violation: `_convertRatio` used in the swap is not reconciled against any protocol-computed value (unlike `smartConvert()`), demonstrating the unrestricted routing/slippage bypass on a shared-balance path.

### Citations

**File:** rewards/ManualCompound.sol (L123-149)
```text
    function compound(address[] calldata _lps, address[][] calldata _rewards, uint256 _convertRatio, uint256 _minRec, bool _lockMgp) external {
        uint256 rewardTokensLength = rewards.length;        
        IMasterMagpie(masterMagpie).multiclaimOnBehalf(_lps, _rewards, msg.sender);
        // send none compoundable reward back to caller
        for(uint256 i; i < _lps.length; i++) {
            uint256 rewardLength = _rewards[i].length;
            if (rewardLength > 0) {
                for (uint j; j < rewardLength; j++) {
                    if (!compoundableRewards[_rewards[i][j]]) {
                        uint256 rewardBalance = IERC20(_rewards[i][j]).balanceOf(address(this));
                        if (rewardBalance > 0)
                            IERC20(_rewards[i][j]).safeTransfer(msg.sender, rewardBalance);
                    }
                }
            }
        }
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
```

**File:** wombat/SmartWomConvert.sol (L133-147)
```text
    function smartConvert(uint256 _amountIn, uint256 _mode) external returns (uint256 obtainedmWomAmount) {
        if (_amountIn == 0) revert MustNoBeZero();

        uint256 convertRatio = DENOMINATOR;
        uint256 mWomToWom = currentRatio();

        if (mWomToWom < buybackThreshold) {
            uint256 maxSwap = maxSwapAmount();
            uint256 amountToSwap = _amountIn > maxSwap ? maxSwap : _amountIn;
            uint256 convertAmount = _amountIn - amountToSwap;
            convertRatio = convertAmount * DENOMINATOR / _amountIn;
        }

        return _convertFor(_amountIn, convertRatio, _amountIn, msg.sender, _mode);
    }
```

**File:** wombat/SmartWomConvert.sol (L175-197)
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
```

**File:** wombat/SmartWomConvert.sol (L199-205)
```text
        if (convertAmount > 0) {
            IERC20(wom).safeApprove(mWom, convertAmount);
            IMWom(mWom).deposit(convertAmount);
        }

        if (convertAmount + amountRec < _minRec)
            revert MinRecNotMatch();
```

**File:** wombat/SmartWomConvert.sol (L212-214)
```text
        } else if (_mode == 2) {
            IERC20(mWom).safeApprove(address(mWomSV), obtainedmWomAmount);
            mWomSV.lockFor(obtainedmWomAmount, _for);
```
