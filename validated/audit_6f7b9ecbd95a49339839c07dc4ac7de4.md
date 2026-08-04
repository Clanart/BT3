### Title
Unbounded `paymasterVerificationGasLimit` lets any UserOp sender drain `SimplexPaymaster`'s EntryPoint deposit via ERC-4337's unused-gas penalty - (File: evm/src/utils/SimplexPaymaster.sol)

### Summary
`SimplexPaymaster._validatePaymasterUserOp` only caps `userOp.paymasterPostOpGasLimit()` against `MAX_POST_OP_GAS_LIMIT` (100,000), but never validates `userOp.paymasterVerificationGasLimit()`. The developers' own comment on `MAX_POST_OP_GAS_LIMIT` explains exactly why this class of field must be capped: "Unbounded, the EntryPoint's unused-gas penalty is drained from this contract's deposit to a caller-chosen beneficiary." Since only one of the two attacker-controlled gas fields subject to this penalty is capped, the mitigation is incomplete and the same fund-drain path remains open through `paymasterVerificationGasLimit`. [1](#0-0) 

### Finding Description
`SimplexPaymaster` is a permissionless ERC-4337 paymaster; anyone can craft a `PackedUserOperation` naming this contract as paymaster and submit it (self-bundled, choosing their own `beneficiary` in `EntryPoint.handleOps`).

`_validatePaymasterUserOp` performs exactly one gas-limit sanity check: [2](#0-1) 

```solidity
uint256 postOpGasLimit = userOp.paymasterPostOpGasLimit();
if (postOpGasLimit > MAX_POST_OP_GAS_LIMIT) {
    revert InvalidPostOpGasLimit(postOpGasLimit, MAX_POST_OP_GAS_LIMIT);
}
```

Nowhere does the function (or `_fetchDetails`, or `_executePermit`) read or bound `userOp.paymasterVerificationGasLimit()`. That field, together with `paymasterPostOpGasLimit`, is exactly the pair of reserved-gas parameters that EntryPoint (v0.7/0.8) charges against the paymaster's deposit up-front and later penalizes for being under-used, paying the difference to the caller-chosen `beneficiary` address — the same mechanism the contract's own comment describes for `postOpGasLimit`: [1](#0-0) 

Because token-side economics (the ERC-20 amount ultimately charged to the sender in `_postOp`/`_fetchDetails`) are computed from *actual* gas used, not from the reserved limits, an attacker can:
1. Set a tiny, cheap-to-execute UserOp (so actual verification/postOp gas usage is minimal and the ERC-20 cost charged to them is negligible).
2. Set `paymasterVerificationGasLimit` to a very large value while keeping the token registered/active so `_validatePaymasterUserOp` passes (mode `0x01`/approve with a trivial pre-approved allowance, or mode `0x00`/permit with a small `permitAmount`).
3. Submit the op themselves via `handleOps`, naming an address they control as `beneficiary`.

EntryPoint reserves `paymasterVerificationGasLimit` gas-cost against the paymaster's ETH deposit as part of `maxCost`, and since only a small fraction is actually consumed, the unused portion's penalty component is paid out to the attacker's `beneficiary`, draining the shared `SimplexPaymaster` EntryPoint deposit — funds contributed by `swapAndDeposit`/governance and shared across all sponsored users.

The existing guard (`MAX_POST_OP_GAS_LIMIT`) does not stop this path because it only inspects `paymasterPostOpGasLimit`; it performs no check on `paymasterVerificationGasLimit` at all, so the value is fully attacker-controlled and unconstrained.

### Impact Explanation
This directly causes loss of funds: the paymaster's pooled EntryPoint deposit (shared by all legitimate sponsored users) can be siphoned to an attacker-controlled beneficiary through repeated cheap UserOps with inflated `paymasterVerificationGasLimit`, at negligible cost to the attacker (only the token cost for minimal actual execution). This is an unprivileged, permissionless attack requiring no relayer/prover/admin compromise — it fits the "stealing or loss of funds" and "logic attack" categories of the bounty scope.

### Likelihood Explanation
High. `SimplexPaymaster` is explicitly documented as "Fully onchain, permissionless" — any address can submit a sponsored UserOp naming this paymaster, and any address can act as the bundler (call `handleOps` directly) choosing its own beneficiary, since ERC-4337 does not restrict who calls `handleOps`. The only defense the contract implements against gas-limit-based value extraction (`MAX_POST_OP_GAS_LIMIT`) proves the team was aware of and tried to close this exact class of issue, but left the twin field (`paymasterVerificationGasLimit`) unguarded.

### Recommendation
Add an analogous cap in `_validatePaymasterUserOp`:
```solidity
uint256 verificationGasLimit = userOp.paymasterVerificationGasLimit();
if (verificationGasLimit > MAX_VERIFICATION_GAS_LIMIT) {
    revert InvalidVerificationGasLimit(verificationGasLimit, MAX_VERIFICATION_GAS_LIMIT);
}
```
Set `MAX_VERIFICATION_GAS_LIMIT` based on the actual measured gas cost of `_validatePaymasterUserOp` (permit path included) plus a small safety margin, mirroring the reasoning already applied to `MAX_POST_OP_GAS_LIMIT`.

### Proof of Concept
1. Attacker deploys/uses a smart account and pre-approves `SimplexPaymaster` for a trivial USDC amount (mode `0x01`).
2. Attacker builds a `PackedUserOperation` with:
   - `paymasterAndData` = `paymaster || paymasterVerificationGasLimit(uint128, set to a very large value, e.g. several million) || paymasterPostOpGasLimit(<= MAX_POST_OP_GAS_LIMIT) || data(mode 0x01, token)`
   - `callData` a no-op/cheap call.
3. Attacker calls `EntryPoint.handleOps([userOp], attackerBeneficiary)` directly (self-bundling; no bundler/relayer trust required).
4. `_validatePaymasterUserOp` only checks `postOpGasLimit` (within cap) and passes; `paymasterVerificationGasLimit` is never checked.
5. EntryPoint reserves gas cost based on the inflated `paymasterVerificationGasLimit` against `SimplexPaymaster`'s deposit; actual verification gas used is far lower, and the EntryPoint's unused-gas penalty for the difference is paid to `attackerBeneficiary` from the paymaster's deposit.
6. Attacker repeats the process to continuously drain the shared paymaster deposit.

Note: I was not able to inspect the exact EntryPoint v0.8 penalty-calculation bytecode/library used in this repo (it's an imported OpenZeppelin/`account-abstraction` dependency) to confirm the exact penalty percentage and payout path; this assessment relies on the standard ERC-4337 v0.7/0.8 specification behavior referenced by the contract's own inline comment, which explicitly documents this exact "unused-gas penalty drained ... to a caller-chosen beneficiary" mechanism for the sibling `postOpGasLimit` field.

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
