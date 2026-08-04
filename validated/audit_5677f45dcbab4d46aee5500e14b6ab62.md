## Finding

The `SimplexPaymaster` contract enforces a single global `maxOracleAge` staleness bound against every registered price feed — the shared native/USD oracle and every token/USD oracle — even though its own documentation acknowledges wildly different Chainlink heartbeats across chains and tokens. [1](#0-0) [2](#0-1) 

### Title
Single global `maxOracleAge` staleness bound lets fast-heartbeat Chainlink feeds be treated as fresh while badly stale, causing mispriced paymaster gas charges and swaps - (File: evm/src/utils/SimplexPaymaster.sol)

### Summary
`SimplexPaymaster` stores one `maxOracleAge` value used identically for `nativeOracle` and every `tokenOracle` in `_getOraclePrice`. The contract's own comment states heartbeats vary by more than three orders of magnitude across supported feeds ("BSC stablecoins ~27s, Base/Ethereum stablecoins up to 24h"), yet there is no per-token or per-oracle staleness bound — only one shared threshold, capped at `MAX_ORACLE_AGE = 7 days`.

### Finding Description
`_getOraclePrice` is the sole staleness gate for every price read in the contract:
```solidity
function _getOraclePrice(AggregatorV3Interface oracle, uint8 oracleDecimals) internal view returns (uint256) {
    (, int256 answer, , uint256 updatedAt, ) = oracle.latestRoundData();
    if (answer <= 0) revert InvalidOraclePrice(address(oracle), answer);
    if (block.timestamp - updatedAt > maxOracleAge) {
        revert StaleOraclePrice(address(oracle), updatedAt);
    }
    ...
}
``` [3](#0-2) 

`maxOracleAge` is a single contract-wide variable set via `_setParams`, bounded only by `MAX_ORACLE_AGE` (7 days) and validated to be non-zero — with no per-token override:
```solidity
if (p.maxOracleAge == 0 || p.maxOracleAge > MAX_ORACLE_AGE) revert InvalidOracleAge(p.maxOracleAge);
...
maxOracleAge = p.maxOracleAge;
``` [4](#0-3) 

`TokenConfig` — the per-token registration struct — carries only the oracle address and decimals, with no per-feed heartbeat/age field: [5](#0-4) 

Because a single scalar must simultaneously bound freshness for a ~27s-heartbeat BSC feed and a 24h-heartbeat Ethereum/Base feed, there is no value of `maxOracleAge` that is *correct* for both: setting it tight enough to catch staleness on the fast feed makes the slow feed's legitimate, unstale rounds revert as "stale"; setting it loose enough to accept the slow feed's normal 24h cadence means the fast-heartbeat feed's data can be up to 24 hours old — nearly 3,200x its actual heartbeat — and still pass the check unmodified. This is not a misconfiguration by a malicious governance actor; it is an inherent property of the one-parameter design applied across heterogeneous, correctly-functioning oracles, exactly analogous to the external report's `CHAINLINK_TIMEOUT` (4h) being set far looser than the underlying feed's 1h heartbeat.

### Impact Explanation
`_getOraclePrice`'s output feeds both:
1. `_tokenPrice` → `tokenPrice` returned from `_fetchDetails`, which `PaymasterERC20._erc20Cost` uses to compute exactly how much ERC-20 a user is charged for gas. [6](#0-5) [7](#0-6) 
2. `swapAndDeposit`'s `amountOutMin` computation, which bounds the acceptable execution price when the treasury swaps accumulated stablecoins for native gas. [8](#0-7) 

If a fast-heartbeat feed's price has genuinely moved (e.g. a depeg or a sharp native-asset price swing) but its last update sits anywhere within the shared, loosely-set `maxOracleAge` window, both computations use that outdated price as if fresh. This directly moves paymaster/treasury funds at the wrong amount: users can be charged an incorrect token amount for gas (self-funding governance-approved allowances at the wrong exchange rate), and `swapAndDeposit` can execute against a stale reference price, understating `amountOutMin` and letting the treasury's stablecoins be swapped for less native asset than the true market rate — a direct value-leak from the paymaster's treasury flow, matching the bounty's "paymaster balances ... must move exactly once and only to the rightful beneficiary and amount" pivot.

### Likelihood Explanation
No malicious relayer, prover, or governance action is required. This is inherent to the shared-parameter design as soon as governance registers tokens whose Chainlink feeds have materially different heartbeats than the value chosen for `maxOracleAge` — which the contract's own comments say is expected for its stated deployment targets ("any token with a Chainlink feed" across BSC/Base/Ethereum). Any legitimate `maxOracleAge` chosen to accommodate the slower feeds (up to 24h, and up to the 7-day hard cap) leaves the faster feeds' price data effectively unchecked for staleness relative to their real update cadence, and any ordinary user operation or `swapAndDeposit` call during that window exercises the flawed check with no special preconditions.

### Recommendation
Add a per-token (and separately, for the native oracle) staleness bound in `TokenConfig`/`Params` instead of one global `maxOracleAge`, so each feed's freshness check tracks its actual Chainlink heartbeat, mirroring how `tokenOracleDecimals` is already cached per token.

### Proof of Concept
1. Governance registers a BSC stablecoin feed (heartbeat ~27s) and an Ethereum stablecoin feed (heartbeat up to 24h) via `RegisterToken`, and sets `maxOracleAge` to `1 days` via `UpdateParams` — a reasonable, honest choice that keeps the Ethereum feed usable.
2. The BSC feed's price crashes/depegs at `updatedAt = T`. No new round is pushed (or is delayed) for several hours.
3. At `T + 20 hours`, `block.timestamp - updatedAt (20h) <= maxOracleAge (24h)`, so `_getOraclePrice` accepts the stale answer — nearly 2,700x older than the feed's ~27s heartbeat.
4. `_tokenPrice`/`_fetchDetails` computes `tokenPrice` from this stale value; a UserOp is charged the wrong ERC-20 amount for gas relative to the real market rate, or `swapAndDeposit` executes the treasury's swap against the stale reference, moving funds at the wrong amount.

### Citations

**File:** evm/src/utils/SimplexPaymaster.sol (L77-90)
```text
    struct Params {
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

**File:** evm/src/utils/SimplexPaymaster.sol (L92-97)
```text
    struct TokenConfig {
        AggregatorV3Interface tokenOracle; // token/USD feed
        uint8 tokenOracleDecimals; // cached decimals() of tokenOracle
        uint8 tokenDecimals; // decimals() of the ERC-20
        bool active; // kill-switch per token
    }
```

**File:** evm/src/utils/SimplexPaymaster.sol (L219-238)
```text
        if (p.maxOracleAge == 0 || p.maxOracleAge > MAX_ORACLE_AGE) revert InvalidOracleAge(p.maxOracleAge);
        if (p.swapSlippageBps > MAX_SWAP_SLIPPAGE_BPS) revert InvalidSlippage(p.swapSlippageBps);

        emit ParamsUpdated(
            Params({
                nativeOracle: nativeOracle,
                markupBps: markupBps,
                treasury: treasury,
                maxOracleAge: maxOracleAge,
                swapSlippageBps: swapSlippageBps
            }),
            p
        );

        nativeOracle = p.nativeOracle;
        nativeOracleDecimals = p.nativeOracle.decimals();
        markupBps = p.markupBps;
        treasury = p.treasury;
        maxOracleAge = p.maxOracleAge;
        swapSlippageBps = p.swapSlippageBps;
```

**File:** evm/src/utils/SimplexPaymaster.sol (L309-312)
```text
        uint256 nativeUsd = _getOraclePrice(nativeOracle, nativeOracleDecimals);
        uint256 tokenUsd = _getOraclePrice(cfg.tokenOracle, cfg.tokenOracleDecimals);
        uint256 expectedWei = (amountIn * tokenUsd * 1e18) / (nativeUsd * (10 ** cfg.tokenDecimals));
        uint256 amountOutMin = (expectedWei * (10_000 - swapSlippageBps)) / 10_000;
```

**File:** evm/src/utils/SimplexPaymaster.sol (L366-393)
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
        validationData = 0; // no time-range restriction
    }
```

**File:** evm/src/utils/SimplexPaymaster.sol (L419-424)
```text
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
