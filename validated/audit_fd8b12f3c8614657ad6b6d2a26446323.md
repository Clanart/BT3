## Analysis

The Chainlink report's core broken invariant is: **a single external price feed is trusted without any sanity/deviation bound, and wrong or stale data flows directly into fund-moving logic with no fallback**, which the report says should be corrected with a secondary oracle and a check against extreme price swings.

The closest local analog is `SimplexPaymaster.sol`, Hyperbridge's ERC-4337 paymaster that lets **any unprivileged UserOp sender** pay gas in a stablecoin priced against `nativeUsd`/`tokenUsd` from two `AggregatorV3Interface` (Chainlink) feeds [1](#0-0) . The only oracle guard is staleness/non-positivity — there is no deviation check and no fallback source: [2](#0-1) 

That value feeds directly into the token price charged to *any* caller through `_fetchDetails`/`_tokenPrice`, which is consumed by `PaymasterERC20` to compute `erc20Cost = weiCost * tokenPrice / 1e18` and to prefund/settle the UserOp: [3](#0-2) [4](#0-3) 

Since `_fetchDetails`/`_validatePaymasterUserOp` are reachable by any permissionless UserOp (no admin gating, unlike `swapAndDeposit` which is `treasury`-gated), a transient bad round from either feed (a brief wrong/aberrant answer that is still "fresh" per `updatedAt` and positive) is accepted at face value and used to size the actual ERC-20 charge against the paymaster's real ETH-denominated `EntryPoint` deposit.

### Title
Unbounded trust in Chainlink price feeds lets a bad oracle round drain the SimplexPaymaster's EntryPoint deposit - (File: evm/src/utils/SimplexPaymaster.sol)

### Summary
`SimplexPaymaster._getOraclePrice` only rejects a `latestRoundData()` answer that is non-positive or older than `maxOracleAge`; it performs no sanity/deviation check against the previous price and has no fallback oracle. Any transient wrong reading from either the `nativeOracle` or a registered `tokenOracle` that is still "fresh" is trusted directly by `_tokenPrice`, which is the exact conversion rate charged to any permissionless ERC-4337 UserOp for gas sponsorship.

### Finding Description
`_tokenPrice` combines two independently-fetched Chainlink answers with no cross-check other than staleness/positivity [5](#0-4) . This price is used unmodified by `_fetchDetails`, invoked by the inherited `PaymasterERC20` on every UserOp validation to determine `tokenPrice`, which multiplies `weiCost` to size the ERC-20 amount pulled from the sender [3](#0-2) . There is no circuit breaker comparing the new answer to the last stored price (the pattern the external report recommends), and no secondary oracle to cross-validate.

Because `_validatePaymasterUserOp`/`_fetchDetails` are permissionless entry points reachable by any UserOp sender (unlike `swapAndDeposit`, which is explicitly `treasury`-gated per the contract's own security note) [6](#0-5) , a wrong-but-fresh Chainlink round (e.g., a bad print on `nativeOracle` reporting an anomalously low native/USD price, or on a `tokenOracle` reporting an anomalously high token/USD price) is accepted and directly lowers `tokenPrice`, letting any attacker submit UserOps that pay far less ERC-20 than the gas actually costs. The paymaster's `EntryPoint` deposit — real, pooled ETH — is drained to sponsor gas essentially for free during that window, with no way to detect or reject the anomaly on-chain.

### Impact Explanation
This is a direct loss-of-funds vector for the paymaster's pooled `EntryPoint` deposit, reachable by any unprivileged UserOp sender (no relayer/prover/admin compromise required) during a transient bad oracle round. It mirrors the report's stated worst-case ("users losing their collateral"/system going uncollateralized") but here manifests as the paymaster subsidizing gas at near-zero real cost, draining its deposit to attacker-chosen beneficiaries via ordinary UserOps.

### Likelihood Explanation
Likelihood is low-to-moderate: it requires a genuine anomalous-but-fresh Chainlink round (flash-crash / bad print / thin-liquidity feed misbehavior), which the referenced report itself treats as a realistic, if infrequent, oracle failure mode. No malicious relayer, prover, or governance actor is needed — only an alert unprivileged user submitting UserOps during the bad-price window.

### Recommendation
Add a deviation/circuit-breaker check in `_getOraclePrice` (e.g., reject or clamp updates whose deltas from the last accepted price exceed a configurable bound, similar to the reporter's "reject >50% movement" suggestion), and/or introduce a secondary price source (a second Chainlink feed or a TWAP from the existing Uniswap V2 router already wired into the contract) to cross-validate before trusting a single feed for fund-moving pricing.

### Proof of Concept
1. `nativeOracle` (or a registered `tokenOracle`) emits one anomalous but fresh round via `latestRoundData()` (e.g., due to a feed bug/flash event), passing both the `answer <= 0` and `maxOracleAge` checks in `_getOraclePrice`.
2. An attacker submits an ERC-4337 UserOp using this paymaster with `paymasterData` referencing the affected token; `_fetchDetails` computes `tokenPrice` from the anomalous reading.
3. `PaymasterERC20` prefunds/settles the UserOp using this depressed `tokenPrice`, charging the attacker far fewer ERC-20 units than the true gas cost.
4. The paymaster's `EntryPoint` deposit is debited for the real gas cost while receiving an under-valued token amount, and can be repeated across many UserOps for as long as the bad round persists, draining the deposit before governance can react via `UpdateParams`/`DeactivateToken`.

### Citations

**File:** evm/src/utils/SimplexPaymaster.sol (L17-25)
```text
/// @notice Minimal Chainlink AggregatorV3 interface — no external dependency needed.
interface AggregatorV3Interface {
    function latestRoundData()
        external
        view
        returns (uint80 roundId, int256 answer, uint256 startedAt, uint256 updatedAt, uint80 answeredInRound);

    function decimals() external view returns (uint8);
}
```

**File:** evm/src/utils/SimplexPaymaster.sol (L299-301)
```text
    function swapAndDeposit(address token, uint256 amountIn) external {
        if (msg.sender != treasury) revert UnauthorizedCall();
        address router = IDispatcher(host()).uniswapV2Router();
```

**File:** evm/src/utils/SimplexPaymaster.sol (L366-393)
```text
    /// @dev Returns the token to charge and its price relative to native gas.
    ///
    ///      PaymasterERC20 computes `erc20Cost = weiCost * tokenPrice / 1e18`,
    ///      so tokenPrice must be token base units per wei, scaled by 1e18:
    ///        tokenPrice = (nativeUsd * 10^tokenDecimals) / tokenUsd
    ///      e.g. BNB at $600, USDC at $1 with 6 decimals: 0.001 BNB (1e15 wei)
    ///      should cost 0.60 USDC (600000 units), giving tokenPrice = 6e8, which
    ///      is exactly (600e8 * 1e6) / 1e8. Markup is applied on top.
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

**File:** evm/src/utils/SimplexPaymaster.sol (L419-442)
```text
    function _tokenPrice(TokenConfig memory cfg) internal view returns (uint256) {
        uint256 nativeUsd = _getOraclePrice(nativeOracle, nativeOracleDecimals);
        uint256 tokenUsd = _getOraclePrice(cfg.tokenOracle, cfg.tokenOracleDecimals);

        return (nativeUsd * (10 ** cfg.tokenDecimals) * (10_000 + markupBps)) / (tokenUsd * 10_000);
    }

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
