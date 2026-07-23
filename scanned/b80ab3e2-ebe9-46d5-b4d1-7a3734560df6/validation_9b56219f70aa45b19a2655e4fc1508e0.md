The direct analog exists in `removeLiquidity`. Let me verify the full transfer path and check the `MetricOmmPoolLiquidityAdder` for completeness.

### Title
LP principal permanently frozen when position owner is blacklisted by pool token — (`metric-core/contracts/MetricOmmPool.sol` / `metric-core/contracts/libraries/LiquidityLib.sol`)

---

### Summary

`removeLiquidity` enforces `msg.sender == owner` and unconditionally transfers withdrawn tokens to `owner` with no `recipient` parameter. If `owner` is blacklisted by a pool token that implements address-level transfer restrictions (USDC, USDT), every call to `removeLiquidity` reverts and the LP's principal is permanently frozen inside the pool.

---

### Finding Description

`MetricOmmPool.removeLiquidity` enforces a strict identity check:

```solidity
// MetricOmmPool.sol line 206
if (msg.sender != owner) revert NotPositionOwner();
``` [1](#0-0) 

It then delegates to `LiquidityLib.removeLiquidity`, which transfers the recovered tokens directly and unconditionally to `owner`:

```solidity
// LiquidityLib.sol lines 242-246
if (amount0Removed > 0) {
    IERC20(ctx.token0).safeTransfer(owner, amount0Removed);
}
if (amount1Removed > 0) {
    IERC20(ctx.token1).safeTransfer(owner, amount1Removed);
}
``` [2](#0-1) 

There is no `recipient` argument anywhere in the call chain. The position key is `(owner, salt)` — there is no position-transfer, delegation, or operator-withdrawal mechanism that would allow a different address to receive the tokens on behalf of a blacklisted `owner`.

The `addLiquidity` path intentionally allows `msg.sender != owner` (operator pattern), but `removeLiquidity` is explicitly stricter per the NatSpec:

> "Requires `msg.sender == owner` (`NotPositionOwner` otherwise). No callback: tokens are transferred out directly." [3](#0-2) 

---

### Impact Explanation

If a liquidity provider's address is added to the USDC or USDT blacklist after depositing, `safeTransfer(owner, ...)` will revert on every `removeLiquidity` call. Because there is no alternative withdrawal path, no position-transfer function, and no recipient override, the LP's full principal (both token legs across all bins) is permanently locked in the pool contract. The pool's accounting is also corrupted: shares are burned and bin balances are decremented before the transfer, so the tokens are irrecoverable even by the factory admin.

---

### Likelihood Explanation

USDC and USDT blacklisting is explicitly in scope per the allowed impact gate ("non-standard ERC20 behavior except USDC/USDT"). Blacklisting of an active LP address is a low-probability event (matching the external report's Medium classification), but the consequence — permanent, total loss of principal — is severe. No privileged action or malicious setup is required; the trigger is a standard USDC/USDT compliance action against the LP's address.

---

### Recommendation

Add a `recipient` parameter to `removeLiquidity` (analogous to the `recipient` already present on `swap`). The caller identity check can remain on `msg.sender == owner` while the token destination becomes `recipient`:

```solidity
// MetricOmmPool.sol
function removeLiquidity(
    address owner,
    uint80 salt,
    LiquidityDelta calldata deltas,
+   address recipient,
    bytes calldata extensionData
) external nonReentrant(PoolActions.REMOVE_LIQUIDITY) returns (...) {
    if (msg.sender != owner) revert NotPositionOwner();
    ...
    LiquidityLib.removeLiquidity(
-       _liquidityContext(), owner, salt, deltas, ...
+       _liquidityContext(), owner, salt, deltas, recipient, ...
    );
}

// LiquidityLib.sol
- IERC20(ctx.token0).safeTransfer(owner, amount0Removed);
- IERC20(ctx.token1).safeTransfer(owner, amount1Removed);
+ IERC20(ctx.token0).safeTransfer(recipient, amount0Removed);
+ IERC20(ctx.token1).safeTransfer(recipient, amount1Removed);
```

---

### Proof of Concept

1. Alice (address `0xAlice`) provides liquidity to a USDC/WETH Metric OMM pool. Her position key is `(0xAlice, salt)`.
2. USDC blacklists `0xAlice` (e.g., due to a compliance action).
3. Alice calls `removeLiquidity(0xAlice, salt, deltas, "")`.
4. `MetricOmmPool` passes the check `msg.sender == owner` (both are `0xAlice`).
5. `LiquidityLib.removeLiquidity` burns Alice's shares, decrements bin balances, then calls `IERC20(USDC).safeTransfer(0xAlice, amount0Removed)`.
6. USDC's `transfer` reverts because `0xAlice` is blacklisted.
7. The entire transaction reverts. Alice's shares are not burned (state is rolled back), but she can never successfully execute step 3 — every attempt reverts at step 6.
8. Alice has no alternative: she cannot delegate removal to another address, cannot specify a different recipient, and there is no position-transfer mechanism. Her USDC principal is permanently frozen in the pool. [1](#0-0) [4](#0-3)

### Citations

**File:** metric-core/contracts/MetricOmmPool.sol (L199-212)
```text
  function removeLiquidity(address owner, uint80 salt, LiquidityDelta calldata deltas, bytes calldata extensionData)
    external
    nonReentrant(PoolActions.REMOVE_LIQUIDITY)
    returns (uint256 amount0Removed, uint256 amount1Removed)
  {
    if (deltas.binIdxs.length == 0) return (0, 0);
    if (deltas.binIdxs.length != deltas.shares.length) revert LiquidityDeltaLengthMismatch();
    if (msg.sender != owner) revert NotPositionOwner();
    _beforeRemoveLiquidity(msg.sender, owner, salt, deltas, extensionData);
    (amount0Removed, amount1Removed) = LiquidityLib.removeLiquidity(
      _liquidityContext(), owner, salt, deltas, binTotals, _binStates, _binTotalShares, _positionBinShares
    );
    _afterRemoveLiquidity(msg.sender, owner, salt, deltas, amount0Removed, amount1Removed, extensionData);
  }
```

**File:** metric-core/contracts/libraries/LiquidityLib.sol (L161-251)
```text
  function removeLiquidity(
    PoolContext memory ctx,
    address owner,
    uint80 salt,
    LiquidityDelta calldata deltas,
    BinTotals storage binTotals,
    mapping(int256 => BinState) storage binStates,
    mapping(int256 => uint256) storage binTotalShares,
    mapping(bytes32 => uint256) storage positionBinShares
  ) public returns (uint256 amount0Removed, uint256 amount1Removed) {
    unchecked {
      uint256 length = deltas.binIdxs.length;
      if (length == 0) return (0, 0);

      uint256 totalToken0ToRemoveScaled = 0;
      uint256 totalToken1ToRemoveScaled = 0;

      BinBalanceDelta[] memory binBalanceDeltas = new BinBalanceDelta[](length);

      for (uint256 i = 0; i < length; i++) {
        int256 binIdx = deltas.binIdxs[i];
        uint256 sharesToRemove = deltas.shares[i];

        if (binIdx < ctx.lowestBin || binIdx > ctx.highestBin) {
          revert IMetricOmmPoolActions.InvalidBinIndex(binIdx);
        }
        if (sharesToRemove == 0) continue;

        {
          // safe because -128 <= LOWEST_BIN <= HIGHEST_BIN <= 127 (enforced by factory)
          // forge-lint: disable-next-line(unsafe-typecast)
          bytes32 posKey = _positionBinKey(owner, salt, int8(binIdx));
          uint256 binTotalSharesVal = binTotalShares[binIdx];
          uint256 userShares = positionBinShares[posKey];

          if (userShares < sharesToRemove) {
            revert IMetricOmmPoolActions.InsufficientLiquidity(sharesToRemove, userShares);
          }
          uint256 newUserShares = userShares - sharesToRemove;
          if (newUserShares > 0 && newUserShares < ctx.minimalMintableLiquidity) {
            revert IMetricOmmPoolActions.MinimalLiquidity(newUserShares, ctx.minimalMintableLiquidity);
          }

          BinState storage binState = binStates[binIdx];
          uint256 amount0Scaled = _checkedMul(binState.token0BalanceScaled, sharesToRemove) / binTotalSharesVal;
          uint256 amount1Scaled = _checkedMul(binState.token1BalanceScaled, sharesToRemove) / binTotalSharesVal;

          // casting to uint104 is safe because amount0Scaled and amount1Scaled are less than token(0|1)BalanceScaled
          // forge-lint: disable-next-line(unsafe-typecast)
          binState.token0BalanceScaled -= uint104(amount0Scaled);
          // forge-lint: disable-next-line(unsafe-typecast)
          binState.token1BalanceScaled -= uint104(amount1Scaled);
          binTotalShares[binIdx] = binTotalSharesVal - sharesToRemove;
          positionBinShares[posKey] = newUserShares;

          totalToken0ToRemoveScaled += amount0Scaled;
          totalToken1ToRemoveScaled += amount1Scaled;

          binBalanceDeltas[i] = BinBalanceDelta({
            // safe because amount0Scaled is bounded by uint104 bin balances.
            // forge-lint: disable-next-line(unsafe-typecast)
            delta0Scaled: -int256(amount0Scaled),
            // safe because amount1Scaled is bounded by uint104 bin balances.
            // forge-lint: disable-next-line(unsafe-typecast)
            delta1Scaled: -int256(amount1Scaled)
          });
        }
      }

      if (totalToken0ToRemoveScaled > 0) {
        // forge-lint: disable-next-line(unsafe-typecast)
        binTotals.scaledToken0 = uint128(uint256(binTotals.scaledToken0) - totalToken0ToRemoveScaled);
      }
      if (totalToken1ToRemoveScaled > 0) {
        // forge-lint: disable-next-line(unsafe-typecast)
        binTotals.scaledToken1 = uint128(uint256(binTotals.scaledToken1) - totalToken1ToRemoveScaled);
      }

      (amount0Removed, amount1Removed) =
        _deltasScaledToExternal(totalToken0ToRemoveScaled, totalToken1ToRemoveScaled, ctx, Math.Rounding.Floor);

      if (amount0Removed > 0) {
        IERC20(ctx.token0).safeTransfer(owner, amount0Removed);
      }
      if (amount1Removed > 0) {
        IERC20(ctx.token1).safeTransfer(owner, amount1Removed);
      }

      emit IMetricOmmPoolActions.LiquidityRemoved(owner, salt, deltas.binIdxs, binBalanceDeltas, deltas.shares);
    }
  }
```

**File:** metric-core/contracts/interfaces/IMetricOmmPool/IMetricOmmPoolActions.sol (L164-174)
```text
  /// @notice Burn shares across bins for `(owner, salt)` and send underlying tokens to `owner`.
  /// @dev Requires `msg.sender == owner` (`NotPositionOwner` otherwise). No callback: tokens are transferred out directly.
  /// @param owner Must equal `msg.sender`.
  /// @param salt Position salt with `owner`.
  /// @param deltas Parallel arrays of bins and share burns.
  /// @param extensionData Opaque bytes forwarded to liquidity extensions (beforeRemoveLiquidity / afterRemoveLiquidity).
  /// @return amount0Removed Total token0 sent from the pool to `owner` (native).
  /// @return amount1Removed Total token1 sent from the pool to `owner` (native).
  function removeLiquidity(address owner, uint80 salt, LiquidityDelta calldata deltas, bytes calldata extensionData)
    external
    returns (uint256 amount0Removed, uint256 amount1Removed);
```
