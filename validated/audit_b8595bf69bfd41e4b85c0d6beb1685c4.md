### Title
Single `MAX_REF_STALENESS` Applied to Both Base and Quote Feeds in Synthetic Mode Allows Stale Prices to Reach Pool Swaps — (`smart-contracts-poc/contracts/AnchoredPriceProvider.sol`)

---

### Summary

`AnchoredPriceProvider` uses one immutable `MAX_REF_STALENESS` to validate freshness for **both** `baseFeedId` and `quoteFeedId` in synthetic-ratio mode. Because different oracle feeds carry different native heartbeats, a single threshold cannot be simultaneously correct for both legs, mirroring the M-12 bug class exactly.

---

### Finding Description

In synthetic mode (`quoteFeedId != bytes32(0)`), `_getBidAndAskPrice()` calls `_readLeg()` twice — once for the base feed and once for the quote feed: [1](#0-0) 

Inside `_readLeg()`, the staleness gate is: [2](#0-1) 

The same `MAX_REF_STALENESS` immutable is applied to **both** legs. There is no per-feed staleness parameter — the constructor accepts only one value: [3](#0-2) 

Consider a synthetic BTC/ETH pool built from `baseFeedId = BTC/USD` (Chainlink heartbeat: 1 h) and `quoteFeedId = USDC/USD` (Chainlink heartbeat: 24 h). The deployer must pick **one** `MAX_REF_STALENESS`:

| Choice | Effect on base (BTC/USD, 1 h) | Effect on quote (USDC/USD, 24 h) |
|---|---|---|
| 24 h (slower feed) | BTC price can be 23 h 59 m stale and still pass | Quote feed always passes |
| 1 h (faster feed) | Base feed always passes | Quote feed reverts ~23 out of every 24 hours |

There is no value that is simultaneously safe for both feeds.

---

### Impact Explanation

**Scenario A — `MAX_REF_STALENESS` set to the slower feed's heartbeat (e.g., 24 h):**  
The faster feed (e.g., BTC/USD) can be nearly 24 hours stale before the check fires. A pool swap executed through `MetricOmmSimpleRouter.exactInputSingle` / `exactInput` / `exactOutput` will consume a grossly outdated synthetic mid-price. The pool pays out tokens at the wrong ratio; the counterparty (LP or trader) absorbs the loss. This is a direct bad-price execution impact.

**Scenario B — `MAX_REF_STALENESS` set to the faster feed's heartbeat (e.g., 1 h):**  
The slower feed fails the staleness check for most of the day, causing `getBidAndAskPrice()` to return `(0, type(uint128).max)` → `FeedStalled` revert. The pool is effectively bricked for normal trading hours, denying users access to their funds via the router.

Both outcomes are in the allowed impact gate: Scenario A is "bad-price execution: stale bid/ask quote reaches a pool swap"; Scenario B is "broken core pool functionality causing loss of funds or unusable swap flows."

---

### Likelihood Explanation

Synthetic mode is an explicitly supported and documented feature (`quoteFeedId` is a named immutable with a NatSpec example). Any deployment that uses two feeds with different native heartbeats — a common real-world configuration (e.g., volatile/USD base + stablecoin/USD quote) — is affected. No privileged action or malicious setup is required; the misconfiguration is structural and baked in at construction.

---

### Recommendation

Introduce a **per-feed** staleness threshold. Replace the single `MAX_REF_STALENESS` with two immutables:

```solidity
uint256 public immutable MAX_BASE_STALENESS;
uint256 public immutable MAX_QUOTE_STALENESS;
```

Pass both to `_readLeg()` and apply the appropriate one per call:

```solidity
(uint256 mid, uint256 spreadBps, , bool ok) = _readLeg(baseFeedId, MAX_BASE_STALENESS);
...
(uint256 mid2, uint256 spreadBps2, , bool ok2) = _readLeg(_quote, MAX_QUOTE_STALENESS);
```

This mirrors the fix applied in the referenced JOJO commit (separate `heartbeatInterval` per feed).

---

### Proof of Concept

1. Deploy `AnchoredPriceProvider` with:
   - `baseFeedId` = BTC/USD (Chainlink heartbeat: 1 h)
   - `quoteFeedId` = USDC/USD (Chainlink heartbeat: 24 h)
   - `MAX_REF_STALENESS` = 86400 (24 h) — forced by the slower feed
2. BTC/USD feed stops updating (e.g., Chainlink node outage) for 23 hours.
3. User calls `MetricOmmSimpleRouter.exactInputSingle()` on the BTC/ETH synthetic pool.
4. `_readLeg(baseFeedId)` checks `(23 h) > MAX_REF_STALENESS (24 h)` → **false** → staleness check passes.
5. `_getBidAndAskPrice()` returns a synthetic mid computed from a 23-hour-old BTC price.
6. Pool swap executes at the stale price; the trader receives far more (or far fewer) output tokens than the true market rate warrants, with the loss borne by LPs or the counterparty.

### Citations

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
