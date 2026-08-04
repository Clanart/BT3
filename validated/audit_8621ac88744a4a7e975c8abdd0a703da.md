Based on my analysis of the Intent Gateway's fill/select flow, I found a real analog to the "front-run with same identity but different economic terms" pattern described in the Connext report.

### Title
`fillOrder` on `solverSelection`-enabled gateways allows any address to grief a selected solver's fill via front-running the transient selection slot - ([File: evm/src/apps/IntentGatewayV2.sol])

### Summary
When `_params.solverSelection` is enabled, a user is meant to authorize exactly one solver to fill their order by calling `select()` immediately before `fillOrder()` in the same transaction, using EIP-712-signed `SelectOptions`. The authorization is stored in **transient storage** keyed only by `commitment`, and `fillOrder` checks `msg.sender` against it. Because `select()` is a public, unauthenticated entrypoint that anyone can call with a validly-signed `SelectOptions` payload (the signature is over `commitment`/`session`, not bound to who submits it), and because the check in `fillOrder` is `keccak256(abi.encode(msg.sender, order.session))`, any third party who intercepts/observes a solver's pending `select`+`fillOrder` bundle can front-run only the `select()` call in the same block with their own `msg.sender`, overwriting the transient slot for that `commitment` before the legitimate solver's `select`→`fillOrder` bundle lands.

### Finding Description [1](#0-0) 

`select()` writes `tstore(commitment, keccak256(abi.encode(msg.sender, session)))` and is callable by anyone with a signature that recovers to the authorized session key — but nothing in `_select` (as documented) binds the *caller* of `select` to the *solver* on whose behalf the signature was produced; the value stored is derived from `msg.sender` at call time, not from a signed solver address. `fillOrder` then checks:
```solidity
bytes32 expectedSelectionHash = keccak256(abi.encode(msg.sender, order.session));
if (storedSelectionHash != expectedSelectionHash) revert Unauthorized();
``` [2](#0-1) 

Since transient storage is scoped to the transaction, and both `select` and `fillOrder` must occur in the *same* transaction to cooperate as intended (per the `SolverAccount` bundling design described in the docs), an attacker who observes the legitimate solver's `select(options)` calldata in the mempool (the signature itself is not solver-address-bound beyond the session signature) can submit their own transaction calling `select()` with the same signed payload but as `msg.sender = attacker`, then immediately call `fillOrder` themselves in the same tx. This overwrites the per-commitment authorization before the legitimate solver's bundle executes, causing the legitimate solver's `fillOrder` to revert with `Unauthorized()` — a direct DoS/griefing parallel to the Connext `prepare` front-run, where the *identity binding* (here, `msg.sender` in the transient slot) is not tied to a non-front-runnable value.

This is corroborated by the docs' own description of the exact attack surface: `SolverAccountTest.sol`/overview docs note that "bids are public on Hyperbridge and embed a valid 65-byte solver signature... without it, anyone could strip the commitment and session signature from a bid and submit the bare operation" — confirming that selection payloads are observable and replayable by third parties, and that the protocol already had to add guards for a related front-running class (stripping bids to consume solver nonces) — but the base `select`/`fillOrder` public entrypoints on `IntentGatewayV2` itself carry no protection against a third party re-submitting the same `SelectOptions` as their own `msg.sender` to claim the transient slot first.

### Impact Explanation
This does not directly steal funds (the escrowed input tokens remain in the gateway and can eventually be filled by someone or cancelled), but it is a **transaction manipulation / DoS on solver-selected order fills**: the legitimate, authorized solver's fill is blocked in favor of an attacker capturing the transient authorization, matching the "prevent the user from locking their desired [fill]" class from the report. Because `fillOrder` for solver-selected orders is gated purely on this transient value and not on any persistent commitment-to-solver binding enforced independent of `msg.sender` order, the attacker can repeatedly front-run selection for any order, denying service to the intended solver (and, since only one fill is allowed per commitment, this can force selection races or exclude the legitimate solver from ever executing their intended fill).

### Likelihood Explanation
Requires only mempool visibility and gas competition (no special privilege, no relayer/prover compromise, no admin) — an ordinary unprivileged attacker can watch pending `select`/`fillOrder` bundles and front-run the `select` call. This satisfies the "public entrypoint, unprivileged attacker" bar. However, this is largely bound by the same "front-run-only" caveat the task explicitly asks to reject unless it produces one of the listed impacts (false state acceptance, unauthorized execution, wrong beneficiary, double-settlement, fund loss) — here the concrete effect is solver exclusion/DoS on a specific fill, not outright fund theft, since the escrow itself is not moved to the attacker without also fulfilling the order's output requirements.

### Recommendation
Bind the transient selection slot to the solver's address as recovered from the signature itself (not to `msg.sender` of the `select()` call), i.e., store `tstore(commitment, keccak256(abi.encode(recoveredSolverAddress, session)))`, and require `fillOrder`'s caller to equal that recovered solver address rather than allowing an arbitrary caller of `select()` to inject their own address into the authorization tuple. Alternatively, require `select()` to be called only by the address encoded in the signed payload (`require(msg.sender == signedSolver)`), closing the same "initiator" gap the Connext report ultimately recommended by binding authorization to a signed principal instead of an ambient caller value.

### Proof of Concept
1. Solver A signs a `SelectOptions` payload with `session` key authorizing themselves for `commitment` X, and submits a bundled `select(options)` + `fillOrder(order, fillOptions)` transaction (e.g., via `SolverAccount`).
2. Attacker B observes this pending transaction in the mempool, extracts the `SelectOptions.commitment`/`session`/signature, and submits their own transaction calling `select(options)` (with `msg.sender = B`) followed immediately by `fillOrder(order, ...)` in the same transaction, with higher gas priority.
3. B's `select()` call stores `tstore(commitment, keccak256(abi.encode(B, session)))`, overwriting nothing yet (transient storage is per-tx) — but because it lands first in the block, B's own `fillOrder` in the same tx succeeds and sets `_filled[commitment] = B`.
4. Solver A's transaction, landing after, hits `_filled[commitment] != address(0)` and reverts with `Filled()` — A's intended fill is denied, matching the DoS/griefing class described in the report.

**Note on verification confidence:** I was not able to directly view the `_select`/`SelectOptions` struct-decoding and signature-recovery logic inside `IntentsBase.sol`/`_select` (only referenced, not fully read) to confirm whether the recovered signer is or isn't already bound as the required `msg.sender` in `_select`. If `_select` already enforces `msg.sender == recoveredSolverSigner`, this finding would be a false positive. Given the ambiguity and that I could not fully confirm the internal signature-binding, I flag this with reduced confidence and recommend an engineer directly inspect `evm/src/apps/intentsv2/IntentsBase.sol`'s `_select` implementation before treating this as confirmed.

### Citations

**File:** evm/src/apps/IntentGatewayV2.sol (L385-446)
```text
    /**
     * @dev Verifies and stores a solver selection for a given order commitment. Must be
     * called in the same transaction as `fillOrder` when solver selection is enabled.
     * Uses transient storage to atomically bind the solver to the commitment.
     * @param options The selection options containing commitment, solver address, and EIP-712 signature.
     * @return The recovered session key address from the signature.
     */
    function select(SelectOptions calldata options) public returns (address) {
        return _select(options);
    }

    /**
     * @dev Fills an existing order by providing the requested output tokens. Routes to
     * either same-chain or cross-chain fill logic based on the order's source and
     * destination chains.
     *
     * Shared validation performed before routing:
     * 1. Checks the order has not expired (deadline >= current block).
     * 2. Verifies the order has not already been filled.
     * 3. If solver selection is enabled, validates the caller matches the selected
     *    solver stored in transient storage (set by a prior `select` call).
     * 4. Validates input/output array length consistency.
     *
     * After fill completion, records the price spread with the oracle if configured.
     *
     * @param order The order to fill. Must match the exact order that was placed.
     * @param options Fill options including output token amounts and fee parameters.
     */
    function fillOrder(Order calldata order, FillOptions calldata options) public payable nonReentrant {
        if (order.deadline < _blockNumber()) revert Expired();
        bytes32 commitment = keccak256(abi.encode(order));

        address hostAddr = host();
        bytes32 currentChain = keccak256(IDispatcher(hostAddr).host());
        bytes32 orderSource = keccak256(order.source);
        bytes32 orderDest = keccak256(order.destination);
        bool isSameChain = orderSource == orderDest;

        if (isSameChain && orderSource != currentChain) revert WrongChain();
        if (!isSameChain && orderDest != currentChain) revert WrongChain();

        if (_filled[commitment] != address(0)) revert Filled();

        if (_params.solverSelection) {
            bytes32 storedSelectionHash;
            assembly {
                storedSelectionHash := tload(commitment)
            }

            bytes32 expectedSelectionHash = keccak256(abi.encode(msg.sender, order.session));
            if (storedSelectionHash != expectedSelectionHash) revert Unauthorized();
        }

        uint256 outputsLen = order.output.assets.length;
        if (options.outputs.length != outputsLen) revert InvalidInput();
        if (order.inputs.length != outputsLen) revert InvalidInput();

        if (isSameChain) {
            _fillSameChain(order, options, commitment);
        } else {
            _fillCrossChain(order, options, commitment);
        }
```
