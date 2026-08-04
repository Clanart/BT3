I have sufficient evidence now to write up the finding. This confirms `SimplexPaymaster` uses raw Chainlink `latestRoundData()` on multiple chains (comment mentions "BSC stablecoins ~27s, Base/Ethereum stablecoins up to 24h") with only a `block.timestamp - updatedAt > maxOracleAge` staleness check — no L2 sequencer-uptime-feed check, matching the exact bug class from the external report.

### Title
`SimplexPaymaster` trusts Chainlink price feeds without an L2 sequencer-uptime check, enabling stale-price fund extraction on L2 - ([File: evm/src/utils/SimplexPaymaster.sol])

### Summary
`SimplexPaymaster` is a permissionless ERC-4337 paymaster deployed on multiple EVM chains, including L2 rollups (per the code's own comments referencing "BSC", "Base", "Ethereum" stablecoin heartbeats) [1](#0-0) . Its `_getOraclePrice` function is the sole gate for pricing every gas payment: it calls `AggregatorV3Interface.latestRoundData()` and only rejects the answer if `block.timestamp - updatedAt > maxOracleAge` [2](#0-1) . This is exactly the pattern flagged in the external report: on L2s (e.g. Arbitrum, Base, or any OP-stack/Orbit chain), when the sequencer is degraded or catching up on a backlog, `block.timestamp` can advance while the Chainlink price feed itself has not been updated for an extended, attacker-favorable window, or L2 timestamps otherwise decouple from true off-chain freshness. Because there is no Chainlink `SequencerUptimeFeed` check, a stale-but-within-`maxOracleAge` price is silently trusted.

### Finding Description
`_getOraclePrice` is used both by `_tokenPrice` (called from `_fetchDetails`, which every `UserOp` prefund/charge in `PaymasterERC20._postOp` depends on) and by `swapAndDeposit` to compute the swap's `amountOutMin` [3](#0-2) [4](#0-3) . The only defense against a bad price is the `maxOracleAge` bound, which is governance-configurable up to `MAX_ORACLE_AGE = 7 days` [5](#0-4)  and, per the comment, is deliberately set as high as 24 hours for some tokens/chains [1](#0-0) . No code path calls or checks an L2 `SequencerUptimeFeed`, so the contract cannot distinguish "price is fresh because the market is quiet" from "price is stale because the L2 sequencer stalled and the feed could not be updated, yet block.timestamp still reports a recent value once the sequencer resumes and drains its backlog." An attacker who is simply an ordinary UserOp sender (no relayer/operator/oracle compromise needed) can time `_validatePaymasterUserOp`/`_fetchDetails` calls to land inside this stale-but-accepted window and pay in a stablecoin at a favorable, outdated `tokenUsd`/`nativeUsd` ratio, extracting value from the paymaster's treasury-funded EntryPoint deposit.

### Impact Explanation
Every gas charge computed via `_tokenPrice` (and every swap's minimum output via `swapAndDeposit`) is derived from an oracle read that is only checked for staleness relative to `block.timestamp`, not relative to actual sequencer liveness. On an affected L2 this allows systematically underpaying for sponsored gas (or extracting excess ETH via mispriced `swapAndDeposit` minimums) directly from the paymaster's funds — a real loss of funds from a production contract, matching the "stealing or loss of funds" / "transaction manipulation" impact categories.

### Likelihood Explanation
No privileged actor, relayer, or prover compromise is required — any address able to submit a `UserOp` through the bundler/EntryPoint can trigger `_fetchDetails`/`_tokenPrice` during a sequencer-degraded or backlog-draining window. The condition is externally observable (sequencer status is public), so the attacker only needs to time submission, making this a directly and cheaply exploitable production path once deployed on an affected L2.

### Recommendation
Add an L2 sequencer-uptime check (per Chainlink's documented pattern) to `_getOraclePrice`, gating on both the sequencer's `latestRoundData()` status and a grace period after it comes back online, before trusting `nativeOracle`/`tokenOracle` answers, mirroring the recommendation in the source report.

### Proof of Concept
1. Deploy/operate `SimplexPaymaster` on an L2 whose sequencer can stall (e.g. Arbitrum/Base) with `maxOracleAge` set near its allowed ceiling (up to 24h per the code comment, hard-capped at 7 days).
2. Sequencer stalls or falls behind; the underlying `tokenOracle`/`nativeOracle` feed stops updating, but once the sequencer resumes, `block.timestamp` for the next executed transaction is recent while `updatedAt` is still within `maxOracleAge`.
3. Attacker submits a `UserOp` with `paymasterData` referencing a token whose true market price has since diverged from the stale oracle answer; `_fetchDetails` → `_tokenPrice` → `_getOraclePrice` returns the stale price without reverting because `block.timestamp - updatedAt <= maxOracleAge`.
4. `PaymasterERC20._postOp` charges the user at the stale rate, letting the attacker pay less token value than the true gas cost, draining the paymaster's `treasury`-funded balance over repeated ops. [2](#0-1)

### Citations

**File:** evm/src/utils/SimplexPaymaster.sol (L84-86)
```text
        /// @notice Maximum oracle staleness. Chainlink heartbeats vary per chain
        ///         (BSC stablecoins ~27s, Base/Ethereum stablecoins up to 24h).
        uint256 maxOracleAge;
```

**File:** evm/src/utils/SimplexPaymaster.sol (L102-106)
```text
    /// @dev Hard ceiling on the governance-configurable oracle staleness bound.
    uint256 public constant MAX_ORACLE_AGE = 7 days;

    /// @dev Hard cap on the governance-configurable swap slippage (10%).
    uint256 public constant MAX_SWAP_SLIPPAGE_BPS = 1_000;
```

**File:** evm/src/utils/SimplexPaymaster.sol (L309-312)
```text
        uint256 nativeUsd = _getOraclePrice(nativeOracle, nativeOracleDecimals);
        uint256 tokenUsd = _getOraclePrice(cfg.tokenOracle, cfg.tokenOracleDecimals);
        uint256 expectedWei = (amountIn * tokenUsd * 1e18) / (nativeUsd * (10 ** cfg.tokenDecimals));
        uint256 amountOutMin = (expectedWei * (10_000 - swapSlippageBps)) / 10_000;
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
