### Title
SimplexPaymaster oracle staleness check has no Arbitrum sequencer-uptime guard, allowing stale-price gas mispricing after a sequencer outage - (File: `evm/src/utils/SimplexPaymaster.sol`)

### Summary
`SimplexPaymaster` prices ERC-20 gas payments using Chainlink `AggregatorV3Interface` feeds and only guards against staleness via `block.timestamp - updatedAt > maxOracleAge` [1](#0-0) . This is deployed to Arbitrum Mainnet, whose price feed address is explicitly configured in the indexer's Chainlink feed registry [2](#0-1) . Like the original D3Oracle report, there is no check on Chainlink's L2 sequencer-uptime feed, so a sequencer outage/recovery window lets a stale (but not-yet-`maxOracleAge`-expired) round be treated as fresh.

### Finding Description
`_getOraclePrice` is the sole gate used both for `_tokenPrice` (charging users for gas) and for `swapAndDeposit`'s minimum-output computation: [3](#0-2) 

It checks only `answer <= 0` and `block.timestamp - updatedAt > maxOracleAge`. On Arbitrum, during a sequencer outage the L2's `block.timestamp` freezes or lags along with the chain itself, so `updatedAt` from the last Chainlink round and the frozen `block.timestamp` stay close together — the staleness check can pass even though the "true" off-chain price has since moved materially. When the sequencer resumes, a burst of blocks catches `block.timestamp` up to real time, but Chainlink's own recommended mitigation (checking the L2 sequencer-uptime feed and enforcing a grace period after it comes back online) is absent here, exactly as in the reported D3Oracle bug. `maxOracleAge` is governance-configurable up to `MAX_ORACLE_AGE = 7 days` [4](#0-3) , widening the exploitable window.

Both consumers of this stale-tolerant price are reachable by unprivileged users:
- `_tokenPrice`/`_fetchDetails`, invoked during ERC-4337 `_validatePaymasterUserOp` for every UserOperation that pays gas in a registered stablecoin [5](#0-4) [6](#0-5) .
- `swapAndDeposit`, which derives `amountOutMin` from the same oracle call and is treasury-gated but still relies on the same unguarded price source [7](#0-6) .

### Impact Explanation
An attacker who submits UserOperations while a stale-but-within-window price is being served can pay less token value than the true gas cost, draining the paymaster's treasury/stablecoin reserves over repeated operations — a direct loss-of-funds vector for a live gas-sponsorship contract, matching the bounty's "stealing or loss of funds" / "logic attacks" categories. Because pricing errors compound per UserOperation and the paymaster automatically recycles fees via `swapAndDeposit`, a skewed price also risks under-collateralized swaps that lock or misallocate treasury funds.

### Likelihood Explanation
No relayer, prover, governance, or admin compromise is needed — any account can submit an ERC-4337 UserOperation through the public EntryPoint at any time, including during and immediately after an L2 sequencer outage/recovery window, which is an ordinary infrastructure event (not attacker-controlled malice) rather than a "malicious peer" scenario. The bug is purely a missing on-chain guard in `_getOraclePrice`, independent of any off-chain trust assumption.

### Recommendation
Add a Chainlink L2 sequencer-uptime feed check (per Chainlink's documented pattern) to `_getOraclePrice`, reverting or extending a grace period when the sequencer feed reports `isDown == true` or has been up for less than the grace period, mirroring the fix recommended in the source report. Additionally consider re-validating `answeredInRound >= roundId` for extra round-completeness assurance.

### Proof of Concept
1. Deploy `SimplexPaymaster` on Arbitrum with `maxOracleAge` set near its configured production value (e.g. tens of minutes to hours, well under the 7-day cap).
2. Simulate an Arbitrum sequencer outage: Chainlink `updatedAt` stops advancing while `block.timestamp` also stalls/lags on L2.
3. During/just after sequencer recovery, submit a UserOperation with `paymasterData` for a registered stablecoin while `block.timestamp - updatedAt` is still `< maxOracleAge` but the true off-chain price has diverged.
4. `_fetchDetails` → `_tokenPrice` → `_getOraclePrice` accepts the stale round, computing `tokenPrice` from an outdated rate; the UserOperation is charged at the stale rate via `PaymasterERC20._erc20Cost`, resulting in an incorrect (attacker-favorable) charge relative to actual gas cost, confirmed by comparing `estimateTokenCost`/`getTokenPrice` output pre- and post-outage against the true market rate [8](#0-7) .

### Citations

**File:** evm/src/utils/SimplexPaymaster.sol (L102-103)
```text
    /// @dev Hard ceiling on the governance-configurable oracle staleness bound.
    uint256 public constant MAX_ORACLE_AGE = 7 days;
```

**File:** evm/src/utils/SimplexPaymaster.sol (L309-312)
```text
        uint256 nativeUsd = _getOraclePrice(nativeOracle, nativeOracleDecimals);
        uint256 tokenUsd = _getOraclePrice(cfg.tokenOracle, cfg.tokenOracleDecimals);
        uint256 expectedWei = (amountIn * tokenUsd * 1e18) / (nativeUsd * (10 ** cfg.tokenDecimals));
        uint256 amountOutMin = (expectedWei * (10_000 - swapSlippageBps)) / 10_000;
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

**File:** evm/src/utils/SimplexPaymaster.sol (L446-453)
```text
    /// @notice Current price in token base units per wei of gas (scaled by 1e18),
    ///         markup included. For offchain gas estimation.
    function getTokenPrice(address token) external view returns (uint256) {
        TokenConfig memory cfg = tokenConfigs[token];
        if (address(cfg.tokenOracle) == address(0)) revert TokenNotRegistered(token);

        return _tokenPrice(cfg);
    }
```

**File:** sdk/packages/indexer/src/addresses/chainlink-price-feeds.addresses.ts (L13-13)
```typescript
	"EVM-42161": "0x639Fe6ab55C921f74e7fac1ee960C0B6293ba612", // Arbitrum Mainnet
```
