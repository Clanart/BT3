## Analysis

The external report's core broken invariant is: **"freshness" checks based purely on `updatedAt`/`block.timestamp` deltas are insufficient on L2 rollups, because during sequencer downtime the L2 clock can stall while a stale round still looks "fresh" once the sequencer resumes and back-processes transactions."**

I searched the Hyperbridge/YieldFi analog space and the only genuinely comparable component is `SimplexPaymaster.sol`, which pulls Chainlink price data through the exact same pattern (`latestRoundData` → staleness check → normalize decimals) that the report criticizes, and which is explicitly documented to run on Base — an OP-Stack L2 with a real sequencer.

### Title
Missing L2 sequencer-uptime check in `SimplexPaymaster::_getOraclePrice` allows stale Chainlink prices to be trusted as fresh - (`evm/src/utils/SimplexPaymaster.sol`)

### Summary
`SimplexPaymaster` prices ERC-20 gas payments using two Chainlink feeds (native/USD and token/USD) fetched in `_getOraclePrice`, which is documented to be deployed on L2s such as Base [1](#0-0) . The staleness guard only compares `block.timestamp - updatedAt` against `maxOracleAge`, with no check that the L2 sequencer is up, so during a sequencer outage a stale-but-recently-reported round can be accepted as fresh once the sequencer resumes and the feed's clock "catches up."

### Finding Description
`_getOraclePrice` performs no sequencer-uptime verification: [2](#0-1) 

This is called by `_tokenPrice` (used in every `_fetchDetails` gas-payment computation) and by `swapAndDeposit`'s slippage-bound calculation: [3](#0-2) [4](#0-3) 

On an OP-Stack L2 like Base, if the sequencer goes down and later resumes, Chainlink's L2 feeds can report an `updatedAt` that looks recent relative to `block.timestamp` even though the underlying price is stale relative to real-world market movement during the outage window. There is no `AggregatorV2V3Interface` sequencer-uptime feed check (the standard `answer == 0` / `startedAt` grace-period pattern from Chainlink's L2 docs) anywhere in this contract or in `PaymasterERC20`/`HyperApp` it inherits from.

### Impact Explanation
Every `UserOperation` priced through this paymaster (`_fetchDetails` → `_tokenPrice` → `_getOraclePrice`) and every treasury-triggered `swapAndDeposit` computes `amountOutMin` from the same unguarded price. If a stale price diverges materially from the true market price during/after sequencer downtime:
- Users could be charged a `tokenPrice` far below the paymaster's real gas cost, letting an attacker drain the paymaster's native-asset deposit in the `EntryPoint` by submitting many cheaply-priced UserOps — direct fund loss to the paymaster (a shared Hyperbridge-governed contract), matching the "stealing or loss of funds" bounty class.
- `swapAndDeposit`'s `amountOutMin` derived from the same stale price could permit an unfavorable swap execution, again a fund-loss vector, though this path is treasury-gated (`msg.sender != treasury` revert) so it is lower likelihood.

The `_fetchDetails`/gas-charging path is reachable by any unprivileged UserOp sender, so this is the more relevant public-entrypoint impact.

### Likelihood Explanation
This requires an actual L2 sequencer outage/recovery window on the deployed chain (Base is called out explicitly in the code comments) — a real, periodically-occurring operational condition on OP-Stack rollups, not a contrived or purely theoretical scenario, and requires no malicious relayer, prover, or governance actor. The existing `maxOracleAge` guard does not stop it because the guard only measures elapsed time against the feed's own `updatedAt`, which is exactly the value that becomes misleading during/after sequencer downtime.

### Recommendation
Add a Chainlink L2 sequencer-uptime feed check (e.g. `AggregatorV2V3Interface` on the `0x429...` sequencer-uptime feed for the target L2) in `_getOraclePrice`, reverting if the sequencer is down or the grace period since it came back up has not elapsed, mirroring Chainlink's documented consumer pattern, before trusting any `latestRoundData()` result.

### Proof of Concept
1. Deploy `SimplexPaymaster` on Base with `maxOracleAge` set to a typical stablecoin heartbeat (e.g. 24h per the code's own comment) [1](#0-0) .
2. Simulate/observe an OP-Stack sequencer outage where the L2 chain halts producing blocks (or produces them with a stalled clock) for a window during which the real off-chain USD price of the native asset or token drops or rises sharply.
3. When the sequencer resumes, `nativeOracle.latestRoundData()`/`cfg.tokenOracle.latestRoundData()` return an `updatedAt` that satisfies `block.timestamp - updatedAt < maxOracleAge`, so `_getOraclePrice` returns the stale pre-outage price without reverting.
4. An attacker submits UserOps priced via `_fetchDetails` → `_tokenPrice` using this stale, favorable price, paying far less token value than the paymaster's real native-gas cost, draining the paymaster's `EntryPoint` deposit over repeated calls.

### Citations

**File:** evm/src/utils/SimplexPaymaster.sol (L84-86)
```text
        /// @notice Maximum oracle staleness. Chainlink heartbeats vary per chain
        ///         (BSC stablecoins ~27s, Base/Ethereum stablecoins up to 24h).
        uint256 maxOracleAge;
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
