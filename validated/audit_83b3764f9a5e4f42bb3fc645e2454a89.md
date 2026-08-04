## Finding

### Title
Missing Chainlink circuit-breaker bounds check in `SimplexPaymaster._getOraclePrice()` allows underpriced ERC-20 gas sponsorship during a token depeg - (File: `evm/src/utils/SimplexPaymaster.sol`)

### Summary
`SimplexPaymaster._getOraclePrice()` validates a Chainlink `latestRoundData()` answer only for positivity and staleness, but never checks it against the aggregator's `minAnswer`/`maxAnswer` circuit-breaker bounds. This is the exact bug class from the external report (missing Chainlink circuit-breaker validation), reproduced locally in the pricing path that determines how many ERC-20 tokens a UserOp sender is charged for gas sponsorship.

### Finding Description
`_getOraclePrice()` only guards against a non-positive answer and staleness: [1](#0-0) 

There is no `minAnswer`/`maxAnswer` (circuit-breaker) bound applied to `answer`. This value feeds directly into `_tokenPrice()`, which is used both for the ERC-20 gas cost charged to a UserOp sender and for offchain quoting: [2](#0-1) 

`_tokenPrice()` computes `tokenPrice = nativeUsd * 10^tokenDecimals * markup / (tokenUsd * 10000)`, i.e. `tokenUsd` sits in the denominator. If a registered stablecoin token depegs downward in the real market (e.g. crashes toward $0.10) but Chainlink's token/USD aggregator hits its `minAnswer` floor (commonly set close to $1 for stablecoin feeds) and freezes there, `tokenUsd` reported by the oracle stays artificially high relative to the token's true market value. Since `tokenUsd` is in the denominator, an inflated `tokenUsd` produces an artificially *low* `tokenPrice` — i.e., the paymaster charges far fewer ERC-20 units per wei of gas than the token is actually worth.

This mirrors the report precisely: Chainlink's circuit breaker returns a frozen bound instead of the real crashed price, and the absence of a min/max sanity check lets that stale-but-"fresh" (per `updatedAt`) frozen price be trusted as ground truth, corrupting a downstream valuation (`tokenPrice`, analogous to `vStrategy.price()` in the original report) used to move real value (native gas paid from the paymaster's EntryPoint deposit) against a nominal ERC-20 charge.

The contract's own documentation acknowledges the oracle risk but only bounds it by limiting the *approval/permit size per call* — it does not bound the *number of calls*, and does not stop the underlying price acceptance flaw: [3](#0-2) 

Because the paymaster is described as "fully onchain, permissionless" and callable by any UserOp sender, an attacker can repeat the small-value exploit across many UserOps/permits for as long as the depeg persists and the feed remains within `maxOracleAge` (configurable up to `MAX_ORACLE_AGE = 7 days`): [4](#0-3) 

### Impact Explanation
Each sponsored UserOp causes the EntryPoint to spend real native gas from the paymaster's deposit (funded by the treasury), while the attacker repays in a stablecoin that is worth substantially less than the frozen oracle price implies. This is a direct, repeatable loss of protocol funds (`stealing or loss of funds` / `transaction manipulation` under the accepted impact classes) driven purely by trusting an unvalidated Chainlink circuit-breaker value, reachable by any unprivileged UserOp sender without any relayer/prover/admin compromise.

### Likelihood Explanation
This requires only a real-world stablecoin depeg event on a token registered in `tokenConfigs` (a normal market condition, not an adversarial infrastructure compromise) coinciding with the feed's circuit breaker being hit — a documented, historically observed Chainlink behavior. No malicious peer, relayer, prover, or governance action is needed; the attacker only needs to hold the depegged token and submit ordinary `UserOperation`s.

### Recommendation
Add explicit `minAnswer`/`maxAnswer` bounds validation in `_getOraclePrice()` (either hardcoded per feed or read via the aggregator/its underlying `AccessControlledOffchainAggregator`), rejecting or pausing pricing when the reported answer equals or approaches those bounds, mirroring the stale/non-positive checks already present:

```solidity
if (answer <= MIN_PRICE || answer >= MAX_PRICE) revert InvalidOraclePrice(address(oracle), answer);
```

Additionally consider a secondary sanity source (e.g., a TWAP or a max deviation check against the last accepted price) before trusting a single Chainlink round for token pricing used in fund-moving paths.

### Proof of Concept
1. A stablecoin `T` is registered via `RegisterToken` with a Chainlink `T/USD` feed whose aggregator has `minAnswer = 0.98e8`.
2. `T` depegs in the real market to `$0.10` due to a protocol failure/bank run. Chainlink's aggregator hits its floor and continues reporting `answer = 0.98e8` with a fresh `updatedAt` (within `maxOracleAge`).
3. `_getOraclePrice()` passes both the non-positive and staleness checks and returns `0.98e8` as `tokenUsd`, even though `T`'s real value is `~0.10e8`.
4. Attacker acquires cheap, depegged `T` on the open market (real cost ≈10% of face value), signs an EIP-2612 permit, and submits a UserOp with `paymasterData` mode `0x00` referencing `T`.
5. `_fetchDetails()` → `_tokenPrice()` computes `tokenPrice` using the inflated `tokenUsd = 0.98e8`, charging the attacker as if `T` were worth ~$0.98, while the EntryPoint pays out real native gas from the paymaster's deposit.
6. Attacker repeats across many UserOps/permits (each individually small per the documented permit-size mitigation, but unbounded in count) to drain the paymaster's EntryPoint deposit/treasury value using tokens acquired at a fraction of their assumed price.

### Citations

**File:** evm/src/utils/SimplexPaymaster.sol (L52-59)
```text
/// @dev Security model. Solvers grant this contract ERC-20 allowances, so a
///      compromise must never translate into large withdrawals from their
///      accounts. There is no privileged key: every administrative action —
///      upgrades, parameter changes, token registry, withdrawals — is an
///      onAccept request authenticated as originating from Hyperbridge
///      governance and delivered by the local host. Clients additionally keep
///      allowances and permit amounts small (a few dollars), bounding exposure
///      to the residual allowance even against a malicious oracle.
```

**File:** evm/src/utils/SimplexPaymaster.sol (L99-112)
```text
    /// @dev Hard cap on the governance-configurable markup (50%).
    uint256 public constant MAX_MARKUP_BPS = 5_000;

    /// @dev Hard ceiling on the governance-configurable oracle staleness bound.
    uint256 public constant MAX_ORACLE_AGE = 7 days;

    /// @dev Hard cap on the governance-configurable swap slippage (10%).
    uint256 public constant MAX_SWAP_SLIPPAGE_BPS = 1_000;

    /// @dev Caps the caller-supplied postOp gas limit. Unbounded, the EntryPoint's
    ///      unused-gas penalty is drained from this contract's deposit to a
    ///      caller-chosen beneficiary; the cap keeps that penalty under the
    ///      `_postOpCost` cushion the user already pays.
    uint256 public constant MAX_POST_OP_GAS_LIMIT = 100_000;
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
