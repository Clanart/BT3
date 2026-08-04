This confirms the exact analog to the M-4 bug class exists in `SimplexPaymaster.sol`. The single `maxOracleAge` variable at line 120 is used for both `nativeOracle` (native/USD, e.g., BNB/USD ~27s heartbeat) and every registered `tokenOracle` (token/USD, which per the contract's own comment at lines 84-85 can have "up to 24h" heartbeats on Base/Ethereum). Both go through the identical `_getOraclePrice` staleness check at line 432.

### Title
Single `maxOracleAge` staleness bound applied to Chainlink feeds with heterogeneous heartbeats causes stale-price gas mispricing or spurious DoS - (File: evm/src/utils/SimplexPaymaster.sol)

### Summary
`SimplexPaymaster` charges UserOp senders ERC-20 tokens for gas by converting price via two independent Chainlink feeds — `nativeOracle` (e.g. BNB/USD, ~27s heartbeat per the contract's own doc comment) and each token's `tokenOracle` (which the contract explicitly documents can have up to 24h heartbeats on Base/Ethereum). Both feeds are checked for staleness against the exact same governance-configured `maxOracleAge` value in `_getOraclePrice` [1](#0-0) , which is exactly the M-4 bug class: one shared "outdated" threshold cannot correctly bound feeds with materially different update frequencies.

### Finding Description
`Params.maxOracleAge` is a single `uint256` stored once in `maxOracleAge` [2](#0-1)  and applied identically to both `nativeOracle` and `cfg.tokenOracle` inside `_tokenPrice` and `_getOraclePrice`: [3](#0-2) 

The contract's own documentation acknowledges the heterogeneity ("BSC stablecoins ~27s, Base/Ethereum stablecoins up to 24h") [4](#0-3) , but no mechanism exists to configure per-feed staleness bounds — `TokenConfig` only stores the oracle address and cached decimals, not an age bound [5](#0-4) . Governance is forced into one of two failure modes when setting `maxOracleAge` via `UpdateParams` (validated only by a flat `0 < age <= 7 days` bound) [6](#0-5) :
- Set it tight enough for fast-heartbeat native feeds (e.g. ~27s–5min) → any token registered with a slower-heartbeat feed (e.g. a 24h-heartbeat stablecoin feed) will make `_fetchDetails`/`_tokenPrice` revert on essentially every legitimate `updatedAt` gap that is normal for that oracle, permanently DoS'ing gas payment for that token.
- Set it loose enough for the slow token feed (e.g. 24h+) → the fast-heartbeat `nativeOracle` can go stale far longer than its actual heartbeat without reverting, so `nativeUsd` in `_tokenPrice` can be computed from a badly outdated native price, causing users to be charged the wrong ERC-20 amount for gas (undercharge drains protocol markup/paymaster funds over time; overcharge harms users).

This directly corrupts the value `tokenPrice` returned by `_fetchDetails`/`_tokenPrice`, which `PaymasterERC20._postOp`/prefund logic uses to size the `transferFrom` amount charged to the UserOp sender — i.e., the actual token amount moved from a user's wallet to the paymaster.

### Impact Explanation
This falls under "unauthorized transaction manipulation" / "wrong amount" impacts: the paymaster computes and pulls an incorrect ERC-20 amount from users' wallets whenever the shared bound is loose enough to admit a stale `nativeOracle` read, or, in the tight configuration, legitimately available tokens become permanently unusable for gas payment (functional fund-flow denial) even though nothing is actually wrong with the token's oracle. Given the wide, documented spread between native feed heartbeats (~27s) and token feed heartbeats (up to 24h) that this exact contract targets across multiple chains, one of these failure modes is essentially guaranteed under any single governance-chosen `maxOracleAge`.

### Likelihood Explanation
High likelihood: this is not an edge case requiring an attacker — it is a deterministic consequence of the contract's own multi-chain, multi-token design (documented in its own comments) combined with a single global staleness parameter. Any deployment supporting both a fast-heartbeat native/USD feed and a slow-heartbeat token/USD feed hits one of the two failure modes during normal operation, with no privileged/malicious actor required, satisfying the bounty's "unprivileged" and "no malicious peer" constraints.

### Recommendation
Add a per-feed `maxAge` field to `TokenConfig` (and a corresponding `nativeMaxOracleAge` alongside `maxOracleAge`, or fold it into `Params`), and check `block.timestamp - updatedAt` against the feed-specific bound instead of the single shared `maxOracleAge` in `_getOraclePrice`. Validate each configured age against the feed's actual Chainlink heartbeat when the token/native oracle is registered.

### Proof of Concept
1. Governance deploys `SimplexPaymaster` on a chain, calling `initialize`/`UpdateParams` with `nativeOracle` = a fast feed (e.g. 27s heartbeat) and `maxOracleAge` set generously to 24h so a slow-heartbeat token feed (e.g. 24h heartbeat) remains usable, per `_setParams` [7](#0-6) .
2. The `nativeOracle`'s Chainlink aggregator stops updating (deviation/liveness issue) for, say, 6 hours — well past its 27s heartbeat but far under the configured 24h `maxOracleAge`.
3. A UserOp is submitted; `_fetchDetails` → `_tokenPrice` → `_getOraclePrice(nativeOracle, ...)` succeeds because `block.timestamp - updatedAt (6h) <= maxOracleAge (24h)` [8](#0-7) , returning a stale `nativeUsd`.
4. `tokenPrice` computed in `_tokenPrice` [9](#0-8)  is now based on the stale native price; `PaymasterERC20` charges the user's ERC-20 balance using this incorrect conversion rate, moving the wrong token amount for the gas actually consumed.

### Citations

**File:** evm/src/utils/SimplexPaymaster.sol (L84-86)
```text
        /// @notice Maximum oracle staleness. Chainlink heartbeats vary per chain
        ///         (BSC stablecoins ~27s, Base/Ethereum stablecoins up to 24h).
        uint256 maxOracleAge;
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

**File:** evm/src/utils/SimplexPaymaster.sol (L118-122)
```text
    AggregatorV3Interface public nativeOracle;
    uint8 public nativeOracleDecimals;
    uint256 public maxOracleAge;
    uint256 public markupBps;
    address public treasury;
```

**File:** evm/src/utils/SimplexPaymaster.sol (L215-238)
```text
    function _setParams(Params memory p) internal {
        if (address(p.nativeOracle) == address(0)) revert ZeroAddress();
        if (p.treasury == address(0)) revert ZeroAddress();
        if (p.markupBps > MAX_MARKUP_BPS) revert InvalidMarkup(p.markupBps);
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

**File:** evm/src/utils/SimplexPaymaster.sol (L419-434)
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
```
