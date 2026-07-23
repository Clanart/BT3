Audit Report

## Title
Single `MAX_REF_STALENESS` Applied to Both Base and Quote Feeds in Synthetic Mode Allows Stale Prices to Reach Pool Swaps — (`smart-contracts-poc/contracts/AnchoredPriceProvider.sol`)

## Summary

`AnchoredPriceProvider` declares one immutable `MAX_REF_STALENESS` and applies it identically to both `baseFeedId` and `quoteFeedId` inside `_readLeg()`. Because different oracle feeds carry different native heartbeats, no single threshold can be simultaneously safe for both legs in synthetic mode, leading either to stale prices passing the staleness gate or to the pool being permanently bricked for normal trading.

## Finding Description

The contract declares a single staleness threshold: [1](#0-0) 

The constructor accepts only one `_maxRefStaleness` parameter and assigns it to `MAX_REF_STALENESS`: [2](#0-1) 

In synthetic mode (`quoteFeedId != bytes32(0)`), `_getBidAndAskPrice()` calls `_readLeg()` for both feeds: [3](#0-2) 

Inside `_readLeg()`, the same `MAX_REF_STALENESS` is applied to whichever `feedId` is passed: [4](#0-3) 

There is no per-feed staleness parameter. Synthetic mode is an explicitly documented and supported feature: [5](#0-4) 

For a BTC/USD (1 h heartbeat) + USDC/USD (24 h heartbeat) synthetic pair, the deployer faces an impossible choice:

| `MAX_REF_STALENESS` | Base (BTC/USD, 1 h) | Quote (USDC/USD, 24 h) |
|---|---|---|
| 24 h (slower feed) | BTC price can be ~23 h 59 m stale and pass | Always passes |
| 1 h (faster feed) | Always passes | Reverts ~23 out of every 24 hours |

No value is simultaneously safe for both feeds.

## Impact Explanation

**Scenario A — staleness set to slower feed's heartbeat (24 h):** The faster feed (BTC/USD) can be nearly 24 hours stale before the check fires. `getBidAndAskPrice()` returns a synthetic mid computed from a grossly outdated price. A swap executed through `MetricOmmSimpleRouter.exactInputSingle` / `exactInput` / `exactOutput` consumes this stale bid/ask, causing the trader to receive far more or far fewer output tokens than the true market rate warrants. This is a direct bad-price execution impact with loss borne by LPs or the counterparty.

**Scenario B — staleness set to faster feed's heartbeat (1 h):** The slower feed fails the staleness check for most of the day, causing `getBidAndAskPrice()` to revert with `FeedStalled`. The pool is effectively bricked for normal trading hours, denying users access to swap flows — broken core pool functionality.

Both outcomes fall within the allowed impact gate.

## Likelihood Explanation

Synthetic mode is explicitly supported and documented in the NatSpec. Any deployment pairing two feeds with different native heartbeats — a common real-world configuration (volatile/USD base + stablecoin/USD quote) — is structurally affected. No privileged action, malicious setup, or attacker input is required; the misconfiguration is baked in at construction time and cannot be corrected without redeployment. Any unprivileged trader or LP calling the router triggers the impact.

## Recommendation

Replace the single `MAX_REF_STALENESS` with two per-feed immutables:

```solidity
uint256 public immutable MAX_BASE_STALENESS;
uint256 public immutable MAX_QUOTE_STALENESS;
```

Pass the appropriate threshold to each `_readLeg()` call:

```solidity
(uint256 mid, uint256 spreadBps, , bool ok) = _readLeg(baseFeedId, MAX_BASE_STALENESS);
...
(uint256 mid2, uint256 spreadBps2, , bool ok2) = _readLeg(_quote, MAX_QUOTE_STALENESS);
```

Update `_readLeg()` to accept the staleness parameter rather than reading the immutable directly.

## Proof of Concept

1. Deploy `AnchoredPriceProvider` with `baseFeedId` = BTC/USD (1 h heartbeat), `quoteFeedId` = USDC/USD (24 h heartbeat), `MAX_REF_STALENESS` = 86400 (24 h, forced by the slower feed).
2. BTC/USD feed stops updating for 23 hours (e.g., Chainlink node outage).
3. User calls `MetricOmmSimpleRouter.exactInputSingle()` on the BTC/ETH synthetic pool.
4. `_readLeg(baseFeedId)` evaluates `_isStale(refTime, block.timestamp, 86400)` → `(23 h) > 86400` → **false** → staleness check passes.
5. `_getBidAndAskPrice()` returns a synthetic mid computed from a 23-hour-old BTC price.
6. Pool swap executes at the stale price; the trader receives far more (or far fewer) output tokens than the true market rate warrants, with the loss borne by LPs or the counterparty.

### Citations

**File:** smart-contracts-poc/contracts/AnchoredPriceProvider.sol (L67-69)
```text
    /// @notice Optional second feed for synthetic ratio quoting; zero = single-feed (no conversion).
    ///         Synthetic mid = price(baseFeedId) / price(quoteFeedId), e.g. BTC/USD ÷ ETH/USD = BTC/ETH.
    bytes32         public immutable quoteFeedId;
```

**File:** smart-contracts-poc/contracts/AnchoredPriceProvider.sol (L77-77)
```text
    uint256 public immutable MAX_REF_STALENESS;
```

**File:** smart-contracts-poc/contracts/AnchoredPriceProvider.sol (L129-151)
```text
        uint256 _maxRefStaleness,
        uint16  _maxSpreadBps,
        bool    _mutableParams,
        int256  _marginStep,
        address _baseToken,
        address _quoteToken
    ) {
        require(_factory != address(0));
        factory = _factory;

        offchainOracle = IOffchainOracle(_oracle);
        baseFeedId = _baseFeedId;
        quoteFeedId = _quoteFeedId;

        // Tokens live ONLY here (the oracles are token-free): the pair is an explicit,
        // mandatory input — including the synthetic (two-feed) mode, where the factory
        // knows the pair when it creates the pool.
        require(_baseToken != address(0) && _quoteToken != address(0) && _baseToken != _quoteToken);
        baseToken = _baseToken;
        quoteToken = _quoteToken;

        if (_maxRefStaleness > 7 days) revert MaxRefStalenessOutOfBounds(); // 0 allowed = same-block reference
        MAX_REF_STALENESS = _maxRefStaleness;
```

**File:** smart-contracts-poc/contracts/AnchoredPriceProvider.sol (L258-271)
```text
    function _getBidAndAskPrice() internal returns (uint128, uint128) {
        (uint256 mid, uint256 spreadBps, , bool ok) = _readLeg(baseFeedId);
        if (!ok) return (0, type(uint128).max);

        bytes32 _quote = quoteFeedId;
        if (_quote != bytes32(0)) {
            (uint256 mid2, uint256 spreadBps2, , bool ok2) = _readLeg(_quote);
            if (!ok2 || mid2 == 0) return (0, type(uint128).max);
            // Synthetic ratio (8-decimal): mid1 / mid2. Relative uncertainties of a ratio add.
            mid = Math.mulDiv(mid, ORACLE_DECIMALS, mid2);
            spreadBps += spreadBps2;
        }

        return _computeBidAsk(mid, spreadBps);
```

**File:** smart-contracts-poc/contracts/AnchoredPriceProvider.sol (L277-283)
```text
    function _readLeg(bytes32 feedId)
        internal returns (uint256 mid, uint256 spreadBps, uint256 refTime, bool ok)
    {
        (mid, spreadBps, , refTime) = IPricedOracle(address(offchainOracle)).price(feedId, msg.sender);

        // Stale reference → not ok. Clamping to a stale anchor is the one false-safety case.
        if (_isStale(refTime, block.timestamp, MAX_REF_STALENESS)) return (mid, spreadBps, refTime, false);
```
