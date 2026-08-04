## Analog Found: Missing Chainlink min/max answer bounds check in `SimplexPaymaster._getOraclePrice`

The external report's core broken invariant — trusting `latestRoundData()` without validating the returned price is not saturated at the aggregator's configured `minAnswer`/`maxAnswer` — has a direct, unpatched local analog in `SimplexPaymaster.sol`.

### Title
Chainlink aggregator min/max saturation is not checked in `SimplexPaymaster._getOraclePrice`, causing wrong-price fee charges and swap execution - (File: `evm/src/utils/SimplexPaymaster.sol`)

### Summary
`SimplexPaymaster._getOraclePrice()` only guards against a non-positive answer and staleness by timestamp, but never checks the returned `answer` against the token oracle's configured `minAnswer`/`maxAnswer` circuit-breaker bounds, exactly the vulnerability class described in the Sherlock M-5 report for DODO's `D3Oracle`.

### Finding Description
`_getOraclePrice` is the sole price-fetch primitive feeding both the ERC-20 gas-fee pricing path and the fee-recycling swap path: [1](#0-0) 

It calls `oracle.latestRoundData()` and validates only:
- `answer <= 0` → revert
- staleness via `block.timestamp - updatedAt > maxOracleAge` → revert

There is no check that `answer` lies strictly between the underlying Chainlink aggregator's `minAnswer` and `maxAnswer`. Chainlink price feeds clamp reported values at these bounds during extreme volatility (e.g. a depeg or crash) — the feed keeps reporting `minAnswer`/`maxAnswer` as a "live", non-stale, positive value that passes both existing guards while silently no longer reflecting the real market price.

This price flows into two fund-moving paths:
1. `_tokenPrice()` → `_fetchDetails()`, which computes `tokenPrice` used by `PaymasterERC20` to charge the UserOp sender the ERC-20 equivalent of gas cost: [2](#0-1) [3](#0-2) 

2. `swapAndDeposit()`, which derives `expectedWei` and `amountOutMin` directly from `_getOraclePrice` results to bound the on-chain swap execution price: [4](#0-3) 

Neither existing guard (`answer <= 0`, staleness) stops a saturated-at-bound price from being accepted, since a clamped value is still positive and still updated within `maxOracleAge`.

### Impact Explanation
- In `_fetchDetails`/`_tokenPrice`: users get charged the wrong ERC-20 amount for gas — either overcharged (loss of user funds to the paymaster/treasury) or undercharged (paymaster drains its own native deposit at the EntryPoint without adequate reimbursement), a direct "wrong beneficiary or amount" fund-movement bug.
- In `swapAndDeposit`: `amountOutMin` is derived from the same corrupted price. If the token oracle is saturated low or the native oracle saturated high (or vice versa), `expectedWei`/`amountOutMin` will be computed from a stale-but-passing bound value rather than the true market price, letting the swap execute at a price far from the real market rate and directly moving treasury-bound funds at the wrong rate. This matches the "stealing or loss of funds"/"transaction manipulation" impact classes in scope.

### Likelihood Explanation
This requires no privileged actor, malicious relayer, or governance compromise — it triggers automatically whenever any registered token's Chainlink feed (or the native asset feed) saturates during a market crash or extreme volatility event, which is a realistic, permissionless-to-trigger market condition rather than an attacker action. The `swapAndDeposit` path is treasury-gated, but the mispricing in `_fetchDetails`/`_tokenPrice` is hit on every ordinary UserOp during such an event with zero privilege required.

### Recommendation
Cache each oracle's `minAnswer`/`maxAnswer` (as returned by the aggregator's `aggregator()` or configured at registration time) and, in `_getOraclePrice`, revert if `answer <= minAnswer || answer >= maxAnswer`, mirroring the Chainlink-recommended "check the latest answer against reasonable limits" pattern cited in the original report.

### Proof of Concept
1. Register a token whose Chainlink `AggregatorV3Interface` feed has `minAnswer = 1e6` (8 decimals).
2. Market crash: real price would report e.g. `5e5`, but the aggregator clamps and returns `answer = 1e6`, with `updatedAt` still fresh.
3. `_getOraclePrice` passes both checks (`answer > 0`, not stale) and returns `1e6` as if it were the true price.
4. `_tokenPrice()`/`_fetchDetails()` compute `tokenPrice` from this stale-but-valid-looking clamped value, charging every subsequent UserOp's sender an ERC-20 fee based on a price roughly 2x the real market rate (or the inverse, undercharging, depending on which side saturates) — an unbounded per-transaction wrong-amount charge with no attacker action required, purely from the unguarded oracle boundary condition already present in `_getOraclePrice`. [1](#0-0)

### Citations

**File:** evm/src/utils/SimplexPaymaster.sol (L299-330)
```text
    function swapAndDeposit(address token, uint256 amountIn) external {
        if (msg.sender != treasury) revert UnauthorizedCall();
        address router = IDispatcher(host()).uniswapV2Router();
        if (router == address(0)) revert InvalidRouter(router);
        TokenConfig memory cfg = tokenConfigs[token];
        if (address(cfg.tokenOracle) == address(0)) revert TokenNotRegistered(token);

        uint256 balance = IERC20(token).balanceOf(address(this));
        if (amountIn == 0 || amountIn > balance) amountIn = balance;

        uint256 nativeUsd = _getOraclePrice(nativeOracle, nativeOracleDecimals);
        uint256 tokenUsd = _getOraclePrice(cfg.tokenOracle, cfg.tokenOracleDecimals);
        uint256 expectedWei = (amountIn * tokenUsd * 1e18) / (nativeUsd * (10 ** cfg.tokenDecimals));
        uint256 amountOutMin = (expectedWei * (10_000 - swapSlippageBps)) / 10_000;

        address[] memory path = new address[](2);
        path[0] = token;
        path[1] = IUniswapV2Router02(router).WETH();

        IERC20(token).forceApprove(router, amountIn);
        uint256[] memory amounts = IUniswapV2Router02(router).swapExactTokensForETH(
            amountIn,
            amountOutMin,
            path,
            address(this),
            block.timestamp
        );

        uint256 deposited = address(this).balance;
        entryPoint().depositTo{value: deposited}(address(this));
        emit FeesRecycled(token, amountIn, amounts[1], deposited);
    }
```

**File:** evm/src/utils/SimplexPaymaster.sol (L374-393)
```text
    function _fetchDetails(
        PackedUserOperation calldata userOp,
        bytes32 /* userOpHash */
    ) internal view override returns (uint256 validationData, IERC20 token, uint256 tokenPrice) {
        bytes calldata data = userOp.paymasterData();
        if (data.length < 21) revert InvalidPaymasterData(data.length);

        uint8 mode = uint8(data[0]);
        if (mode > 0x01) revert InvalidMode(mode);

        address tokenAddr = address(bytes20(data[1:21]));

        TokenConfig memory cfg = tokenConfigs[tokenAddr];
        if (address(cfg.tokenOracle) == address(0)) revert TokenNotRegistered(tokenAddr);
        if (!cfg.active) revert TokenNotActive(tokenAddr);

        tokenPrice = _tokenPrice(cfg);
        token = IERC20(tokenAddr);
        validationData = 0; // no time-range restriction
    }
```

**File:** evm/src/utils/SimplexPaymaster.sol (L417-424)
```text
    // ── Pricing ──────────────────────────────────────────────────────

    function _tokenPrice(TokenConfig memory cfg) internal view returns (uint256) {
        uint256 nativeUsd = _getOraclePrice(nativeOracle, nativeOracleDecimals);
        uint256 tokenUsd = _getOraclePrice(cfg.tokenOracle, cfg.tokenOracleDecimals);

        return (nativeUsd * (10 ** cfg.tokenDecimals) * (10_000 + markupBps)) / (tokenUsd * 10_000);
    }
```

**File:** evm/src/utils/SimplexPaymaster.sol (L426-442)
```text
    /// @dev Fetch a Chainlink price normalized to 8 decimals.
    ///      Reverts on stale or non-positive answers.
    function _getOraclePrice(AggregatorV3Interface oracle, uint8 oracleDecimals) internal view returns (uint256) {
        (, int256 answer, , uint256 updatedAt, ) = oracle.latestRoundData();

        if (answer <= 0) revert InvalidOraclePrice(address(oracle), answer);
        if (block.timestamp - updatedAt > maxOracleAge) {
            revert StaleOraclePrice(address(oracle), updatedAt);
        }

        if (oracleDecimals < 8) {
            return uint256(answer) * (10 ** (8 - oracleDecimals));
        } else if (oracleDecimals > 8) {
            return uint256(answer) / (10 ** (oracleDecimals - 8));
        }
        return uint256(answer);
    }
```
