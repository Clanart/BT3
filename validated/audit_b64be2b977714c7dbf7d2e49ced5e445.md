Confirmed: `SimplexPaymaster._validatePaymasterUserOp` at [1](#0-0)  only bounds `paymasterPostOpGasLimit` against `MAX_POST_OP_GAS_LIMIT`, never checking or bounding `userOp.callGasLimit`. That is enough to build the analog.

### Title
Unbounded `callGasLimit` lets any UserOp sender drain `SimplexPaymaster`'s EntryPoint deposit via the ERC‑4337 v0.8 unused-gas penalty - (File: `evm/src/utils/SimplexPaymaster.sol`)

### Summary
`SimplexPaymaster` is a permissionless ERC‑4337 v0.8 paymaster that charges users an ERC‑20 amount computed from `actualGasCost` inside `PaymasterERC20._postOp` (inherited, unmodified). The EntryPoint v0.8 (`ERC4337Utils.ENTRYPOINT_V08`, referenced at [2](#0-1)  and comments at [3](#0-2) ) applies a 10% penalty on *unused* `executionGasLimit = callGasLimit + paymasterPostOpGasLimit` after `postOp()` has already run and already computed the ERC‑20 charge. `SimplexPaymaster` caps `paymasterPostOpGasLimit` at `MAX_POST_OP_GAS_LIMIT = 100_000` ( [3](#0-2) ) but never validates or bounds `userOp.callGasLimit` anywhere in `_validatePaymasterUserOp` ( [1](#0-0) ).

### Finding Description
This is the same broken invariant as the external report's `GasTankPaymaster::_postOp` bug: the paymaster's ERC‑20 charge is computed from `actualGasCost`/`actualGas`, a value that is finalized in `PaymasterERC20._postOp` **before** the EntryPoint applies the unused-gas penalty on `executionGasLimit - executionGasUsed`. The penalty is settled purely in native ETH against the paymaster's EntryPoint deposit during `_postExecution`, completely decoupled from what the user was actually charged in tokens.

`SimplexPaymaster`'s own code comment acknowledges this exact class of loss ("the EntryPoint's unused-gas penalty is drained from this contract's deposit to a caller-chosen beneficiary") and claims the `MAX_POST_OP_GAS_LIMIT` cap "keeps that penalty under the `_postOpCost` cushion the user already pays." That claim is false: `executionGasLimit = callGasLimit + paymasterPostOpGasLimit`, and only the `paymasterPostOpGasLimit` term is capped. `callGasLimit` is entirely attacker/sender-controlled and unbounded by the paymaster (bounded only by the block gas limit and the bundler's own risk tolerance, not by anything in this contract). Any sender can set `callGasLimit` arbitrarily large while the account's actual call consumes very little gas, producing a large `unusedGas` and thus a large `unusedGasPenalty` — up to 10% of the entire unused `callGasLimit` — charged in ETH against `SimplexPaymaster`'s EntryPoint deposit, while the ERC‑20 amount collected from the user (via `_fetchDetails`/`_postOp`) reflects only the small actual gas used plus the fixed `_postOpCost()` cushion (30_000 gas per `POST_OP_COST` in the test file, [4](#0-3) ).

### Impact Explanation
This is a direct, permissionless fund-loss path against the paymaster's EntryPoint-deposited ETH: `SimplexPaymaster` pays a native-ETH penalty proportional to `callGasLimit - actualCallGasUsed` that is never recovered from the user, who only pays for actual usage plus a small fixed cushion. Since `callGasLimit` can be set arbitrarily large (limited only by the block gas limit / EntryPoint's own caps), the drained amount per UserOp can be made significant. This is qualitatively identical to the accepted M‑04 finding on `GasTankPaymaster`, and it survives specifically because the mitigation implemented here (`MAX_POST_OP_GAS_LIMIT`) addresses only the `paymasterPostOpGasLimit` half of `executionGasLimit`, leaving `callGasLimit` — the dominant term in typical UserOps — completely unchecked.

### Likelihood Explanation
High. No privileged role, relayer, prover, or governance action is required — any account that can submit a UserOp through the standard EntryPoint `handleOps` public entrypoint controls its own `callGasLimit` and can trivially exploit this by setting a high `callGasLimit` and doing minimal work in `execute()`/`callData`. This is fully reachable by an ordinary end-user account interacting with a public, permissionless paymaster, with no special conditions beyond bundler acceptance of the UserOp (bundlers do not protect the paymaster's economic interests).

### Recommendation
Bound `userOp.callGasLimit` (in addition to `paymasterPostOpGasLimit`) in `_validatePaymasterUserOp`, e.g. reject UserOps whose `callGasLimit` exceeds a governance-configurable maximum, or size the ERC‑20 charge to account for the worst-case 10% EntryPoint penalty on the full `callGasLimit + paymasterPostOpGasLimit` window rather than only on actual gas used, mirroring the fix applied to `GasTankPaymaster::_postOp` in the referenced report (subtract/account for prefund vs. worst-case penalty before charging).

### Proof of Concept
1. Attacker deploys/uses a smart account whose `execute()` call for a given `callData` does minimal work (e.g., a no-op call), consuming ~21,000–30,000 gas.
2. Attacker submits a UserOp through the canonical EntryPoint v0.8 with:
   - `callGasLimit = 2,000,000` (or higher, up to block gas limit)
   - `paymasterPostOpGasLimit = 100,000` (at the `MAX_POST_OP_GAS_LIMIT` cap enforced by `_validatePaymasterUserOp`, [5](#0-4) )
   - `paymasterAndData` referencing `SimplexPaymaster` in APPROVE mode with a registered token.
3. EntryPoint executes the call (actual gas ~30,000), then calls `SimplexPaymaster._postOp` (inherited `PaymasterERC20._postOp`), which computes and pulls an ERC‑20 amount sized to `actualGasCost` (≈30,000 gas + `_postOpCost()` cushion) — a small charge.
4. EntryPoint's `_postExecution` then computes `executionGasLimit = 2,000,000 + 100,000 = 2,100,000`, `executionGasUsed ≈ 30,000 + postOp gas`, `unusedGas ≈ 2,000,000+`, and adds a 10% penalty (~200,000 gas-equivalent) to `actualGas`, settling `actualGasCost` against `SimplexPaymaster`'s EntryPoint deposit in ETH.
5. `SimplexPaymaster` loses ETH from its EntryPoint deposit equal to ~200,000 gas × gasPrice, while having only collected an ERC‑20 amount corresponding to ~30,000 + 30,000 (cushion) gas from the user — a net uncompensated loss repeatable on every UserOp.

### Citations

**File:** evm/src/utils/SimplexPaymaster.sol (L108-112)
```text
    /// @dev Caps the caller-supplied postOp gas limit. Unbounded, the EntryPoint's
    ///      unused-gas penalty is drained from this contract's deposit to a
    ///      caller-chosen beneficiary; the cap keeps that penalty under the
    ///      `_postOpCost` cushion the user already pays.
    uint256 public constant MAX_POST_OP_GAS_LIMIT = 100_000;
```

**File:** evm/src/utils/SimplexPaymaster.sol (L169-171)
```text
        if (host_ == address(0) || host_.code.length == 0) revert InvalidHost();
        if (tokens_.length != oracles_.length) revert LengthMismatch();

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

**File:** evm/tests/foundry/SimplexPaymasterTest.t.sol (L139-140)
```text
    // PaymasterERC20._postOpCost()
    uint256 constant POST_OP_COST = 30_000;
```
