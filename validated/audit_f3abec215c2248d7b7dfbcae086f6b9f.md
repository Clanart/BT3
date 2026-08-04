Based on the investigation, the strongest local analog to the reported `_getExchangeRate` precision-loss bug is `SimplexPaymaster._tokenPrice`, which performs the exact same style of USD-oracle-ratio integer division used to gate a monetary amount.

### Title
Oracle-ratio integer division in `_tokenPrice` can round to zero, letting UserOps drain paymaster gas sponsorship for free - (File: evm/src/utils/SimplexPaymaster.sol)

### Summary
`SimplexPaymaster._tokenPrice` computes a token-per-wei exchange rate the same way the reported `AutomationMaster._getExchangeRate` computes a token/token exchange rate: `(priceA * scale) / priceB`. Because this is plain Solidity integer division, when the denominator (`tokenUsd * 10_000`) is large relative to the numerator (`nativeUsd * 10^tokenDecimals * (10_000+markupBps)`), the result truncates to `0`. This value is then used directly by `PaymasterERC20` to compute `erc20Cost = weiCost * tokenPrice / 1e18`, so a zero `tokenPrice` means the sponsored UserOp is charged `0` ERC-20 tokens while the paymaster still pays real gas out of its EntryPoint deposit.

### Finding Description [1](#0-0) 
computes:
```
return (nativeUsd * (10 ** cfg.tokenDecimals) * (10_000 + markupBps)) / (tokenUsd * 10_000);
```
`_fetchDetails` [2](#0-1)  forwards this value straight into `PaymasterERC20`'s cost formula (documented at [3](#0-2) : `erc20Cost = weiCost * tokenPrice / 1e18`) with **no `require(tokenPrice > 0)` guard anywhere** in `_tokenPrice`, `_fetchDetails`, or `_validatePaymasterUserOp`. `_getOraclePrice` [4](#0-3)  only guards against non-positive/stale answers, not against a ratio that truncates to zero downstream.

This mirrors the reported bug precisely: same "USD-price-ratio via integer division" pattern, same missing "amount must be > 0" guard, and the truncated value feeds directly into the amount an unprivileged caller must pay.

### Impact Explanation
If `tokenPrice` rounds to `0` for any registered token, every subsequent UserOp that selects that token in `paymasterData` is sponsored for free: `PaymasterERC20` prefunds the operation, gas is spent, and `_postOp` collects `weiCost * 0 / 1e18 = 0` tokens back. This drains the paymaster's EntryPoint deposit (funded by governance/treasury) at zero cost to the attacker, an unbounded, repeatable fund-loss primitive reachable by any address that can submit a UserOp — no relayer, prover, or admin compromise needed.

### Likelihood Explanation
Truncation only occurs when `tokenUsd * 10_000 > nativeUsd * 10^tokenDecimals * (10_000+markupBps)`. Because `10^tokenDecimals` is a large multiplier (`1e6`–`1e18` for realistic ERC-20s), this requires an extreme combination — a very low-decimal, very high-USD-value token priced against a very low-USD-value native gas asset. Under the token/native pairs exercised in the test suite (e.g. BNB $600 vs USDC 6/18 decimals, tested at [5](#0-4) ) the ratio never truncates. The path only becomes reachable if governance registers a token (via `RequestKind.RegisterToken`) whose decimals/price combination is unusually skewed relative to the chain's native asset — this depends on a governance/registration choice rather than being attacker-triggerable on arbitrary correctly-configured deployments, which weakens (but does not eliminate) real-world likelihood.

### Recommendation
- Add `require(tokenPrice > 0)` (or an equivalent revert) in `_tokenPrice` / `_fetchDetails` before returning, mirroring the reported mitigation of asserting the derived amount is non-zero.
- Switch `_tokenPrice`'s intermediate math to a higher-precision fixed-point representation (e.g., scale by `1e18` before dividing) so extreme decimal/price combinations don't truncate to zero.
- Add a governance-time sanity check in `_registerToken` that rejects tokens whose `_tokenPrice` computation would currently evaluate to zero, so misconfiguration is caught at registration rather than silently enabling free gas sponsorship.

### Proof of Concept
Given the formula in `_tokenPrice` [1](#0-0) , register a token `T` with `tokenDecimals = 0` (a legal, if unusual, ERC-20) whose Chainlink feed reports `tokenUsd` scaled to `1e8` such that:
```
tokenUsd * 10_000 > nativeUsd * 1 * (10_000 + markupBps)
```
e.g., native asset priced at `nativeUsd = 1e4` (i.e., $0.0001, plausible for a very low-value native gas token) and `tokenUsd = 1e14` (a token priced at $1,000,000): `numerator = 1e4 * 1 * 10_010 ≈ 1.001e8`, `denominator = 1e14 * 1e4 = 1e18` → `tokenPrice = 0`. Any UserOp using mode `0x01` (`APPROVE`) with token `T` in `paymasterData` then passes `_fetchDetails` with `tokenPrice = 0`, and `PaymasterERC20._postOp` collects `0` tokens for real gas spent, repeatable to drain the paymaster's EntryPoint balance.

### Citations

**File:** evm/src/utils/SimplexPaymaster.sol (L366-373)
```text
    /// @dev Returns the token to charge and its price relative to native gas.
    ///
    ///      PaymasterERC20 computes `erc20Cost = weiCost * tokenPrice / 1e18`,
    ///      so tokenPrice must be token base units per wei, scaled by 1e18:
    ///        tokenPrice = (nativeUsd * 10^tokenDecimals) / tokenUsd
    ///      e.g. BNB at $600, USDC at $1 with 6 decimals: 0.001 BNB (1e15 wei)
    ///      should cost 0.60 USDC (600000 units), giving tokenPrice = 6e8, which
    ///      is exactly (600e8 * 1e6) / 1e8. Markup is applied on top.
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

**File:** evm/tests/foundry/SimplexPaymasterTest.t.sol (L178-191)
```text
    function testTokenPriceSixDecimals() public view {
        // $600 native, $1 token, 6 decimals: 1 wei of gas costs 6e8 / 1e18 token units.
        // 0.001 BNB (1e15 wei) should cost 0.60 USDC (600_000 units).
        uint256 price = paymaster.getTokenPrice(address(usdc6));
        assertEq(price, 6e8);
        assertEq((1e15 * price) / 1e18, 600_000);
    }

    function testTokenPriceEighteenDecimals() public view {
        uint256 price = paymaster.getTokenPrice(address(usdc18));
        assertEq(price, 6e20);
        // 0.001 BNB should cost 0.6 tokens in 18-decimal units.
        assertEq((1e15 * price) / 1e18, 6e17);
    }
```
