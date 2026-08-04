## Title
Missing Chainlink min/max circuit-breaker check in `SimplexPaymaster._getOraclePrice` allows draining real ETH for worthless tokens - (File: `evm/src/utils/SimplexPaymaster.sol`)

### Summary
`SimplexPaymaster._getOraclePrice()` only guards against a non-positive answer and staleness, but never checks the returned answer against the underlying Chainlink aggregator's `minAnswer`/`maxAnswer` circuit-breaker bounds [1](#0-0) . If a registered token (e.g. a stablecoin) suffers a severe de-peg/crash below the feed's `minAnswer` floor, the aggregator keeps returning `minAnswer` instead of reverting, exactly as described in the external Chainlink circuit-breaker report. Because this price feeds directly into gas pricing (`_tokenPrice`) and swap execution (`swapAndDeposit`), the paymaster will keep charging users based on the stale floor price rather than the real, collapsed market price.

### Finding Description
`_getOraclePrice` is the single price-fetching primitive used everywhere pricing happens in the contract: [2](#0-1) 

It is consumed by:
- `_tokenPrice`, which sets the exchange rate `tokenPrice = nativeUsd * 10^tokenDecimals * (1+markup) / tokenUsd` used by `PaymasterERC20` to compute how many token units a UserOp must pay for gas [3](#0-2) .
- `swapAndDeposit`, which derives `amountOutMin` for the router swap from the same on-chain oracle prices [4](#0-3) .

No code path anywhere in the contract compares `answer` against the aggregator's configured `minAnswer`/`maxAnswer`. If `cfg.tokenOracle` (the token/USD feed for a registered stablecoin) hits its aggregator's `minAnswer` floor during a de-peg crash, `_getOraclePrice` happily returns that stale floor value as `tokenUsd` because it is still `> 0` and freshly updated (Chainlink still posts rounds at the pinned floor). This is the exact class of bug in the referenced report: the aggregator returns a wrong, pinned price instead of reverting.

### Impact Explanation
When `tokenUsd` is pinned at `minAnswer` (above the real, crashed value), `_tokenPrice` computes `tokenPrice = nativeUsd * 10^decimals * markup / tokenUsd`, which is deflated relative to the true price. A holder of the crashed/worthless token can then pay gas through `PaymasterERC20`/`_fetchDetails` using far fewer real-value tokens than the gas is actually worth, effectively draining the paymaster's EntryPoint deposit (real ETH) for near-worthless tokens — unauthorized value extraction / fund loss from the paymaster, matching the bounty's "stealing or loss of funds" impact. The same stale price additionally corrupts `swapAndDeposit`'s `expectedWei`/`amountOutMin` calculation, causing the treasury's recycling swap to accept a bad execution price derived from the same broken input.

### Likelihood Explanation
This requires only a real-world market event (a stablecoin de-peg/crash hitting the registered token's Chainlink feed floor) — no compromised relayer, prover, admin, or governance actor is needed, and any user who can obtain the crashed token can exploit it via ordinary `PackedUserOperation`s through the public paymaster validation flow (`_validatePaymasterUserOp` → `_fetchDetails` → `_tokenPrice` → `_getOraclePrice`). Given that registered tokens are explicitly described as "USDC, USDT, or any token with a Chainlink feed," a de-peg event is a realistic operational scenario, not a contrived edge case.

### Recommendation
In `_getOraclePrice`, fetch and cache each aggregator's `minAnswer`/`maxAnswer` bounds (via `AggregatorV2V3Interface.minAnswer()`/`maxAnswer()` or the underlying `aggregator()` proxy) and revert (e.g. via a new `OraclePriceOutOfBounds` error) whenever `answer <= minAnswer` or `answer >= maxAnswer`, in addition to the existing staleness and non-positive checks.

### Proof of Concept
1. Governance registers token `T` with Chainlink feed `F` via `RegisterToken` [5](#0-4) .
2. `T` de-pegs catastrophically; `F`'s underlying aggregator hits its `minAnswer` circuit breaker and keeps returning `minAnswer` (e.g. $0.90) every round instead of the real price (e.g. $0.01).
3. A user submits a UserOp with `paymasterData` mode `0x01` referencing `T`. `_fetchDetails` calls `_tokenPrice(cfg)` → `_getOraclePrice(cfg.tokenOracle, ...)`, which returns `$0.90` unchallenged since `answer > 0` and `updatedAt` is fresh [6](#0-5) .
4. `PaymasterERC20` charges the user's `T` balance based on this inflated $0.90 valuation while `T` is actually worth $0.01 on the open market — the attacker pays gas at ~1% of its real market rate, and the paymaster's EntryPoint deposit (real ETH) is drained accordingly over repeated UserOps.

### Citations

**File:** evm/src/utils/SimplexPaymaster.sol (L243-260)
```text
    function _registerToken(address token, AggregatorV3Interface oracle) internal {
        if (token == address(0) || address(oracle) == address(0)) revert ZeroAddress();

        bool isNew = !tokenConfigs[token].active && address(tokenConfigs[token].tokenOracle) == address(0);

        tokenConfigs[token] = TokenConfig({
            tokenOracle: oracle,
            tokenOracleDecimals: oracle.decimals(),
            tokenDecimals: IERC20Metadata(token).decimals(),
            active: true
        });

        if (isNew) {
            registeredTokens.push(token);
        }

        emit TokenRegistered(token, address(oracle));
    }
```

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

**File:** evm/src/utils/SimplexPaymaster.sol (L366-391)
```text
    /// @dev Returns the token to charge and its price relative to native gas.
    ///
    ///      PaymasterERC20 computes `erc20Cost = weiCost * tokenPrice / 1e18`,
    ///      so tokenPrice must be token base units per wei, scaled by 1e18:
    ///        tokenPrice = (nativeUsd * 10^tokenDecimals) / tokenUsd
    ///      e.g. BNB at $600, USDC at $1 with 6 decimals: 0.001 BNB (1e15 wei)
    ///      should cost 0.60 USDC (600000 units), giving tokenPrice = 6e8, which
    ///      is exactly (600e8 * 1e6) / 1e8. Markup is applied on top.
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
```

**File:** evm/src/utils/SimplexPaymaster.sol (L419-442)
```text
    function _tokenPrice(TokenConfig memory cfg) internal view returns (uint256) {
        uint256 nativeUsd = _getOraclePrice(nativeOracle, nativeOracleDecimals);
        uint256 tokenUsd = _getOraclePrice(cfg.tokenOracle, cfg.tokenOracleDecimals);

        return (nativeUsd * (10 ** cfg.tokenDecimals) * (10_000 + markupBps)) / (tokenUsd * 10_000);
    }

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
