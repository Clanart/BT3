### Title
`SimplexPaymaster._getOraclePrice` accepts Chainlink `minAnswer`/`maxAnswer` floor/ceiling prices as valid, allowing gas payment with de-pegged collateral at a stale/false exchange rate - ([File: evm/src/utils/SimplexPaymaster.sol])

### Summary
`SimplexPaymaster` prices ERC-4337 gas payments using two Chainlink feeds (native/USD and token/USD) fetched through `_getOraclePrice`. This function checks only for non-positive answers and staleness — it never checks the returned `answer` against the aggregator's own `minAnswer`/`maxAnswer` circuit-breaker bounds [1](#0-0) . This is the exact bug class from the external report: when an aggregator hits its price band floor during a market crash/de-peg, it continues returning the floor price instead of the true (much lower) price, and nothing here detects it.

### Finding Description
`_tokenPrice` divides `nativeUsd` by `tokenUsd` to compute how many token base units are owed per wei of gas [2](#0-1) . Both values come from `_getOraclePrice`, whose only guards are:
```solidity
if (answer <= 0) revert InvalidOraclePrice(address(oracle), answer);
if (block.timestamp - updatedAt > maxOracleAge) {
    revert StaleOraclePrice(address(oracle), updatedAt);
}
``` [3](#0-2) 

If a registered token's Chainlink `token/USD` feed hits its aggregator's `minAnswer` during a de-peg event (e.g., a stablecoin like USDC/USDT/BUSD dropping toward zero), the feed keeps emitting the floored, positive, freshly-updated price (e.g. $0.98) instead of the real near-zero price. `answer <= 0` and the staleness check both pass, so `tokenUsd` is accepted as if it reflected reality. This flows straight into `_tokenPrice`, used by `_fetchDetails` during `_validatePaymasterUserOp` on every ERC-4337 `UserOperation` that pays with this token [4](#0-3) .

`_fetchDetails`/`_validatePaymasterUserOp` are reachable by any unprivileged UserOperation sender through the ERC-4337 EntryPoint — no relayer, prover, or admin compromise is required, only holding the crashed token that governance had previously registered as a legitimate payment asset.

### Impact Explanation
Because `tokenPrice` is inflated for a depegged token (the floored `tokenUsd` still divides as if near $1), `PaymasterERC20`'s `erc20Cost = weiCost * tokenPrice / 1e18` undercharges the amount of the crashed token pulled from the user, while the paymaster still fronts the full, real gas cost in native currency from its EntryPoint deposit (funded from treasury via `swapAndDeposit`/governance deposits). An attacker holding worthless/near-worthless de-pegged tokens can repeatedly submit UserOperations, paying a token that the oracle still prices near its pre-crash value, draining the paymaster's EntryPoint deposit for real native-asset gas while contributing near-zero real value — direct, permissionless fund loss caused by false price/state acceptance, matching the bounty's "stealing or loss of funds" / "false proof/state acceptance" categories.

### Likelihood Explanation
This requires only a real-world event that is common for the exact class of assets this contract is designed to accept ("USDC, USDT, or any token with a Chainlink feed" per the contract's own NatSpec) [5](#0-4) . Stablecoin de-pegs hitting exchange/oracle price floors have happened multiple times historically (referenced directly in the external report). No privileged actor, malicious relayer, or governance compromise is needed — any user can trigger the mispricing path the moment a registered token's feed pins at its aggregator bound, and the same missing check applies symmetrically to `nativeOracle` and to `swapAndDeposit`'s `expectedWei` computation [6](#0-5) .

### Recommendation
Add `minAnswer`/`maxAnswer` bounds checking in `_getOraclePrice` (or query and compare against the underlying aggregator's configured min/max, or maintain a governance-configured sane band per feed) and revert with a dedicated error (e.g. `OraclePriceOutOfBounds`) when the returned `answer` sits at or beyond those bounds, mirroring the staleness/non-positive checks already present.

### Proof of Concept
1. Governance registers `TOKEN` with Chainlink feed `F` via `RequestKind.RegisterToken` — `F` is a live stablecoin feed whose aggregator has `minAnswer = 0.98e8`.
2. `TOKEN` depegs to real market value `$0.001`; `F.latestRoundData()` still reports `answer = 0.98e8` (pinned at `minAnswer`) with a fresh `updatedAt`.
3. `_getOraclePrice(F, ...)` passes both its `answer <= 0` and staleness checks and returns `98_000_000` (8-decimal normalized) — the stale/false floor price, not the real crashed price.
4. Attacker submits a `PackedUserOperation` with `paymasterData` mode `0x01` referencing `TOKEN`. `_fetchDetails` computes `tokenPrice` via `_tokenPrice`, using the inflated `tokenUsd`, so `erc20Cost` charges the attacker as if `TOKEN` were still worth ~$0.98.
5. `PaymasterERC20` pulls the (cheap, real-market-value near-zero) `TOKEN` amount from the attacker while the paymaster's EntryPoint deposit is debited for the full real native gas cost.
6. Repeating this across many UserOperations drains the paymaster's EntryPoint deposit/treasury funds for near-worthless tokens, with no code path in `_getOraclePrice` ever detecting or rejecting the pinned floor price.

### Citations

**File:** evm/src/utils/SimplexPaymaster.sol (L27-32)
```text
/// @title  SimplexPaymaster
/// @author Polytope Labs
/// @notice Fully onchain, permissionless ERC-4337 v0.8 paymaster that accepts
///         ERC-20 stablecoins (USDC, USDT, or any token with a Chainlink feed)
///         for gas payment. Deployed behind an ERC1967Proxy and administered
///         exclusively through Hyperbridge governance.
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
