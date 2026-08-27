### Title
`depositFor` hardcodes `_minimumLiquidity = 0`, exposing victim deposits to sandwich/MEV extraction via Wombat pool imbalance - ([File: wombat/WombatPoolHelperV2.sol])

### Summary
`WombatPoolHelperV2.depositFor` calls `_deposit(_amount, 0, _for, address(this))`, hardcoding the minimum-liquidity slippage parameter to `0` regardless of caller input. This removes any protection against a front-run/back-run that skews the Wombat pool's cash/liability ratio right before the deposit call, causing the underlying `IWombatPool.deposit` to mint a reduced `lpReceived` amount that is then staked on behalf of `_for` in MasterMagpie.

### Finding Description
`depositFor` is a public, unauthenticated function [1](#0-0)  that pulls `_amount` of `depositToken` from `msg.sender`, approves `wombatStaking`, and calls the internal `_deposit` with a hardcoded `_minimumLiquidity` of `0`, unlike the self-service `deposit(uint256, uint256)` entrypoint which lets the caller choose their own slippage floor [2](#0-1) .

`_deposit` forwards this value unchanged into `WombatStaking.deposit`, which passes it straight to `IWombatPool(poolInfo.depositTarget).deposit(...)`; the resulting `lpReceived` (the actual liquidity minted by the AMM invariant, based on live cash/liability values) is what gets minted to the pool helper and ultimately staked for the beneficiary via `MasterMagpie.depositFor` [3](#0-2)  and [4](#0-3) .

Because Wombat's invariant computes `liabilityToMint` from the pool's current `cash`/`liability` state (see the mock's equivalent formula) [5](#0-4) , an attacker can:
1. Flash-loan swap directly against the underlying Wombat pool to push the deposit-token side into cash surplus (skewing the coverage ratio unfavorably for further deposits of that token).
2. Wait for/trigger the victim's `depositFor` transaction, which now mints a reduced `lpReceived` for the same `_amount` because it lands on the "haircut" side of the invariant, with zero floor to revert on.
3. Reverse the swap, extracting the value differential created by the victim's under-minted deposit as arbitrage profit.

`WombatStaking.deposit` only carries `nonReentrant`/`whenNotPaused`/`_onlyActivePoolHelper` guards [6](#0-5) , none of which prevent cross-transaction front-running/back-running — they only protect against reentrancy and pausing, not price/coverage-ratio manipulation between blocks or within the same block via ordering.

### Impact Explanation
This is a direct theft-of-user-funds vector: the beneficiary `_for` receives a receipt/staked position backed by fewer LP tokens than the deposit should be worth at an unmanipulated price, while the attacker captures the difference through the surrounding swap pair. This matches the "direct theft of user funds via unprotected deposit / frontrunning value extraction" impact class.

### Likelihood Explanation
Exploitability depends on: (1) available flash-loan capital sized to meaningfully skew the target Wombat pool's coverage ratio, (2) the target pool having non-trivial depth/fee curve sensitivity to coverage ratio (typical for Wombat stableswap pools), and (3) `depositFor` being called with a economically significant `_amount`. Since `depositFor` is permissionless and callable by anyone for any `_for`, the attacker does not need any special relationship with the victim, and the attack is repeatable against every future `depositFor` call.

### Recommendation
Add a `_minimumLiquidity` parameter to `depositFor` that the caller supplies (analogous to `deposit`), and pass it through to `_deposit`/`WombatStaking.deposit` instead of hardcoding `0`, so the caller can enforce a slippage floor on `lpReceived` for the benefit of `_for`.

### Proof of Concept
Fork test plan (Foundry):
1. Fork BNB/Arbitrum mainnet at a block with an active Wombat stable pool and deploy/point to the real `WombatPoolHelperV2`/`WombatStaking` contracts.
2. Baseline: call `depositFor(amount, for)` in isolation, record `lpReceived` emitted in `NewDeposit`/staked balance for `_for` in `MasterMagpie`.
3. Attack: in one transaction bundle, (a) flash-loan and swap a large amount into the pool to skew coverage ratio of `depositToken`, (b) call `depositFor(amount, for)`, (c) reverse the swap.
4. Assert: staked amount for `_for` in step 3 is strictly less than baseline in step 2 for identical `amount`, and assert attacker's net token balance after the round trip (flash loan repaid) is positive — quantifying the value extracted from `_for`'s deposit.
5. Contrast with `deposit(amount, minimumLiquidity)` where a non-zero `minimumLiquidity` reverts the same manipulated sequence, proving the fix's viability.

### Citations

**File:** wombat/WombatPoolHelperV2.sol (L99-101)
```text
    function deposit(uint256 _amount, uint256 _minimumLiquidity) external override {
        _deposit(_amount, _minimumLiquidity, msg.sender, msg.sender);
    }
```

**File:** wombat/WombatPoolHelperV2.sol (L103-107)
```text
    function depositFor(uint256 _amount, address _for) external {
        IERC20(depositToken).safeTransferFrom(msg.sender, address(this), _amount);
        IERC20(depositToken).safeApprove(wombatStaking, _amount);
        _deposit(_amount, 0, _for, address(this));
    }    
```

**File:** wombat/WombatPoolHelperV2.sol (L155-162)
```text
    function _deposit(uint256 _amount, uint256 _minimumLiquidity, address _for, address _from) internal {
        uint256 beforeDeposit = IERC20(stakingToken).balanceOf(address(this));
        IWombatStaking(wombatStaking).deposit(lpToken, _amount, _minimumLiquidity, _for, _from);
        uint256 afterDeposit = IERC20(stakingToken).balanceOf(address(this));
        _stake(afterDeposit - beforeDeposit, _for);
        
        emit NewDeposit(_for, _amount);
    }
```

**File:** wombat/WombatStaking.sol (L242-270)
```text
    function deposit(
        address _lpAddress,
        uint256 _amount,
        uint256 _minimumLiquidity,
        address _for,
        address _from
    ) nonReentrant whenNotPaused _onlyActivePoolHelper(_lpAddress) external {
        // Get information of the Pool of the token
        Pool storage poolInfo = pools[_lpAddress];
        address depositToken = poolInfo.depositToken;
        IERC20(depositToken).safeTransferFrom(_from, address(this), _amount);

        IERC20(depositToken).safeApprove(poolInfo.depositTarget, _amount);
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
        emit NewDeposit(_for, depositToken, _amount, poolInfo.receiptToken, lpReceived);
    }
```

**File:** mocks/wombat/WombatPoolMock.sol (L50-65)
```text
        uint256 liabilityToMint = exactDepositLiquidityInEquilImpl(
            int256(amount),
            int256(uint256(lpToken.cash())),
            int256(uint256(lpToken.liability())),
            int256(ampFactor)
        ).toUint256();

        if (liabilityToMint < amount) {
            liabilityToMint = amount;
        }

        uint256 lpTokenToMint = (
            lpToken.liability() == 0
                ? liabilityToMint
                : (liabilityToMint * lpToken.totalSupply()) / lpToken.liability()
        );
```
