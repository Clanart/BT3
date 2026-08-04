## Title
Missing L2 sequencer-uptime check in `SimplexPaymaster` Chainlink price oracle allows stale-price exploitation causing paymaster fund loss - (File: `evm/src/utils/SimplexPaymaster.sol`)

### Summary
`SimplexPaymaster` prices ERC‑4337 gas payments using two Chainlink feeds (`nativeOracle` and each token's `tokenOracle`) via `_getOraclePrice`, which only validates `answer > 0` and `block.timestamp - updatedAt <= maxOracleAge` [1](#0-0) . The contract is explicitly designed to be deployed on multiple chains including Base and BSC, per its own documentation comments referencing "BNB/USD on BSC, ETH/USD on Ethereum" and per-chain heartbeats "(BSC stablecoins ~27s, Base/Ethereum stablecoins up to 24h)" [2](#0-1) . Neither `_getOraclePrice`, `_tokenPrice`, nor `swapAndDeposit` check an L2 sequencer-uptime feed, so on sequencer-based L2s (e.g. Base) a sequencer outage can leave the reported Chainlink price frozen yet still within the (generously large, up-to-24h) staleness window, letting the contract treat a stale/incorrect rate as fresh — exactly the pattern flagged in the external report for `TradingModule.getOraclePrice`.

### Finding Description
`_getOraclePrice` is the sole gate on price freshness for every gas-payment conversion in the paymaster: [3](#0-2) 

It is called by `_tokenPrice` (used in `_fetchDetails`, which determines how much ERC‑20 a UserOp sender is charged for gas) [4](#0-3)  and by `swapAndDeposit` (used to compute `amountOutMin` when recycling fees through a Uniswap V2 router) [5](#0-4) .

Unlike the Chainlink-recommended pattern for L2 deployments (checking an L2 sequencer uptime feed before trusting `latestRoundData()`), this contract has no such check anywhere in the codebase — a repo-wide search for `sequencer` only turns up matches in the Arbitrum consensus/state-machine clients, unrelated to this oracle logic. When the underlying chain's sequencer is down, the last Chainlink `updatedAt` timestamp simply stops advancing while `block.timestamp` (computed from L1 or from a delayed/backfilled batch once the sequencer resumes) can still fall within `maxOracleAge`, especially given the contract's own stated heartbeat/staleness tolerance of "up to 24h" for Base/Ethereum stablecoin feeds. During that window `_getOraclePrice` returns a price that looks "fresh" by the code's only check, but is actually the last pre-outage market price.

### Impact Explanation
Any unprivileged UserOp sender can submit gas-payment transactions priced off the stale rate during/after a sequencer outage. If the true native-asset or token price has since diverged from the frozen oracle value, the attacker pays the paymaster's treasury less real value than the gas actually costs (or drains disproportionately favorable swap outputs via `swapAndDeposit`'s oracle-derived `amountOutMin`), causing direct loss of funds to the paymaster/treasury — matching the bounty's "stealing or loss of funds" / "logic attacks via price manipulation" impact class. This requires no malicious relayer, prover, or admin: it is exploitable by any normal ERC‑4337 UserOp sender interacting with a public entrypoint (`_fetchDetails`/paymaster validation).

### Likelihood Explanation
Likelihood depends on an L2 sequencer outage occurring on a chain where this paymaster is deployed with a sequencer-based execution model (e.g. Base), combined with the token/native price moving during the outage. Sequencer downtime events, while not the norm, have occurred historically on OP-stack L2s, and the contract's own comments show it explicitly targets multi-chain deployment including chains with sequencers and unusually large (24h) staleness tolerances, which widens the exploitable window significantly compared to Arbitrum's typical few-minute-to-hour feed heartbeats.

### Recommendation
Add an L2 sequencer uptime feed check (per Chainlink's `L2SequencerUptimeFeed` pattern) in `_getOraclePrice`, requiring both that the sequencer is up and that a `gracePeriod` has elapsed since it came back online, before trusting `latestRoundData()`. This check should be applied consistently in `_tokenPrice` and `swapAndDeposit`'s price derivation, and should be chain-aware/configurable via governance (`UpdateParams`) since not all deployment targets (e.g., Ethereum mainnet) have a sequencer.

### Proof of Concept
1. `SimplexPaymaster` is deployed on Base (or any OP-stack L2) with `maxOracleAge` set near the documented "up to 24h" tolerance for Base/Ethereum stablecoins.
2. The Base sequencer goes offline; the `nativeOracle`/`tokenOracle` Chainlink feeds stop updating `updatedAt`, freezing at the pre-outage price.
3. During the outage (and for a period after recovery, within `maxOracleAge`), the true market price of the native asset diverges from the frozen oracle price.
4. An attacker submits a UserOp via the public `_fetchDetails`/paymaster validation path; `_getOraclePrice` passes both its checks (`answer > 0`, staleness within `maxOracleAge`) despite the price being stale relative to the sequencer outage.
5. The attacker pays gas fees in the ERC‑20 token at the frozen, unfavorable-to-the-paymaster rate, extracting value from the treasury with each UserOp processed during the window — no privileged actor, relayer, or governance compromise required.

### Citations

**File:** evm/src/utils/SimplexPaymaster.sol (L78-90)
```text
        /// @notice Native asset / USD oracle (BNB/USD on BSC, ETH/USD on Ethereum, etc.)
        AggregatorV3Interface nativeOracle;
        /// @notice Markup in basis points (100 = 1%). Applied on top of the oracle price.
        uint256 markupBps;
        /// @notice Receives markup surplus and EntryPoint deposit withdrawals.
        address treasury;
        /// @notice Maximum oracle staleness. Chainlink heartbeats vary per chain
        ///         (BSC stablecoins ~27s, Base/Ethereum stablecoins up to 24h).
        uint256 maxOracleAge;
        /// @notice Slippage tolerance in basis points applied to the
        ///         oracle-derived expected output in {swapAndDeposit}.
        uint256 swapSlippageBps;
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
