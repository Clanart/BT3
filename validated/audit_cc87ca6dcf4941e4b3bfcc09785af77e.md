### Title
`callGasLimit`/`verificationGasLimit` unused-gas penalty is not capped in `SimplexPaymaster`, allowing the sender to drain the paymaster's EntryPoint deposit - (File: `evm/src/utils/SimplexPaymaster.sol`)

### Summary
`SimplexPaymaster` is a fully permissionless ERC-4337 v0.8 paymaster that sponsors gas for any sender in exchange for an ERC-20 token charge computed from `actualGasCost`/`actualUserOpFeePerGas` in `PaymasterERC20._postOp` (inherited, not overridden). The contract's own author comment on `MAX_POST_OP_GAS_LIMIT` shows the team is aware that "the EntryPoint's unused-gas penalty is drained from this contract's deposit to a caller-chosen beneficiary" and mitigates it — but **only** for `paymasterPostOpGasLimit`, via the cap enforced in `_validatePaymasterUserOp`: [1](#0-0) [2](#0-1) 

Under EntryPoint v0.7/v0.8, the 10% unused-execution-gas penalty is computed over the **total** unused gas across `verificationGasLimit`, `callGasLimit`, `paymasterVerificationGasLimit`, and `paymasterPostOpGasLimit` — not just the postOp portion. `SimplexPaymaster` caps only the one component it explicitly reasoned about (`paymasterPostOpGasLimit` ≤ 100,000) and leaves `callGasLimit` (and `verificationGasLimit`) completely unconstrained.

### Finding Description
`_validatePaymasterUserOp` bounds only `userOp.paymasterPostOpGasLimit()`: [3](#0-2) 

No check exists anywhere in the file (or its EntryPoint-facing surface) that inspects `userOp.callGasLimit` or `userOp.verificationGasLimit`. A grep across `evm/src/utils/*.sol` for these identifiers returns no matches, confirming there is no bound.

The token amount charged to the user is computed by the inherited `PaymasterERC20._postOp` from `actualGasCost`/`actualUserOpFeePerGas`, i.e., only the **actually consumed** gas plus the fixed `_postOpCost()` cushion — see `estimateTokenCost`, which mirrors exactly this formula: [4](#0-3) 

Per ERC-4337 v0.7/v0.8 semantics, EntryPoint applies the 10% unused-gas penalty on the *reserved-but-unused* gas across the whole UserOp bundle **after** `_postOp()` returns, debiting it from the paymaster's EntryPoint deposit directly — this happens outside of `_postOp`'s visibility and outside of the token charge computed from `actualGasCost`. Because the token charge only reflects gas actually spent, the user never pays for this penalty; the paymaster's native ETH deposit in the EntryPoint absorbs it.

An attacker who is a plain, unprivileged UserOp sender can set an inflated `callGasLimit` (e.g., several million gas) while having their `callData` consume only a small fraction of it, then use `SimplexPaymaster` to sponsor the operation. The paymaster's own reasoning shows this drains real value ("drained from this contract's deposit to a caller-chosen beneficiary"), but the contract only closes that door for the `paymasterPostOpGasLimit` component, not for `callGasLimit`.

### Impact Explanation
This is a direct paymaster-fund-loss vector matching the bounty's "stealing or loss of funds" and "logic attacks" categories: any regular unprivileged UserOp sender can force `SimplexPaymaster` to lose native ETH from its EntryPoint deposit disproportionate to the tiny ERC-20 fee actually charged, repeatable per UserOp with no permission or relayer/prover collusion required. Given the deposit is shared across all sponsored users, repeated abuse can exhaust the paymaster's EntryPoint balance, causing denial of sponsorship / fund loss for the protocol/treasury.

### Likelihood Explanation
High. The only actor required is the UserOp sender themselves — an ordinary, permissionless caller of a "fully permissionless" paymaster, satisfying the requirement of an unprivileged-attacker path with no admin/relayer/prover assumptions. Bundlers do simulate and estimate gas, but nothing in `SimplexPaymaster`'s validation logic rejects a UserOp with an inflated `callGasLimit`; the developer comment shows the team already recognizes this class of loss is real and worth guarding against for one of the four affected gas fields, yet left the largest one (`callGasLimit`) unbounded.

### Recommendation
Cap `userOp.callGasLimit` (and `verificationGasLimit`) in `_validatePaymasterUserOp`, symmetric to the existing `MAX_POST_OP_GAS_LIMIT` check, or alternatively charge the user in `_postOp`/via a custom `_postOp` override for the difference between prefunded worst-case cost and `actualGasCost` scaled to include the expected 10% penalty on the unused reserved gas, so the sender — who controls `callGasLimit` — bears the cost of their own over-reservation rather than the paymaster's deposit.

### Proof of Concept
1. Attacker (any address) submits a UserOp using `SimplexPaymaster` for sponsorship, with `paymasterData` in mode `0x01` (approve mode, no need for a real permit) and a modest token allowance.
2. Attacker sets `callGasLimit` to a very large value (e.g., 5,000,000) while their actual `callData`/execution only consumes ~30,000 gas.
3. `_validatePaymasterUserOp` only checks `paymasterPostOpGasLimit ≤ 100_000` (`evm/src/utils/SimplexPaymaster.sol:347-350`) — the inflated `callGasLimit` passes unchecked.
4. EntryPoint executes the UserOp; `actualGasCost` reflects only gas truly used, so `PaymasterERC20._postOp` charges the attacker's ERC-20 balance a small, correctly-priced amount.
5. EntryPoint v0.7/v0.8 then applies its 10% unused-execution-gas penalty on the ~4.97M unused `callGasLimit`, debiting that ETH amount from `SimplexPaymaster`'s EntryPoint deposit and crediting it per EntryPoint's penalty-beneficiary rules (typically the bundler/beneficiary of the transaction) — money the paymaster never recovered from the attacker.
6. Repeating this with many UserOps drains the paymaster's deposit far faster than its markup revenue can replenish it.

### Citations

**File:** evm/src/utils/SimplexPaymaster.sol (L108-112)
```text
    /// @dev Caps the caller-supplied postOp gas limit. Unbounded, the EntryPoint's
    ///      unused-gas penalty is drained from this contract's deposit to a
    ///      caller-chosen beneficiary; the cap keeps that penalty under the
    ///      `_postOpCost` cushion the user already pays.
    uint256 public constant MAX_POST_OP_GAS_LIMIT = 100_000;
```

**File:** evm/src/utils/SimplexPaymaster.sol (L342-364)
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
```

**File:** evm/src/utils/SimplexPaymaster.sol (L455-467)
```text
    /// @notice Estimate the token cost for a given gas amount and fee, mirroring
    ///         PaymasterERC20._erc20Cost (including its postOp gas cushion).
    function estimateTokenCost(
        address token,
        uint256 gasAmount,
        uint256 maxFeePerGas
    ) external view returns (uint256) {
        TokenConfig memory cfg = tokenConfigs[token];
        if (address(cfg.tokenOracle) == address(0)) revert TokenNotRegistered(token);

        uint256 weiCost = gasAmount * maxFeePerGas + _postOpCost() * maxFeePerGas;
        return (weiCost * _tokenPrice(cfg)) / _tokenPriceDenominator();
    }
```
