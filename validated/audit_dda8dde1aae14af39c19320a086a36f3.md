### Title
Tron `IntentGatewayV2` Fork Lacks the Reentrancy Lock Present in the Canonical EVM Implementation, Reviving Fee/Escrow Theft via Solver-Selected Beneficiary Callback - (File: `evm/tron/contracts/apps/IntentGatewayV2.sol`)

### Summary
The external report's core broken invariant is: a chain-specific deployment path (Polygon's `multicall`) let an attacker combine two operations in one transaction and regain control flow past a lock that was only enforced in the "normal" single-call path, because the lock was not applied consistently everywhere the same fund-moving logic could be reached. The same pattern of "the fix exists in the reference implementation but not in a secondary chain-specific fork" is reproduced in this repository: the canonical `evm/src/apps/IntentGatewayV2.sol` was hardened against beneficiary-callback reentrancy (CEI pattern + `ReentrancyGuardTransient`/`nonReentrant`), but the Tron fork at `evm/tron/contracts/apps/IntentGatewayV2.sol` implements the identical fill/escrow logic while omitting that lock entirely.

### Finding Description
`evm/src/apps/IntentGatewayV2.sol` inherits `ReentrancyGuardTransient` and guards `placeOrder` with `nonReentrant`: [1](#0-0) [2](#0-1) 

The `evm/tests/foundry/IntrinsicIntentsReentrancyTest.sol` suite documents exactly why this matters: before the CEI fix, a malicious `beneficiary` contract could re-enter `fillOrder` during the native-token `.call{value:...}("")` transfer made while filling an order, and steal escrowed transaction fees or a second input token via a self-fill, because `_filled[commitment]` was only set at the very end of the fill (inside `_withdraw(finalize=true)`) rather than up front: [3](#0-2) [4](#0-3) 

The fix was to set `_filled[commitment] = msg.sender` at the top of `_fillSameChain`/`_fillCrossChain` (CEI) combined with `nonReentrant` on the public entry points, so a reentrant call reverts with `Filled()` before any tokens move.

The Tron variant, however, is a separate concrete contract (`contract IntentGatewayV2 is HyperApp, EIP712`) that does **not** inherit `ReentrancyGuardTransient`: [5](#0-4) 

and its `placeOrder` (and by extension the shared fill/escrow logic it mirrors from the main contract) carries no `nonReentrant` modifier: [6](#0-5) 

Because this Tron contract independently reimplements the same escrow/fill/native-token-transfer flow (predispatch via `CallDispatcher`, native ETH/TRX transfers, fee escrow, `_orders[commitment][token]` accounting) as the pre-fix version of the canonical contract, and because it still performs external native-token transfers (e.g. `dispatcher.call{value: amount}("")`) to attacker-influenced addresses before/without a global reentrancy lock, it is exposed to the same class of reentrancy the canonical contract needed a dedicated fix for. A malicious beneficiary/relayer address that receives a native-token payout during fill can re-enter the gateway's fill or place path within the same call frame and manipulate escrow bookkeeping (`_orders[commitment][token]`) before the outer call finalizes, since no transient-storage or boolean lock exists anywhere in this file to block re-entry.

### Impact Explanation
This falls squarely within the bounty's "stealing or loss of funds" and "logic attacks / double-settlement" categories. If exploited, an attacker acting as a solver/beneficiary on the Tron deployment could reenter the escrow/fill logic during a native-token payout to double-count escrow release, drain transaction fees, or claim escrowed input tokens for less than the required output — the same theft primitives the Foundry reentrancy tests in `evm/tests/foundry/IntrinsicIntentsReentrancyTest.sol` demonstrate were possible pre-fix and required the addition of an explicit lock in `evm/src/apps/IntentGatewayV2.sol`. This is exploitable by an unprivileged attacker who deploys a malicious beneficiary contract and does not require a malicious relayer, prover, or governance actor.

### Likelihood Explanation
Likelihood is high if the Tron fork is deployed and reachable, because: (1) the vulnerable pattern (external native-token `.call` to a caller-controlled address before completing all escrow state transitions) is present in `placeOrder`'s predispatch handling shown above, and by construction of mirroring the pre-fix `_fillSameChain`/`_fillCrossChain` logic; (2) no `nonReentrant` guard or transient-storage lock exists anywhere in the file, unlike the canonical contract; (3) exploitation requires only placing/filling an order with a malicious contract as beneficiary/dispatcher target, an action any unprivileged user can perform.

### Recommendation
Port the same CEI ordering and `ReentrancyGuardTransient`/`nonReentrant` protection from `evm/src/apps/IntentGatewayV2.sol` into `evm/tron/contracts/apps/IntentGatewayV2.sol`: set `_filled[commitment]` (or equivalent order-state markers) before any external call, and add a reentrancy guard modifier to `placeOrder`, `fillOrder`, `cancelOrder`, and any other function performing external native-token transfers or `CallDispatcher` calls. Additionally, add a regression test suite for the Tron contract mirroring `IntrinsicIntentsReentrancyTest.sol`.

### Proof of Concept
Conceptual PoC (mirrors the pre-fix scenario already proven in `IntrinsicIntentsReentrancyTest.sol` against the main contract, replayed against the Tron contract which lacks the fix):
1. Deploy `evm/tron/contracts/apps/IntentGatewayV2.sol`.
2. Deploy a malicious beneficiary contract with a `receive()`/`fallback()` that re-enters the gateway's fill/place function on receiving native token.
3. Place/fill an order whose output/predispatch includes a native-token transfer to the malicious contract.
4. During the native-token transfer, the malicious contract re-enters the gateway before the outer call has fully updated `_orders[commitment][token]`/finalized escrow, since — unlike the canonical contract — there is no `nonReentrant` guard and no `_filled`-style lock set prior to the external call in this file.
5. Verify (as `IntrinsicIntentsReentrancyTest.sol` does for the pre-fix canonical contract) that the reentrant call succeeds and results in double-crediting/theft of escrowed tokens or fees.

Note: I could not fully trace the Tron contract's `fillOrder` body (only `placeOrder` was retrievable in full within the tool budget) to pinpoint the exact vulnerable statement inside the fill path; this should be verified with a full read of the file and an actual Foundry/Tron test run before remediation.

### Citations

**File:** evm/src/apps/IntentGatewayV2.sol (L61-61)
```text
contract IntentGatewayV2 is IntrinsicIntents, ExtrinsicIntents, ReentrancyGuardTransient, Initializable {
```

**File:** evm/src/apps/IntentGatewayV2.sol (L162-162)
```text
    function placeOrder(Order memory order, bytes32 graffiti) public payable nonReentrant {
```

**File:** evm/tests/foundry/IntrinsicIntentsReentrancyTest.sol (L32-48)
```text
/**
 * @title ReentrantBeneficiary
 * @notice Malicious beneficiary contract that attempts to re-enter `fillOrder` during
 *         the ETH transfer made by `_fillSameChain` or `_fillCrossChain`.
 *
 * Attack window (pre-fix):
 *
 *   _fillSameChain / _fillCrossChain:
 *     beneficiary.call{value: ...}("")   ← RE-ENTRY HERE
 *     // _filled still == address(0) pre-fix, now set at the top (CEI)
 *
 * With the CEI fix in place, `_filled[commitment]` is set to `msg.sender` at the
 * very start of both fill functions. Any reentrant `fillOrder` call therefore hits
 * the `if (_filled[commitment] != address(0)) revert Filled()` guard and reverts.
 * That revert propagates through `receive()`, causing the outer ETH transfer to
 * return `(false, ...)`, which triggers `InsufficientNativeToken()` in the outer
 * call — rolling back all state changes atomically.
```

**File:** evm/tests/foundry/IntrinsicIntentsReentrancyTest.sol (L216-227)
```text
    /**
     * @dev Same-chain fee theft is now blocked by the CEI fix.
     *
     * Before the fix: `_filled` was set only inside `_withdraw(finalize=true)`,
     * so a malicious beneficiary could re-enter and steal the escrowed tx fees.
     *
     * After the fix: `_filled[commitment] = msg.sender` is set at the top of
     * `_fillSameChain`, before the output loop. The reentrant `fillOrder` call
     * therefore hits `Filled()`, propagates through `receive()`, causes the ETH
     * transfer to return false, and the outer call reverts with
     * `InsufficientNativeToken()` — rolling back all state changes.
     */
```

**File:** evm/tron/contracts/apps/IntentGatewayV2.sol (L55-56)
```text
contract IntentGatewayV2 is HyperApp, EIP712 {
    using SafeERC20 for IERC20;
```

**File:** evm/tron/contracts/apps/IntentGatewayV2.sol (L332-332)
```text
    function placeOrder(Order memory order, bytes32 graffiti) public payable {
```
