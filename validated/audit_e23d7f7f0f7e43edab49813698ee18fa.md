### Title
Missing Chainlink circuit-breaker (min/max answer) validation in SimplexPaymaster oracle pricing lets a stale-clamped price mis-price gas fees and drain the paymaster deposit - (File: evm/src/utils/SimplexPaymaster.sol)

### Summary
`SimplexPaymaster._getOraclePrice` (the same bug class as Sherlock issue 94/M-10 for `ChainlinkAdapterOracle`) only validates that a Chainlink `latestRoundData()` answer is positive and not older than `maxOracleAge`. It never checks the answer against the underlying aggregator's circuit-breaker bounds (`minAnswer`/`maxAnswer`). During extreme price moves, Chainlink aggregators can continue reporting a "fresh," positive, but clamped floor/ceiling price. Because this contract treats that value as a genuine market price, gas fee conversion between the native asset and the registered ERC-20 (USDC/USDT/etc.) becomes silently wrong while all existing guards pass.

### Finding Description
`_getOraclePrice` is used by `_tokenPrice`, which is the sole input for `_fetchDetails` (used by ERC-4337 `PaymasterERC20` to decide how much ERC-20 to pull from the sender) and for `swapAndDeposit`'s slippage-bounded conversion: [1](#0-0) 

Both `nativeUsd` and `tokenUsd` come from this same unguarded helper: [2](#0-1) 

and are consumed directly to compute the exact ERC-20 amount charged per unit of gas: [3](#0-2) 

The only defenses are `answer <= 0` and the age check against `maxOracleAge`: [4](#0-3) 

Chainlink aggregators expose an internal `minAnswer`/`maxAnswer` clamp (a circuit breaker on the underlying `AccessControlledOffchainAggregator`, not exposed by `AggregatorV3Interface`). When the true price moves past that clamp (e.g. a stablecoin de-peg toward $0, or a native-asset crash/spike), the aggregator keeps emitting fresh, positive rounds pinned at the clamp value rather than reverting or going obviously stale. `_getOraclePrice` has no way to detect this because it never cross-checks the returned `answer` against the aggregator's configured bounds — exactly the gap identified as unresolved in the referenced Sherlock issue for `ChainlinkAdapterOracle`. The comment in the contract (`"even against a malicious oracle"`) shows awareness of oracle risk, but the mitigation described there (small allowances/permits) only bounds the attacker's own deposit, not the paymaster's exposure from mispricing every other sender's gas.

### Impact Explanation
`tokenPrice` computed from a clamped price directly determines: (1) how much ERC-20 is pulled from every UserOp sender via `PaymasterERC20`'s prefund/postOp accounting, and (2) the `amountOutMin` bound used in `swapAndDeposit`, which converts accumulated ERC-20 into native currency deposited to the `EntryPoint`. If the token/USD feed is clamped low relative to real value (or the native/USD feed clamped high), `_tokenPrice` returns an artificially low ERC-20 cost per wei of gas — users get sponsored gas for a fraction of its real cost, draining the paymaster's `EntryPoint` deposit funded by the treasury, i.e., real fund loss from the paymaster's balance without any privileged/relayer/prover compromise required. Conversely a clamp in the opposite direction overcharges every legitimate sender. This is a direct "false state (price) acceptance" leading to wrong-beneficiary/wrong-amount fund movement, matching the bounty's accepted impact categories.

### Likelihood Explanation
No malicious relayer, governance actor, or leaked key is needed — clamped-answer behavior is a documented, observed Chainlink failure mode during genuine market stress (e.g., stablecoin de-pegs), and any unprivileged user submitting a normal ERC-4337 UserOp through the existing `0x00`/`0x01` paymasterData paths will be priced using the clamped value automatically, since `_getOraclePrice`'s only checks (`answer > 0`, staleness) remain satisfied throughout.

### Recommendation
Extend `_getOraclePrice` to reject answers that equal (or are within a configurable margin of) the aggregator's `minAnswer`/`maxAnswer`. Since `AggregatorV3Interface` doesn't expose these, either query the underlying aggregator (`AggregatorV2V3Interface(oracle).aggregator()` then read `minAnswer()`/`maxAnswer()` from the `AccessControlledOffchainAggregator`) at registration time and store the bounds per `TokenConfig`/`nativeOracle`, or add a secondary sanity source (e.g., a TWAP/second feed) and revert/fallback when the primary answer sits at a stored clamp boundary.

### Proof of Concept
1. Governance registers a token with a Chainlink USD feed via `RegisterToken` (`evm/src/utils/SimplexPaymaster.sol` `RegisterToken` handling) and sets `maxOracleAge` per the paymaster's `Params`.
2. The underlying real-world aggregator for that feed hits its configured `minAnswer` clamp during a market crash (a real, previously observed Chainlink behavior) and continues publishing fresh rounds pinned at `minAnswer`.
3. Any user submits a UserOp with `paymasterData` mode `0x01`; `_validatePaymasterUserOp` → `_fetchDetails` → `_tokenPrice` → `_getOraclePrice` reads this clamped, fresh, positive answer and returns it unmodified (as shown in `_getOraclePrice`, lines 428-434 above — no bound check exists).
4. `PaymasterERC20` charges the sender an ERC-20 amount computed from the wrong `tokenPrice`, and/or `swapAndDeposit`'s `expectedWei`/`amountOutMin` (lines 309-312) is computed from the same distorted price, draining/misallocating the paymaster's `EntryPoint` deposit over repeated UserOps — no relayer, governance, or admin compromise required, only normal usage during a real market dislocation.

### Citations

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
