Based on my research, I found the SimplexPaymaster registered-token registry, which mirrors the report's exact broken invariant.

### Title
Deactivated tokens remain permanently enumerable and swap-eligible in `SimplexPaymaster`, letting a stale/compromised oracle be recycled after governance disables it - (File: evm/src/utils/SimplexPaymaster.sol)

### Summary
The report's core defect is: a security-relevant enumeration (`registeredAssets`) is used to gate a privileged mode/action, but the enumeration is never pruned when an entry becomes "bad" (fully liquidated / deactivated), and the gating check treats "listed" as equivalent to "safe to use," ignoring the entry's actual live state. `SimplexPaymaster` reproduces this pattern: `_deactivateToken` flips `active = false` but never removes the token from `registeredTokens`, and `swapAndDeposit` — the one still-callable path for a deactivated token — keeps pricing and swapping through that token's oracle with no re-validation that deactivation was intentional/safe versus a stale-oracle emergency stop.

### Finding Description
`_registerToken` pushes into `registeredTokens` only once (`isNew` check), and `_deactivateToken` only clears the `active` flag [1](#0-0) . `getRegisteredTokens()` returns the full historical list regardless of `active` state [2](#0-1) , exactly mirroring `isBasketModeEnabled()`'s flaw of iterating "registered" items without accounting for their real, current liquidity/validity state.

Crucially, `swapAndDeposit` is explicitly documented to keep operating on deactivated tokens ("deactivated tokens remain recyclable") [3](#0-2) , and its logic only checks the registry (`tokenOracle != 0`), not `active`: [4](#0-3) 

The pricing for that swap is still computed from the same `TokenConfig.tokenOracle` via `_tokenPrice`/`_getOraclePrice` [5](#0-4) . Governance's only recourse to a misbehaving/depegged token oracle is `DeactivateToken`, which stops new UserOps from selecting it in `_fetchDetails`/`_validatePaymasterUserOp` [6](#0-5)  — but it does **not** stop `swapAndDeposit` from continuing to trust that same oracle for pricing the swap of whatever balance the paymaster already accrued in that token. This is the direct analog of "basket mode doesn't turn off when bad collateral is fully liquidated" (deactivation should retire the asset from *all* privileged flows, not just new-mint/new-UserOp flows) and of "mode activates on a stale/zero-relevance entry" (the registry keeps pricing a token governance has already flagged as bad).

### Impact Explanation
If a token is deactivated because its Chainlink oracle is stale/compromised or the token has depegged, an accrued balance of that token sitting in the paymaster can still be swept through `swapAndDeposit`, converting it to native currency and depositing into the EntryPoint using the paymaster's own (potentially corrupted) oracle-derived minimum output. Because the treasury-only caller can pass `amountIn = 0` to sweep the "full balance" [7](#0-6) , the deactivation gate provides a false sense of an emergency stop while the vulnerable price path stays fully live. This is a logic/state-inconsistency issue (loss of protective guarantee) rather than a direct fund-theft primitive, since `swapAndDeposit` is treasury-gated, but it directly reproduces the reported invariant violation: a "kill switch" (deactivation / basket mode) that doesn't propagate to every consumer of the flagged asset's state.

### Likelihood Explanation
Requires no attacker action beyond ordinary conditions the protocol already anticipates (a stale or manipulated oracle triggering `DeactivateToken`). The treasury role executing `swapAndDeposit` in the normal recycling cadence (the SDK's `PaymasterKeeperService` runs this periodically) [8](#0-7)  would unknowingly keep using the compromised oracle for any residual balance until someone notices and manually stops recycling that specific token — the code offers no automatic signal that recycling should also halt.

### Recommendation
Gate `swapAndDeposit` (and any other privileged flow reading `tokenConfigs[token].tokenOracle`) on `cfg.active` as well as registration, or add an explicit separate "oracle frozen" flag that both new-UserOp validation and recycling honor. Additionally, prune or mark `registeredTokens` entries so enumeration reflects live/active state, consistent with fixing the reported basket-mode analog of ignoring stale/zero-relevance entries in an aggregate check.

### Proof of Concept
1. Governance registers `usdc6` with an oracle and it accrues balance via normal fee collection.
2. Oracle becomes stale/compromised; governance calls `_govern(DeactivateToken, usdc6)`, setting `active = false` (test at `evm/tests/foundry/SimplexPaymasterTest.t.sol:251-259` confirms this blocks `fetchDetails`).
3. Despite deactivation, `testSwapAndDepositAllowsDeactivatedToken` demonstrates `swapAndDeposit` still succeeds and swaps the deactivated token's balance using its oracle: [4](#0-3) 
4. This confirms the deactivation "mode switch" only partially disables the token, leaving the stale-oracle-priced swap path open — the same class of incomplete-state-transition bug described in the source report.

### Citations

**File:** evm/src/utils/SimplexPaymaster.sol (L243-265)
```text
    function _registerToken(address token, AggregatorV3Interface oracle) internal {
        if (token == address(0) || address(oracle) == address(0)) revert ZeroAddress();

        bool isNew = !tokenConfigs[token].active && address(tokenConfigs[token].tokenOracle) == address(0);

        tokenConfigs[token] = TokenConfig({
            tokenOracle: oracle,
            tokenOracleDecimals: oracle.decimals(),
            tokenDecimals: IERC20Metadata(token).decimals(),
            active: true
        });

        if (isNew) {
            registeredTokens.push(token);
        }

        emit TokenRegistered(token, address(oracle));
    }

    function _deactivateToken(address token) internal {
        tokenConfigs[token].active = false;
        emit TokenDeactivated(token);
    }
```

**File:** evm/src/utils/SimplexPaymaster.sol (L285-299)
```text
    /// @notice Swaps accrued stablecoins to the native asset through the host's
    ///         V2-style router and deposits the contract's entire native balance
    ///         into the EntryPoint, so collected fees keep the paymaster funded
    ///         without a governance round-trip.
    /// @param token    A registered token; deactivated tokens remain recyclable.
    /// @param amountIn Token amount to swap; 0 (or more than the balance)
    ///                 swaps the full balance.
    /// @dev The minimum output is derived onchain from the Chainlink oracles
    ///      (markup-free price minus `swapSlippageBps`), so the caller cannot
    ///      influence the execution price. Still treasury-gated: were this
    ///      permissionless, a UserOp's calldata could invoke it mid-bundle and
    ///      swap away other ops' pending prefunds, breaking their postOp
    ///      refunds. The treasury sends ordinary transactions, which can never
    ///      execute mid-bundle.
    function swapAndDeposit(address token, uint256 amountIn) external {
```

**File:** evm/src/utils/SimplexPaymaster.sol (L342-393)
```text
    function _validatePaymasterUserOp(
        PackedUserOperation calldata userOp,
        bytes32 userOpHash,
        uint256 maxCost
    ) internal override returns (bytes memory context, uint256 validationData) {
        uint256 postOpGasLimit = userOp.paymasterPostOpGasLimit();
        if (postOpGasLimit > MAX_POST_OP_GAS_LIMIT) {
            revert InvalidPostOpGasLimit(postOpGasLimit, MAX_POST_OP_GAS_LIMIT);
        }

        bytes calldata data = userOp.paymasterData();
        if (data.length == 0) revert InvalidPaymasterData(0);
        if (uint8(data[0]) == 0x00) {
            if (data.length < 21) revert InvalidPaymasterData(data.length);
            address tokenAddr = address(bytes20(data[1:21]));
            TokenConfig memory cfg = tokenConfigs[tokenAddr];
            if (address(cfg.tokenOracle) == address(0)) revert TokenNotRegistered(tokenAddr);
            if (!cfg.active) revert TokenNotActive(tokenAddr);
            _executePermit(userOp);
        }

        return super._validatePaymasterUserOp(userOp, userOpHash, maxCost);
    }

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

**File:** evm/src/utils/SimplexPaymaster.sol (L469-472)
```text
    /// @notice List all registered tokens.
    function getRegisteredTokens() external view returns (address[] memory) {
        return registeredTokens;
    }
```

**File:** evm/tests/foundry/SimplexPaymasterTest.t.sol (L534-541)
```text
    function testSwapAndDepositAllowsDeactivatedToken() public {
        _govern(SimplexPaymaster.RequestKind.DeactivateToken, abi.encode(address(usdc6)));
        deal(address(usdc6), address(paymaster), 600e6);
        router.setNextAmountOut(1 ether);

        vm.prank(treasury);
        paymaster.swapAndDeposit(address(usdc6), 0);
        assertEq(entryPoint.balanceOf(address(paymaster)), 1 ether);
```

**File:** sdk/packages/simplex/src/services/PaymasterKeeperService.ts (L42-57)
```typescript
	/** Idempotent. Runs one cycle shortly after start, then on an interval. */
	start(chains: string[]): void {
		if (this.timer) return
		const targets = this.config.chains ?? chains
		const intervalMs = (this.config.intervalMinutes ?? DEFAULT_INTERVAL_MINUTES) * 60_000

		setTimeout(() => {
			this.runCycle(targets).catch((error) => this.logger.error({ error }, "Initial keeper cycle failed"))
		}, 10_000)

		this.timer = setInterval(() => {
			this.runCycle(targets).catch((error) => this.logger.error({ error }, "Keeper cycle failed"))
		}, intervalMs)

		this.logger.info({ chains: targets, intervalMs }, "Paymaster keeper started")
	}
```
