Based on my analysis, I have found the following:

**Key observations:**
1. `PriceProviderL2.sol` and `ProtectedPriceProviderL2.sol` are deployed on L2 chains (Arbitrum, Base, Avalanche, BSC, Berachain per the registry) and implement `_isStale()` with a `FUTURE_TOLERANCE` for clock skew — but contain **no Arbitrum/L2 sequencer uptime check**.
2. The registry ABI confirms a separate Chainlink-based L2 provider (`ChainlinkVerifierL2`) **does** have `sequencerUptimeFeed` and `GRACE_PERIOD` — the exact same asymmetry as the external report.
3. `MAX_TIME_DELTA` can be set up to 7 days, meaning a sequencer outage shorter than `MAX_TIME_DELTA` leaves a window where pre-outage stale prices pass the staleness check.

---

### Title
Missing Arbitrum Sequencer Uptime Check in `PriceProviderL2` and `ProtectedPriceProviderL2` Allows Stale-Price Swaps After Sequencer Restart — (`smart-contracts-poc/contracts/PriceProviderL2.sol`, `smart-contracts-poc/contracts/ProtectedPriceProviderL2.sol`)

### Summary

`PriceProviderL2` and `ProtectedPriceProviderL2` are deployed on L2 networks (Arbitrum, Base, Avalanche, BSC, Berachain) and supply bid/ask prices to every pool swap via `getBidAndAskPrice()`. Both contracts implement a `MAX_TIME_DELTA` staleness check and an L2 clock-skew `FUTURE_TOLERANCE`, but neither checks whether the L2 sequencer is live before trusting oracle data. The Chainlink-based L2 provider in the same codebase (`ChainlinkVerifierL2`) correctly implements `sequencerUptimeFeed` and `GRACE_PERIOD`. The omission in the Pyth/compressed-oracle providers creates a window after sequencer restart where pre-outage stale prices pass the staleness check and reach pool swaps.

### Finding Description

`PriceProviderL2._getBidAndAskPrice()` reads price data from the offchain oracle and applies only a timestamp-based staleness check:

```solidity
if (_isStale(refTime, block.timestamp, MAX_TIME_DELTA, FUTURE_TOLERANCE)) {
    return (0, type(uint128).max);
}
```

`_isStale` returns `true` only when `(block.timestamp - refTime) > MAX_TIME_DELTA`. If the sequencer was down for a duration shorter than `MAX_TIME_DELTA` (which can be up to 7 days), the pre-outage oracle price is still considered fresh when the sequencer restarts. During the restart window — before keepers push new Pyth prices — the pool quotes and executes swaps at the last pre-outage price.

`ProtectedPriceProviderL2` has the identical gap: its `_computeBidAsk` calls the same `_isStale` with no sequencer liveness gate.

The Chainlink-based L2 provider in the registry explicitly stores and checks `sequencerUptimeFeed` with a `GRACE_PERIOD`, confirming the protocol authors are aware of the requirement for Chainlink feeds but omitted it for the Pyth/compressed-oracle path.

### Impact Explanation

During the sequencer-restart grace window, the pool's `getBidAndAskPrice()` returns a stale pre-outage bid/ask. A trader can:
- Swap against a stale favorable price, extracting value from LP positions (LPs receive less input than the current market price warrants, or give more output).
- In the worst case, if the asset price dropped significantly during the outage, the pool quotes a bid above the true market price, allowing a trader to sell at an inflated rate — a direct loss of LP principal.

This matches the "Bad-price execution: stale bid/ask quote reaches a pool swap" impact gate.

### Likelihood Explanation

L2 sequencer outages are documented historical events (Arbitrum has experienced multiple). The attack requires no special privilege — any user can submit a swap transaction immediately after sequencer restart. The window lasts until keepers push a fresh Pyth price update, which may take seconds to minutes. `MAX_TIME_DELTA` up to 7 days means even a multi-hour outage leaves prices appearing fresh.

### Recommendation

Add a sequencer uptime check to `getBidAndAskPrice()` in both `PriceProviderL2` and `ProtectedPriceProviderL2`, mirroring the pattern already used in `ChainlinkVerifierL2`:

```solidity
AggregatorV3Interface public immutable sequencerUptimeFeed;
uint256 public constant GRACE_PERIOD = 3600; // 1 hour

function _isSequencerActive() internal view returns (bool) {
    (, int256 answer, uint256 startedAt,,) = sequencerUptimeFeed.latestRoundData();
    if (answer == 1) return false; // sequencer is down
    if (block.timestamp - startedAt < GRACE_PERIOD) return false; // in grace period
    return true;
}

function getBidAndAskPrice() external override returns (uint128 bid, uint128 ask) {
    if (!_isSequencerActive()) revert SequencerDown();
    (bid, ask) = _getBidAndAskPrice();
    if (bid == 0 || ask == type(uint128).max) revert FeedStalled();
}
```

On chains without a sequencer uptime feed (e.g., Ethereum mainnet), deploy the non-L2 variants (`PriceProvider`, `ProtectedPriceProvider`) which correctly omit this check.

### Proof of Concept

1. Pool on Arbitrum is configured with `PriceProviderL2`, `MAX_TIME_DELTA = 3600` (1 hour).
2. At `t=0`, ETH/USDC price is 3000 USDC. Oracle `refTime = t=0`.
3. At `t=1`, Arbitrum sequencer goes down. No new oracle updates can be pushed.
4. Off-chain, ETH price drops to 2000 USDC.
5. At `t=1800` (30 min later), sequencer restarts. `block.timestamp - refTime = 1800 < MAX_TIME_DELTA = 3600` → staleness check passes.
6. Attacker immediately calls `swap(zeroForOne=false, ...)` — sells USDC to buy ETH at the stale 3000 USDC/ETH ask price.
7. Pool pays out ETH valued at 3000 USDC when true market value is 2000 USDC — LP suffers a 33% loss on the trade.
8. Keepers push a fresh Pyth update seconds later, closing the window — but the attacker's transaction executes first. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4) [6](#0-5) [7](#0-6) [8](#0-7)

### Citations

**File:** smart-contracts-poc/contracts/PriceProviderL2.sol (L92-95)
```text
        if (_maxTimeDelta == 0 || _maxTimeDelta > 7 days) revert MaxTimeDeltaOutOfBounds();
        if (_futureTolerance > 1 hours) revert FutureToleranceOutOfBounds();
        MAX_TIME_DELTA   = _maxTimeDelta;
        FUTURE_TOLERANCE = _futureTolerance;
```

**File:** smart-contracts-poc/contracts/PriceProviderL2.sol (L123-128)
```text
    function getBidAndAskPrice()
        external override returns (uint128 bid, uint128 ask)
    {
        (bid, ask) = _getBidAndAskPrice();
        if (bid == 0 || ask == type(uint128).max) revert FeedStalled();
    }
```

**File:** smart-contracts-poc/contracts/PriceProviderL2.sol (L135-150)
```text
    function _isStale(
        uint256 refTime,
        uint256 nowTs,
        uint256 maxDelta,
        uint256 futureTol
    ) internal pure returns (bool) {
        if (refTime == 0) return true;

        if (refTime > nowTs) {
            // refTime in the future: tolerate only within futureTol
            return (refTime - nowTs) > futureTol;
        }

        // refTime in the past or equal: check age
        return (nowTs - refTime) > maxDelta;
    }
```

**File:** smart-contracts-poc/contracts/PriceProviderL2.sol (L208-217)
```text
    function _getBidAndAskPrice() internal returns (uint128, uint128) {
        // 1. Read via the unified price(feedId, pool) path, forwarding the pool (msg.sender).
        //    refTime is already in seconds.
        (uint256 mid, uint256 spread, , uint256 refTime) =
            IPricedOracle(address(offchainOracle)).price(offchainFeedId, msg.sender);

        // 2. Staleness check
        if (_isStale(refTime, block.timestamp, MAX_TIME_DELTA, FUTURE_TOLERANCE)) {
            return (0, type(uint128).max);
        }
```

**File:** smart-contracts-poc/contracts/ProtectedPriceProviderL2.sol (L96-99)
```text
        if (_maxTimeDelta == 0 || _maxTimeDelta > 7 days) revert MaxTimeDeltaOutOfBounds();
        if (_futureTolerance > 1 hours) revert FutureToleranceOutOfBounds();
        MAX_TIME_DELTA   = _maxTimeDelta;
        FUTURE_TOLERANCE = _futureTolerance;
```

**File:** smart-contracts-poc/contracts/ProtectedPriceProviderL2.sol (L196-209)
```text
    function _getBidAndAskPrice() internal returns (uint128, uint128) {
        (uint256 mid, uint256 spread, , uint256 refTime) =
            IPricedOracle(address(offchainOracle)).price(offchainFeedId, msg.sender);
        return _computeBidAsk(mid, spread, refTime);
    }

    /// @dev Downstream pricing: staleness, price guard, confidence spread, marginStep.
    function _computeBidAsk(uint256 price, uint256 spread, uint256 refTime)
        internal view returns (uint128, uint128)
    {
        // 1. Staleness check
        if (_isStale(refTime, block.timestamp, MAX_TIME_DELTA, FUTURE_TOLERANCE)) {
            return (0, type(uint128).max);
        }
```

**File:** smart-contracts-poc/contract-registry/versions/registry.json (L631-636)
```json
                },
                {
                  "name": "_sequencerUptimeFeed",
                  "type": "address",
                  "internalType": "address"
                },
```

**File:** smart-contracts-poc/contract-registry/versions/registry.json (L5685-5790)
```json
        "ChainlinkVerifierL2": {
          "abi": [
            {
              "type": "constructor",
              "inputs": [
                {
                  "name": "_sequencerUptimeFeed",
                  "type": "address",
                  "internalType": "address"
                }
              ],
              "stateMutability": "nonpayable"
            },
            {
              "type": "function",
              "name": "GRACE_PERIOD",
              "inputs": [],
              "outputs": [
                {
                  "name": "",
                  "type": "uint256",
                  "internalType": "uint256"
                }
              ],
              "stateMutability": "view"
            },
            {
              "type": "function",
              "name": "sequencerUptimeFeed",
              "inputs": [],
              "outputs": [
                {
                  "name": "",
                  "type": "address",
                  "internalType": "contract AggregatorV3Interface"
                }
              ],
              "stateMutability": "view"
            },
            {
              "type": "event",
              "name": "ClOracleRemoved",
              "inputs": [
                {
                  "name": "token",
                  "type": "address",
                  "indexed": true,
                  "internalType": "address"
                }
              ],
              "anonymous": false
            },
            {
              "type": "event",
              "name": "ClOracleSet",
              "inputs": [
                {
                  "name": "token",
                  "type": "address",
                  "indexed": true,
                  "internalType": "address"
                },
                {
                  "name": "oracle",
                  "type": "address",
                  "indexed": true,
                  "internalType": "address"
                },
                {
                  "name": "heartbeat",
                  "type": "uint32",
                  "indexed": false,
                  "internalType": "uint32"
                }
              ],
              "anonymous": false
            },
            {
              "type": "event",
              "name": "ClOracleStateSet",
              "inputs": [
                {
                  "name": "token",
                  "type": "address",
                  "indexed": true,
                  "internalType": "address"
                },
                {
                  "name": "oracle",
                  "type": "address",
                  "indexed": true,
                  "internalType": "address"
                }
              ],
              "anonymous": false
            },
            {
              "type": "error",
              "name": "ClOracleNotFound",
              "inputs": []
            }
          ],
          "methodIdentifiers": {
            "GRACE_PERIOD()": "c1a287e2",
            "sequencerUptimeFeed()": "a7264705"
          }
```
