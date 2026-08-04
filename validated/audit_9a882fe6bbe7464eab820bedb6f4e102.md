### Title
Missing L2 sequencer-uptime check lets `SimplexPaymaster` accept stale Chainlink prices during sequencer downtime, causing gas-fee mispricing and paymaster fund loss - (evm/src/utils/SimplexPaymaster.sol)

### Summary
`SimplexPaymaster` prices ERC-20 gas payments off two Chainlink `AggregatorV3Interface` feeds (`nativeOracle`, per-token `tokenOracle`) and only guards against staleness via `block.timestamp - updatedAt > maxOracleAge` and `answer <= 0`. It is deployed across multiple chains (BSC, Base, Ethereum per the code comments) via `evm/script/DeploySimplexPaymaster.s.sol`, and its `Params.maxOracleAge` is governance-configurable up to `MAX_ORACLE_AGE = 7 days`, with the code's own comment noting real-world deployments use heartbeats "up to 24h" for stablecoin feeds. Nowhere does the contract check an L2 sequencer-uptime feed (e.g. Chainlink's `0xFdB631F5EE196F0ed6FAa767959853A9F217697` pattern on Arbitrum), which is exactly the missing control described in the referenced report.

### Finding Description
`_getOraclePrice` in `evm/src/utils/SimplexPaymaster.sol` (lines 428-442) is the single price-fetch primitive used both for real-time gas pricing (`_tokenPrice`, called from `_fetchDetails` during `_validatePaymasterUserOp`) and for swap-slippage bounds in `swapAndDeposit`:

```solidity
function _getOraclePrice(AggregatorV3Interface oracle, uint8 oracleDecimals) internal view returns (uint256) {
    (, int256 answer, , uint256 updatedAt, ) = oracle.latestRoundData();
    if (answer <= 0) revert InvalidOraclePrice(address(oracle), answer);
    if (block.timestamp - updatedAt > maxOracleAge) {
        revert StaleOraclePrice(address(oracle), updatedAt);
    }
    ...
}
``` [1](#0-0) 

This mirrors the exact defect in the referenced report's `GLPOracle.getEthPrice()`: it checks `updatedAt` staleness but never asks whether the underlying L2 sequencer was down. On Arbitrum-style rollups, Chainlink feeds continue to report their *last* `updatedAt` timestamp from before a sequencer outage; once the sequencer resumes, transactions built during/immediately after the outage can read a price that is still within `maxOracleAge` (up to 24h per the contract's own documentation for some tokens) but does not reflect the market during the outage window, and — more importantly — the canonical L2-sequencer risk is that feeds can be updated with a burst of stale-relative-to-market data right as the sequencer restarts, before the standard `GRACE_PERIOD_TIME` recommended by Chainlink elapses.

The contract's own comment at line 84-86 acknowledges wide heartbeat windows are expected:
```solidity
/// @notice Maximum oracle staleness. Chainlink heartbeats vary per chain
///         (BSC stablecoins ~27s, Base/Ethereum stablecoins up to 24h).
uint256 public constant MAX_ORACLE_AGE = 7 days;
``` [2](#0-1) 

This governance-set `maxOracleAge` is applied in `_setParams` with only an upper bound check (`p.maxOracleAge > MAX_ORACLE_AGE`), never a sequencer check:
```solidity
if (p.maxOracleAge == 0 || p.maxOracleAge > MAX_ORACLE_AGE) revert InvalidOracleAge(p.maxOracleAge);
``` [3](#0-2) 

Every consumer of price data — `_tokenPrice` (used by `_fetchDetails`, which determines how much ERC-20 the user is charged per unit of gas) and `swapAndDeposit` (which converts accumulated ERC-20 surplus to native currency and deposits it to the EntryPoint) — depends solely on `_getOraclePrice`, so the missing sequencer check propagates to both the per-UserOp charge and the treasury's fee-recycling swap execution price.

### Impact Explanation
This is a live, in-scope EVM contract (not a peer/relayer assumption) reachable by any account submitting an ERC-4337 UserOperation. If the sequencer of a deployed L2 goes down and later resumes while a stale-but-not-yet-expired Chainlink answer is in effect:
- Users can be charged incorrect ERC-20 amounts for gas (`_fetchDetails` → `_tokenPrice` → `PaymasterERC20._erc20Cost`), directly causing paymaster fund loss (undercharging drains the paymaster's treasury/deposit) or overcharging users (theft of user funds), both matching the "stealing or loss of funds" and "logic attacks" impact categories.
- `swapAndDeposit`'s `amountOutMin` is derived from the same stale oracle price (line 309-312), so the treasury-triggered swap can execute at an unfavorable, stale-market-relative rate, causing loss of surplus funds that should be recycled into the EntryPoint deposit.

The severity depends on market volatility during the sequencer outage window and on the actual `maxOracleAge` configured per chain, but the guard is fully absent regardless of configuration — it's a structural gap, not a parameter-tuning issue.

### Likelihood Explanation
The attack requires no privileged access, no relayer/prover/admin compromise, and no malicious peer — it is triggered purely by public L2 sequencer downtime (a documented, historically-occurring, permissionless-to-observe event on OP-stack/Arbitrum-style L2s) combined with any account calling the paymaster's public UserOp validation path or the treasury calling `swapAndDeposit`. Given the explicit design comment referencing multi-chain deployment with long stablecoin heartbeats (up to 24h), the staleness window during which a sequencer-downtime price can still pass the `maxOracleAge` check is realistically wide.

### Recommendation
Add an L2 sequencer-uptime Chainlink feed check (following Chainlink's documented `L2SequencerUptimeFeed` pattern) inside `_getOraclePrice`, reverting if the sequencer is down or if it has resumed less than a configured grace period ago, before trusting `latestRoundData()` from `nativeOracle`/`tokenOracle`. This check should gate both `_tokenPrice` (paymaster UserOp pricing) and `swapAndDeposit`'s slippage computation.

### Proof of Concept
1. Deploy `SimplexPaymaster` on an Arbitrum-style L2 with `maxOracleAge` set to a realistic stablecoin heartbeat (e.g. 24h, per the contract's own comment).
2. Simulate sequencer downtime: the Chainlink `nativeOracle`/`tokenOracle` `updatedAt` stops advancing while true market price moves significantly (verifiable against `SimplexPaymasterTest.t.sol`'s `MockOracle.setUpdatedAt`/`setAnswer` harness, e.g. `testStaleOracleReverts` at lines 216-226, which shows the contract only reverts once `block.timestamp - updatedAt > maxOracleAge` — i.e., any staleness under that bound, however price-divergent, is silently accepted).
3. Submit an ERC-4337 UserOperation with `paymasterData` targeting a registered token; `_fetchDetails` → `_tokenPrice` → `_getOraclePrice` accepts the stale (but within-`maxOracleAge`) `answer`, charging the user (or crediting the paymaster) at a mispriced rate relative to actual post-outage market conditions.
4. Alternatively, have `treasury` call `swapAndDeposit` during the same window; `amountOutMin` is computed from the same stale price, and the router executes at a rate the true market would not offer, draining value from the paymaster's recycled surplus. [4](#0-3)

### Citations

**File:** evm/src/utils/SimplexPaymaster.sol (L84-103)
```text
        /// @notice Maximum oracle staleness. Chainlink heartbeats vary per chain
        ///         (BSC stablecoins ~27s, Base/Ethereum stablecoins up to 24h).
        uint256 maxOracleAge;
        /// @notice Slippage tolerance in basis points applied to the
        ///         oracle-derived expected output in {swapAndDeposit}.
        uint256 swapSlippageBps;
    }

    struct TokenConfig {
        AggregatorV3Interface tokenOracle; // token/USD feed
        uint8 tokenOracleDecimals; // cached decimals() of tokenOracle
        uint8 tokenDecimals; // decimals() of the ERC-20
        bool active; // kill-switch per token
    }

    /// @dev Hard cap on the governance-configurable markup (50%).
    uint256 public constant MAX_MARKUP_BPS = 5_000;

    /// @dev Hard ceiling on the governance-configurable oracle staleness bound.
    uint256 public constant MAX_ORACLE_AGE = 7 days;
```

**File:** evm/src/utils/SimplexPaymaster.sol (L219-219)
```text
        if (p.maxOracleAge == 0 || p.maxOracleAge > MAX_ORACLE_AGE) revert InvalidOracleAge(p.maxOracleAge);
```

**File:** evm/src/utils/SimplexPaymaster.sol (L428-442)
```text
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

**File:** evm/tests/foundry/SimplexPaymasterTest.t.sol (L216-226)
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
```
