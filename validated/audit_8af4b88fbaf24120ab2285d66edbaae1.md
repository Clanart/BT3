## Title
Yield permanently locked in `StreamingYieldVault` when all holders exit during an active vesting tranche - (File: `sdk/packages/core/contracts/vaults/StreamingYieldVault.sol`)

## Summary
`StreamingYieldVault` streams owner-supplied yield linearly and masks the unvested portion out of `totalAssets()` [1](#0-0) . Withdrawals and redemptions are always open, even while a tranche is vesting [2](#0-1) . Nothing in the contract prevents the real (non-virtual) share supply from dropping to zero while `_vestingAmount` is still non-zero. When that happens, the not-yet-vested tokens remain in the contract's asset balance with no shares outstanding to claim them once they finish vesting — exactly the "yield permanently locked when all stakers withdraw during vesting" bug class from the external report. The contract's own docs concede the fix ("seed-and-burn") is only a deployment-time *recommendation*, not an enforced invariant [3](#0-2) [4](#0-3) .

## Finding Description
`totalAssets()` is defined as `balanceOf(vault) - lockedYield()`, where `lockedYield()` is the not-yet-recognized part of the current tranche [5](#0-4) [6](#0-5) . `addYield` (owner-only) pulls funds and arms a new tranche without requiring the owner to keep a stake in the vault, and without any check on the current holder distribution [7](#0-6) .

`maxDeposit`/`maxMint` are gated to zero while a tranche vests, but `withdraw`/`redeem` are never gated — they use the plain, unmodified OpenZeppelin ERC-4626 `_withdraw` path, which only checks the caller's own share balance/allowance, not whether other holders still have skin in the game. The contract overrides only `totalAssets`, `_decimalsOffset`, `maxDeposit`, and `maxMint`; it does not override `_deposit`/`_withdraw`/`redeem` to add any minimum-supply invariant [8](#0-7) .

Consequently, if every real depositor redeems their shares while a tranche is still vesting, the real share supply falls to zero (only the OZ virtual-share offset remains, which is not a real, claimable token balance). The still-locked yield sitting in the contract's asset balance becomes permanently unattributed: no shares exist to redeem it once it finishes vesting. This is the exact corrupted invariant from the external report — "all stakers withdraw during vesting window" leaves stranded assets with `totalSupply == 0` — reproduced here because `StreamingYieldVault` relies on an off-chain/deployment-time convention (seed-and-burn) rather than an on-chain guarantee.

The docs explicitly flag this as merely "still recommended," confirming it is not enforced in code: [9](#0-8) [10](#0-9) 

Any subsequent depositor arriving after the tranche fully vests inherits a diluted conversion rate (their new deposit is priced against `totalAssets()` that already includes the stranded yield, per OZ's `_convertToShares`/`_convertToAssets` virtual-offset formula), so the stranded value is not cleanly recoverable even by a later depositor — it leaks pro-rata and only if someone happens to deposit again.

## Impact Explanation
This is a direct loss-of-funds / stuck-funds bug: yield that the vault owner has legitimately paid into the vault (a real transfer of the underlying asset) becomes permanently unclaimable by any party once the real share supply transiently reaches zero during an active vesting window. Because `withdraw`/`redeem` are unconditionally open during vesting (this is documented as a feature, not a bug: "Withdrawals and redeems are always available" [2](#0-1) ), any ordinary sequence of user-initiated exits — no malicious peer, relayer, or governance action required — can trigger the lock. This matches the bounty's "stealing or loss of funds" / "logic attack" category using only unprivileged, public entrypoints (`deposit`, `redeem`, `withdraw`).

## Likelihood Explanation
The vault only prevents this by *deployment discipline* (owner does a seed deposit and burns the shares), which is a recommendation in the docs, not a contract-enforced precondition. Any integrator who deploys `StreamingYieldVault` without following the seed-and-burn step, or whose seeded stake is later fully withdrawn by the owner alongside other holders, is exposed. Given multiple holders naturally entering/exiting around 24h vesting cycles, a scenario where all holders exit near-simultaneously while a tranche is mid-vest is plausible without any adversarial coordination.

## Recommendation
Enforce the seed-and-burn invariant on-chain rather than only in documentation: e.g., in the constructor or on first deposit, mint a minimum number of shares to a burn address (or otherwise disallow `totalSupply()` — excluding the OZ virtual offset — from reaching zero while `_vestingAmount > 0`/`lockedYield() > 0`). Alternatively, add a `_withdraw` override that, when a withdrawal would leave zero real shares outstanding while yield is still vesting, either reverts, or flushes the remaining locked yield back to the owner/treasury (mirroring the "rescue" mechanism adopted in the referenced Syntetika fix).

## Proof of Concept
Using the existing test harness `StreamingYieldVaultTest` conventions:

```solidity
function test_allHoldersExitDuringVesting_locksYield() public {
    // No seed-and-burn performed by the integrator (contract does not require it).

    // Two depositors, no owner stake.
    _deposit(alice, 100 ether);
    _deposit(bob, 100 ether);

    // Owner streams 20 ether of yield.
    _addYield(20 ether);

    // Halfway through the 22h vest.
    vm.warp(block.timestamp + 11 hours);

    // Both holders redeem everything — always allowed, even mid-vest.
    _redeemAll(alice);
    _redeemAll(bob);

    // Real share supply is now ~0 (only OZ virtual offset remains).
    assertApproxEqAbs(vault.totalSupply(), 0, 10 ** vault.decimals()); // negligible virtual dust

    // Vault still holds the not-yet-vested remainder of the 20 ether tranche.
    assertGt(asset.balanceOf(address(vault)), 0);

    // Let vesting finish.
    vm.warp(block.timestamp + 11 hours);

    // totalAssets() now reports the fully-vested leftover, but there are no
    // real shares to redeem it — it is stranded until (and unless) a fresh
    // deposit arrives, and even then only partially recoverable pro-rata.
    assertGt(vault.totalAssets(), 0);
    assertApproxEqAbs(vault.totalSupply(), 0, 10 ** vault.decimals());
}
``` [11](#0-10)

### Citations

**File:** sdk/packages/core/contracts/vaults/StreamingYieldVault.sol (L43-140)
```text
contract StreamingYieldVault is ERC4626, Ownable, IERC1363Receiver {
    using SafeERC20 for IERC20;

    /// @notice Window over which each yield tranche is linearly recognized. Deposits and mints are
    ///         disabled while a tranche vests (`maxDeposit`/`maxMint` report 0), so `VEST` must end
    ///         before the next `addYield` to leave a window for new capital to enter. With
    ///         `MIN_WINDOW = 2h`, a 22h vest yields a 24h minimum cadence and a guaranteed 2h
    ///         deposit window each cycle.
    uint256 public constant VEST = 22 hours;

    /// @notice Minimum time `addYield` must wait after the current tranche finishes vesting before
    ///         it may start the next one. Because `addYield` cannot fire during this stretch, every
    ///         cycle has a guaranteed deposit window of at least `MIN_WINDOW`, independent of how
    ///         eagerly the keeper runs. Minimum cadence is therefore `VEST + MIN_WINDOW`.
    uint256 public constant MIN_WINDOW = 2 hours;

    /// @dev Virtual-share offset hardening the first-depositor inflation attack. Shares carry
    ///      `assetDecimals + DECIMALS_OFFSET` decimals.
    uint8 private constant DECIMALS_OFFSET = 6;

    /// @dev The size of the yield tranche currently being recognized.
    uint256 private _vestingAmount;

    /// @dev The timestamp at which the current tranche started vesting. Zero means no tranche
    ///      has ever been added.
    uint256 private _vestingStart;

    /// @notice Thrown when `addYield` is called before the previous tranche has fully vested.
    error YieldStillVesting(uint256 vestedAt);

    /// @notice Thrown when `addYield` is called with a zero amount.
    error ZeroAmount();

    /// @notice Thrown when `addYield` is called during the guaranteed deposit window, before
    ///         `MIN_WINDOW` has elapsed past the end of the previous tranche's vesting.
    error DepositWindowOpen(uint256 closesAt);

    /// @notice Thrown when `onTransferReceived` is called by anything other than the vault's asset.
    error CallerNotAsset(address caller);

    /// @notice Emitted when a new yield tranche begins vesting.
    event YieldAdded(uint256 amount, uint256 vestingStart);

    constructor(IERC20 asset_, string memory name_, string memory symbol_, address owner_)
        ERC20(name_, symbol_)
        ERC4626(asset_)
        Ownable(owner_)
    {}

    /// @inheritdoc ERC4626
    /// @notice Total assets backing shares, net of any not-yet-vested yield.
    function totalAssets() public view override returns (uint256) {
        return IERC20(asset()).balanceOf(address(this)) - _lockedYield();
    }
    
    /// @inheritdoc ERC4626
    function _decimalsOffset() internal pure override returns (uint8) {
        return DECIMALS_OFFSET;
    }

    /// @notice The portion of the current tranche that has not yet been recognized.
    function lockedYield() external view returns (uint256) {
        return _lockedYield();
    }

    /// @notice The timestamp at which the current tranche finishes vesting. Deposits open at this
    ///         time; `addYield` may only be called from `nextYieldAt()` onward.
    function vestedAt() external view returns (uint256) {
        return _vestingStart + VEST;
    }

    /// @notice The earliest time the next `addYield` may be called. The interval
    ///         `[vestedAt(), nextYieldAt()]` is the guaranteed deposit window for each cycle.
    function nextYieldAt() external view returns (uint256) {
        return _vestingStart + VEST + MIN_WINDOW;
    }

    /// @dev True while the current tranche is still vesting, i.e. deposits are locked. Returns
    ///      false before the first tranche has ever been added (`_vestingStart == 0`).
    function _isVesting() internal view returns (bool) {
        uint256 start = _vestingStart;
        return start != 0 && block.timestamp < start + VEST;
    }

    /// @inheritdoc ERC4626
    /// @dev Zero while a tranche is vesting so deposits are closed (and integrators can detect it);
    ///      unbounded otherwise. This is the single lock that keeps new capital from joining
    ///      mid-tranche: `deposit` reverts at its `maxDeposit` check with `ERC4626ExceededMaxDeposit`.
    function maxDeposit(address) public view override returns (uint256) {
        return _isVesting() ? 0 : type(uint256).max;
    }

    /// @inheritdoc ERC4626
    /// @dev Zero while a tranche is vesting so integrators see mints are closed; unbounded otherwise.
    function maxMint(address) public view override returns (uint256) {
        return _isVesting() ? 0 : type(uint256).max;
    }

```

**File:** sdk/packages/core/contracts/vaults/StreamingYieldVault.sol (L163-170)
```text
    /// @dev Linear unlock of the current tranche, keyed on `block.timestamp` so that a deposit
    ///      and withdrawal within the same block observe an identical, unchanged share price.
    function _lockedYield() internal view returns (uint256) {
        uint256 start = _vestingStart;
        uint256 elapsed = block.timestamp - start;
        if (elapsed >= VEST) return 0;
        return (_vestingAmount * (VEST - elapsed)) / VEST;
    }
```

**File:** sdk/packages/core/contracts/vaults/StreamingYieldVault.sol (L172-203)
```text
    /// @notice Add a new yield tranche, pulled from the caller. Reverts unless the previous
    ///         tranche has fully vested, which guarantees tranches never overlap and no yield
    ///         is ever left permanently locked.
    /// @param amount The amount of `asset` to stream in over `VEST`.
    function addYield(uint256 amount) external onlyOwner {
        // Pull the funds first so `balanceOf` already reflects `amount` before it is marked
        // locked; otherwise `totalAssets` would transiently underflow when a tranche exceeds
        // the current backing (e.g. the very first `addYield` on a near-empty vault).
        IERC20(asset()).safeTransferFrom(msg.sender, address(this), amount);

        _startVesting(amount);
    }

    /// @dev Arms a new tranche after validating the no-overlap / deposit-window guards. The caller
    ///      must have already moved `amount` of `asset` into the vault (so `balanceOf` reflects it
    ///      before `_vestingAmount` is set, avoiding a transient `totalAssets` underflow).
    function _startVesting(uint256 amount) private {
        if (amount == 0) revert ZeroAmount();

        uint256 start = _vestingStart;
        if (start != 0) {
            if (block.timestamp < start + VEST) revert YieldStillVesting(start + VEST);
            // Hold off until the guaranteed deposit window has elapsed, so new capital always has
            // a chance to enter between tranches regardless of how promptly the keeper runs.
            if (block.timestamp < start + VEST + MIN_WINDOW) revert DepositWindowOpen(start + VEST + MIN_WINDOW);
        }

        _vestingAmount = amount;
        _vestingStart = block.timestamp;

        emit YieldAdded(amount, block.timestamp);
    }
```

**File:** docs/content/developers/evm/streaming-yield-vault.mdx (L30-32)
```text
- **A guaranteed deposit window.** `addYield` must wait at least `MIN_WINDOW` (2 hours) past the end of vesting, so every cycle has a deposit window of at least `MIN_WINDOW` regardless of how promptly the keeper runs. The window is the interval `[vestedAt(), nextYieldAt()]`.

The first-depositor inflation/donation attack is mitigated with OpenZeppelin's virtual shares/assets (a non-zero decimals offset). A **seed-and-burn** at deployment is still recommended.
```

**File:** docs/content/developers/evm/streaming-yield-vault.mdx (L113-115)
```text
<Callout type="info">
Deposits and mints are only open **between tranches** — after a tranche fully vests and before the next `addYield`. While a tranche is vesting, `maxDeposit`/`maxMint` return `0` and `deposit`/`mint` revert with the standard `ERC4626ExceededMaxDeposit`/`ERC4626ExceededMaxMint`. Check `maxDeposit(receiver)` (or `vestedAt()`/`nextYieldAt()`) to find the open window. Withdrawals and redeems are always available.
</Callout>
```

**File:** docs/content/developers/evm/streaming-yield-vault.mdx (L168-172)
```text
## Security considerations

- **Standard ERC-20 only.** Fee-on-transfer and rebasing assets desync the vault's accounting; ERC-777 callbacks reintroduce reentrancy vectors the design otherwise avoids.
- **Seed-and-burn at deploy.** Combined with the decimals offset, this removes the empty-vault inflation attack.
- **Mind the cadence.** Minimum cadence is `VEST + MIN_WINDOW` (24h): `addYield` reverts until the prior tranche has vested *and* its deposit window has elapsed. Deposits are closed while a tranche vests, so capital enters in bursts during the window.
```

**File:** sdk/packages/core/test/StreamingYieldVault.t.sol (L90-113)
```text
    function _deposit(address who, uint256 amount) internal returns (uint256 shares) {
        vm.prank(who);
        shares = vault.deposit(amount, who);
    }

    function _redeemAll(address who) internal returns (uint256 assets) {
        uint256 shares = vault.balanceOf(who);
        vm.prank(who);
        assets = vault.redeem(shares, who, who);
    }

    function _addYield(uint256 amount) internal {
        vm.prank(owner);
        vault.addYield(amount);
    }

    /// @dev Seed the vault with a real first deposit and burn the shares. Together with the
    ///      decimals offset this removes the empty-vault edge for the economic tests.
    function _seedAndBurn(uint256 amount) internal {
        vm.prank(seeder);
        uint256 shares = vault.deposit(amount, seeder);
        vm.prank(seeder);
        vault.transfer(address(0xdead), shares);
    }
```
