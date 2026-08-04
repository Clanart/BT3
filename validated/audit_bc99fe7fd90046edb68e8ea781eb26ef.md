## Title
Missing Chainlink `minAnswer`/`maxAnswer` circuit-breaker check in `SimplexPaymaster._getOraclePrice` allows mispriced gas payments during price-feed saturation - (File: `evm/src/utils/SimplexPaymaster.sol`)

### Summary
`SimplexPaymaster` prices ERC-20 gas payments using two Chainlink feeds (native/USD and token/USD) read through `_getOraclePrice`. The function only guards against non-positive and stale answers, never against the aggregator returning a saturated boundary value (`minAnswer`/`maxAnswer`), reproducing exactly the bug class described in the external report. [1](#0-0) 

### Finding Description
`_getOraclePrice` reads `latestRoundData()` and rejects `answer <= 0` and staleness beyond `maxOracleAge`, but does not validate the returned answer against the aggregator's configured `minAnswer`/`maxAnswer` bounds: [2](#0-1) 

This value feeds both the ERC-4337 gas-charging path (`_tokenPrice` → `_fetchDetails`, consumed by `PaymasterERC20._validatePaymasterUserOp`/`_postOp`) and the treasury-only fee-recycling swap (`swapAndDeposit`, which derives `amountOutMin` from the same oracle): [3](#0-2) [4](#0-3) 

Chainlink aggregators clamp reported answers to `[minAnswer, maxAnswer]`. If either the `nativeOracle` or a registered `tokenOracle` experiences a sharp de-peg/crash (or spike) that pushes the true price outside this band, `latestRoundData()` keeps returning the boundary value — a positive, fresh (non-stale) number — so both of the existing guards pass silently. `_getOraclePrice` then returns the frozen boundary price as if it were the live market price. Nothing in `_setParams`, `_registerToken`, or `_getOraclePrice` checks the aggregator's configured bounds, and the minimal local `AggregatorV3Interface` doesn't even expose them, so there is no mechanism to detect or reject a saturated reading.

### Impact Explanation
`tokenPrice = (nativeUsd * 10^tokenDecimals * (10000+markupBps)) / (tokenUsd * 10000)` is used directly to compute how much ERC-20 the paymaster pulls from the UserOp sender via `transferFrom`/permit in exchange for sponsoring gas. If `tokenUsd` is clamped low (token trending toward zero but frozen at `minAnswer`) while `nativeUsd` reflects reality, `tokenPrice` is computed against a stale, too-high USD value for the token — the paymaster undercharges, draining native gas value from the contract/EntryPoint deposit for underpriced ERC-20 collateral (funds loss to the protocol/treasury). Conversely, if `nativeUsd` saturates high or `tokenUsd` saturates high, users are overcharged. The same corrupted price additionally sets `amountOutMin` in the permissioned `swapAndDeposit`, which could execute a swap at a bad, clamped conversion rate. This is a direct funds-loss/mispricing vector reachable by any permissionless UserOp sender through the paymaster's public gas-sponsorship path, with no privileged actor required to trigger it — matching the bounty's "stealing or loss of funds" / "transaction manipulation" criteria.

### Likelihood Explanation
Requires a real-world market event that pushes an underlying asset price to or beyond a Chainlink aggregator's configured band (as happened historically, e.g. LUNA/UST). Given the paymaster is explicitly designed to accept arbitrary "any token with a Chainlink feed" via governance registration (not only blue-chip stablecoins), and stablecoin de-pegs toward zero are a recurring event class, this is a plausible, non-contrived trigger condition — not reliant on a malicious relayer, prover, or governance actor.

### Recommendation
Extend `_getOraclePrice` (and the `swapAndDeposit` price path) to fetch and enforce the aggregator's `minAnswer`/`maxAnswer` bounds — e.g. via the aggregator's `aggregator()` accessor to the underlying `AccessControlledOffchainAggregator`, or by having governance register per-feed bounds during `_registerToken`/`_setParams` — and revert (or fail over to a backup oracle) when `answer` sits at or outside those bounds, mirroring the `StaleOraclePrice`/`InvalidOraclePrice` revert pattern already in place.

### Proof of Concept
1. Governance registers `tokenOracle` for `TOKEN` via `RegisterToken`.
2. `TOKEN`'s real market price collapses below the Chainlink aggregator's `minAnswer` (e.g. a de-peg event), but the feed keeps reporting `minAnswer` as a fresh, positive value within `maxOracleAge`.
3. A user submits a UserOp with `paymasterData` selecting `TOKEN`; `_fetchDetails` → `_tokenPrice` → `_getOraclePrice` accepts the clamped price and computes `tokenPrice` using the inflated `minAnswer` instead of the collapsed real price.
4. `PaymasterERC20` charges the user the resulting (undercharged, since token appears worth more than it is) amount of `TOKEN` for real native gas spent — the paymaster's treasury/EntryPoint deposit loses value relative to the ERC-20 actually collected, with no guard rejecting the boundary answer. `testNonPositiveOraclePriceReverts`/`testStaleOracleReverts` in the test suite confirm only the zero/staleness paths are covered, with no equivalent boundary-clamp test: [5](#0-4)

### Citations

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

**File:** evm/tests/foundry/SimplexPaymasterTest.t.sol (L216-234)
```text
    function testStaleOracleReverts() public {
        nativeOracle.setUpdatedAt(block.timestamp - paymaster.maxOracleAge() - 1);
        vm.expectRevert(
            abi.encodeWithSelector(
                SimplexPaymaster.StaleOraclePrice.selector,
                address(nativeOracle),
                block.timestamp - paymaster.maxOracleAge() - 1
            )
        );
        paymaster.getTokenPrice(address(usdc6));
    }

    function testNonPositiveOraclePriceReverts() public {
        usdcOracle.setAnswer(0);
        vm.expectRevert(
            abi.encodeWithSelector(SimplexPaymaster.InvalidOraclePrice.selector, address(usdcOracle), int256(0))
        );
        paymaster.getTokenPrice(address(usdc6));
    }
```
