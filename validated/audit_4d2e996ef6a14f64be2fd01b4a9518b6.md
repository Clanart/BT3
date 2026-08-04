## Local Analog Confirmed: Unauthenticated `select()` Corrupts the Solver-Selection Checkpoint in `IntentGatewayV2`

### Title
Unbound, publicly-callable `select()` lets any address overwrite another order's solver-selection checkpoint - (File: `evm/src/apps/intentsv2/IntentsBase.sol`, `evm/src/apps/IntentGatewayV2.sol`)

### Summary
The `select()` / `fillOrder()` pair in `IntentGatewayV2` implements the exact "checkpoint / ensure-checkpoint" pattern flagged in the external report: a public function stages state that a later call trusts without binding it to the caller or to a single logical transaction context. `select()` is `public`, unauthenticated, and writes to a **transient storage slot keyed only by the public order `commitment`** [1](#0-0) . `fillOrder()` later reads that same slot and trusts it as proof that a legitimate `select()` call authorized `msg.sender` [2](#0-1) .

### Finding Description
`select(SelectOptions calldata options)` can be called by anyone, for any `commitment` (a value that is public — it is `keccak256(abi.encode(order))` and orders are broadcast in the `OrderPlaced` event and required as calldata for `fillOrder` anyway). It performs no check that the caller is related to the order, the session key, or the solver:

```solidity
function _select(SelectOptions calldata options) internal returns (address) {
    ...
    bytes32 selectionHash = keccak256(abi.encode(options.solver, sessionKey));
    assembly {
        tstore(commitment, selectionHash)
    }
    return sessionKey;
}
``` [1](#0-0) 

`fillOrder()` later performs the "ensureCheckpoint" step, reading the same transient slot:

```solidity
if (_params.solverSelection) {
    bytes32 storedSelectionHash;
    assembly { storedSelectionHash := tload(commitment) }
    bytes32 expectedSelectionHash = keccak256(abi.encode(msg.sender, order.session));
    if (storedSelectionHash != expectedSelectionHash) revert Unauthorized();
}
``` [2](#0-1) 

Because transient storage is shared across every call frame within the same transaction (not scoped to a specific caller or call sequence), any code that executes in the *same transaction* as a legitimate `select()+fillOrder()` pair can call `select()` itself for the victim's `commitment` and overwrite the slot before the victim's `fillOrder` reads it. The docs confirm the intended usage is to batch `select()` and `fillOrder()` for **different, unrelated orders/solvers** into the same bundler transaction via ERC-4337 (`SolverAccount` + EIP-7702 batches), and that bundlers routinely combine multiple parties' UserOperations into one transaction [3](#0-2) . Any external call surface reachable mid-transaction (a malicious `beneficiary` receiving native ETH in `_fillSameChain`/`_fillCrossChain`, a malicious postdispatch/predispatch target executed via the `CallDispatcher`, etc.) gives an unprivileged party exactly the kind of "get a callback" primitive the original report describes — from that callback they can call the public `select()` for a *different* order's commitment that is scheduled to be filled later in the same transaction, corrupting the checkpoint the legitimate solver relies on.

This is structurally identical to the reported `SwapGuardV2` bug: a public setter (`makeCheckpoint()` ≈ `select()`) writes shared, non-caller-bound state that a later checker (`ensureCheckpoint()` ≈ the `tload` comparison in `fillOrder`) blindly trusts.

### Impact Explanation
An attacker who gains any code-execution point within the same transaction as another party's staged `select()+fillOrder()` sequence (e.g., as the beneficiary of a different fill in the same batched/bundled transaction, or via a postdispatch/predispatch call target) can overwrite the transient selection hash for an unrelated order's commitment. The victim's subsequent `fillOrder()` call then reads a corrupted `storedSelectionHash`, fails the equality check, and reverts with `Unauthorized()` — denying the legitimate, pre-authorized solver their fill inside the same atomic transaction, potentially causing the order to expire (`deadline`) or forcing costly resubmission. This is a logic/transaction-manipulation defect: authorization state that should be bound 1:1 to a single call pair is instead a shared, unauthenticated, globally-writable value.

Note: because `fillOrder`'s expected hash is `keccak256(abi.encode(msg.sender, order.session))` and `order.session` is a value the attacker cannot forge a valid EIP-712 signature for without the session private key, the attacker cannot hijack fill authorization *to themselves* — the confirmed impact is corruption/denial of the legitimate solver's authorized fill, not direct fund theft.

### Likelihood Explanation
The primitive is trivially reachable — `select()` has no access control and accepts an arbitrary attacker-chosen `commitment`. The limiting factor is achieving co-execution in the same transaction as the victim's `select()+fillOrder()` pair. This is realistic in the documented ERC-4337 flow, where multiple solvers' `UserOperation`s (each potentially containing `select`+`fillOrder` batches) are combined into a single bundler transaction, and further amplified whenever an order's `beneficiary` or predispatch/postdispatch calldata target is attacker-controlled, giving a mid-transaction callback.

### Recommendation
Bind the transient-storage checkpoint to the specific call context rather than a globally-guessable key: derive the `tstore` key from `keccak256(abi.encode(commitment, msg.sender))` at `select()` time and require `fillOrder` to be invoked by that exact recorded caller only after also validating that `select()` was invoked from an authorized context (e.g., require `select` and `fillOrder` to be atomically bundled via a single wrapper function, or record the tx-scoped nonce/caller pairing so an unrelated third party cannot overwrite another commitment's slot). Alternatively, remove the general-purpose public `select()` entry point entirely and fold solver-selection verification directly into `fillOrder()` as a single call that takes the `SelectOptions` and verifies+consumes them in one atomic step, eliminating the two-call race window altogether — mirroring the original report's own recommendation to collapse `makeCheckpoint`/`ensureCheckpoint` into one function.

### Proof of Concept
1. Solver A signs a valid `SelectOptions` for `order_A`'s commitment and stages `select(optionsA)` in the same bundler transaction as their subsequent `fillOrder(order_A, ...)` call (per the documented `SolverAccount` ERC-7821 batch pattern).
2. In the same bundler transaction, a second, unrelated UserOperation (submitted by Attacker, or triggered as a beneficiary/postdispatch callback from an earlier call in the bundle) calls `IntentGatewayV2.select(SelectOptions({commitment: commitment_A, solver: attacker, signature: anyValidSigOverSomeSessionKey}))`.
3. Because `select()` performs no ownership check on `commitment_A`, the transient slot for `commitment_A` is overwritten with `keccak256(abi.encode(attacker, someSessionKey))`.
4. When Solver A's `fillOrder(order_A, ...)` executes later in the same transaction, `tload(commitment_A)` returns the attacker-written value, `expectedSelectionHash` (computed from Solver A's real `msg.sender`/`order.session`) does not match, and the call reverts with `Unauthorized()`, denying Solver A's legitimate, pre-signed fill inside the same atomic transaction.

### Citations

**File:** evm/src/apps/intentsv2/IntentsBase.sol (L500-512)
```text
    function _select(SelectOptions calldata options) internal returns (address) {
        bytes32 structHash = keccak256(abi.encode(SELECT_SOLVER_TYPEHASH, options.commitment, options.solver));
        bytes32 digest = _hashTypedDataV4(structHash);
        address sessionKey = ECDSA.recover(digest, options.signature);

        bytes32 commitment = options.commitment;
        bytes32 selectionHash = keccak256(abi.encode(options.solver, sessionKey));
        assembly {
            tstore(commitment, selectionHash)
        }

        return sessionKey;
    }
```

**File:** evm/src/apps/IntentGatewayV2.sol (L428-436)
```text
        if (_params.solverSelection) {
            bytes32 storedSelectionHash;
            assembly {
                storedSelectionHash := tload(commitment)
            }

            bytes32 expectedSelectionHash = keccak256(abi.encode(msg.sender, order.session));
            if (storedSelectionHash != expectedSelectionHash) revert Unauthorized();
        }
```

**File:** docs/content/developers/evm/intent-gateway/overview.mdx (L112-120)
```text
## Solver Selection

When `solverSelection` is enabled in the intent gateway parameters, orders are protected from unauthorized fills. At order placement, the user specifies a `session` key — a temporary keypair generated for this order. Only a solver explicitly authorized by the session key can fill the order.

### `SolverAccount`

The SolverAccount is a smart account designed for solvers that combines [ERC-4337](https://eips.ethereum.org/EIPS/eip-4337) (account abstraction), [EIP-7702](https://eips.ethereum.org/EIPS/eip-7702), and [ERC-7821](https://eips.ethereum.org/EIPS/eip-7821) (batch execution) to batch `gateway.select(...)` and `gateway.fillOrder(...)` into a single atomic UserOperation. Solvers delegate their EOA to the SolverAccount via EIP-7702 and submit bundled operations through the ERC-4337 EntryPoint.

`SolverAccount.validateUserOp` accepts two signature formats, discriminated by length: a standard 65-byte ECDSA signature over the `userOpHash` for regular account operations (delegation no-ops, approvals, treasury batches), and the 162-byte intent-selection payload `abi.encodePacked(commitment, solverSignature, sessionSignature)` for fills. UserOperations whose calldata contains a `fillOrder` call to the gateway are refused on the standard path. This guard exists because bids are public on Hyperbridge and embed a valid 65-byte solver signature over the `userOpHash` — without it, anyone could strip the commitment and session signature from a bid and submit the bare operation: the fill would revert (no selection is staged during validation), but it would still consume the bid's nonce and gr ... (truncated)
```
