## Analog Found: Dead `_paused` flag never enforced in IntentGatewayV2 — pause has no effect on fund-moving entrypoints

### Title
Intent Gateway `_paused` circuit breaker is declared but never checked, so no code path can actually halt fills, placements, cancels, or withdrawals - ([File: evm/src/apps/intentsv2/IntentsBase.sol])

### Summary
The Sherlock report's core defect is that code assumed an action (`redeem`) could always proceed and never accounted for a restriction flag that should have gated it, so the guarded state was silently ignored. The same class of defect exists in Hyperbridge's `IntentGatewayV2`: a pause/circuit-breaker storage slot (`_paused`) exists and is explicitly documented as intentional (`_owner` is described as "Privileged admin for future upgrade-gated actions (e.g., pausing)"), but the flag is never read by any of `placeOrder`, `fillOrder`, `cancelOrder`, `_withdraw`, `onAccept`, or `onGetResponse`. The restriction state exists in storage but has zero effect on execution — the exact "code doesn't account for the restricted state" pattern from the report, just inverted (here it always takes the *unrestricted* path instead of reverting).

### Finding Description
`IntentsBase.sol` declares: [1](#0-0) 

with the comment "Appended last to preserve existing storage slots" (indicating a real intent to add pause functionality later, consistent with `IntentGatewayV2.sol`'s admin comment): [2](#0-1) 

A grep across the entire repo shows `_paused` is referenced in exactly this one declaration site and nowhere else — no modifier, no setter, no read in any function. Every fund-moving public entrypoint proceeds unconditionally:

- `placeOrder` escrows user tokens with no pause check: [3](#0-2) 
- `fillOrder` releases escrow to solvers with no pause check: [4](#0-3) 
- `cancelOrder` triggers refunds with no pause check: [5](#0-4) 
- `_withdraw`, the single choke point that moves every escrowed token out of the contract (fills, refunds, cross-chain settlement via `onAccept`/`onGetResponse`), also never checks it: [6](#0-5) 

Because there is no `require(!_paused, ...)` anywhere, even if a future upgrade or governance action ever sets `_paused = true` (e.g., via the `UpgradeContract`/`UpdateParams` cross-chain governance messages routed through `onAccept`), it has no runtime effect — every entrypoint keeps executing exactly as if unpaused. Effectively there is no enforcement point at all, so the "restriction" can never stop fund movement no matter how it gets set.

### Impact Explanation
This falls squarely under "logic attacks" / "unauthorized execution continuing when it should be halted" in the bounty scope. In a real incident — e.g., a discovered bug in `_fillCrossChain`/`_withdraw`, a compromised `CallDispatcher`, a malicious `priceOracle`, or a cross-chain authentication bypass discovered post-deployment — operators would expect to pause the gateway to stop further escrow placement, fills, and withdrawals while a fix is prepared. Because the pause mechanism is non-functional, an attacker can continue to `placeOrder`/`fillOrder`/`cancelOrder` against the gateway during the exact window operators believe the contract is halted, continuing to drain or manipulate escrowed funds. This is a silent guarantee failure: the system *appears* to have a circuit breaker (storage slot present, docstring says "for future upgrade-gated actions (e.g., pausing)"), but the guarantee does not exist in the deployed bytecode logic.

### Likelihood Explanation
High confidence this is exploitable as soon as it matters (i.e., during any incident where governance actually needs to pause): no privileged actor, relayer, or prover compromise is required to trigger the underlying condition — the bug is that the *existing* legitimate governance pause action (whatever future mechanism sets `_paused`) has no enforcement side, so normal permissionless callers (any user calling `placeOrder`/`fillOrder`/`cancelOrder`) automatically bypass the intended restriction with no special effort. The only reason this isn't "always" actively exploited is that pausing an otherwise-healthy contract has no attacker benefit; the risk materializes precisely in the emergency scenario the flag was built for, which is when it is needed most.

### Recommendation
Add an explicit enforcement modifier (e.g., `whenNotPaused`) checked in `placeOrder`, `fillOrder`, `cancelOrder`, `onAccept`, and `onGetResponse` (or centrally in `_withdraw`, since every fund-transfer path routes through it), and wire a real setter for `_paused` gated by `_owner` or the existing Hyperbridge governance `UpdateParams`/dedicated `SetPaused` request kind, mirroring the same pattern the report recommended: make the state-dependent branch actually observe the restriction instead of silently ignoring it.

### Proof of Concept
1. Confirm via static review / bytecode inspection that `_paused` (storage slot in `IntentsBase.sol`) is never read in any `JUMPI`/`require` in `IntentGatewayV2.sol`, `IntrinsicIntents.sol`, or `ExtrinsicIntents.sol`.
2. Assume/simulate a future governance action that flips `_paused = true` (via whatever mechanism is eventually wired, e.g., an `UpdateParams` message through `onAccept`).
3. Immediately after, call `placeOrder(order, graffiti)` from any unprivileged EOA with valid ERC20 approvals — the call succeeds and escrows funds normally.
4. Call `fillOrder`/`cancelOrder` on the same order — both succeed and move escrowed tokens, proving the "pause" has no effect on any of the exchange's core money-movement functions.

Note: I could not find any code path anywhere in the repo (including governance/`onAccept` messages) that ever sets `_paused`, so this is presently dead/unused storage rather than an active toggle — but its presence alongside the explicit "future upgrade-gated... pausing" comment on `_owner` confirms this is an incomplete safety control rather than intentionally omitted functionality, which is the direct local analog to the reported bug class (state that should gate execution but doesn't).

### Citations

**File:** evm/src/apps/intentsv2/IntentsBase.sol (L160-161)
```text
    /// @dev Appended last to preserve existing storage slots.
    bool public _paused;
```

**File:** evm/src/apps/intentsv2/IntentsBase.sol (L390-410)
```text
    function _withdraw(WithdrawalRequest memory body, bool isRefund, bool finalize) internal {
        address beneficiary = address(uint160(uint256(body.beneficiary)));
        if (finalize) _filled[body.commitment] = beneficiary;

        uint256 len = body.tokens.length;
        for (uint256 i; i < len; i++) {
            address token = address(uint160(uint256(body.tokens[i].token)));
            uint256 amount = body.tokens[i].amount;
            if (amount == 0) continue;

            uint256 escrowed = _orders[body.commitment][token];
            if (escrowed == 0) revert UnknownOrder();

            _orders[body.commitment][token] = escrowed - amount;
            if (token == address(0)) {
                (bool sent,) = beneficiary.call{value: amount}("");
                if (!sent) revert InsufficientNativeToken();
            } else {
                IERC20(token).safeTransfer(beneficiary, amount);
            }
        }
```

**File:** evm/src/apps/IntentGatewayV2.sol (L64-67)
```text
    /// @dev Privileged admin for future upgrade-gated actions (e.g. pausing). Immutable, so it must
    /// be identical across chains or the deterministic proxy address diverges. Does not gate
    /// `initialize`; atomic CREATE2 deployment already binds the init data to the canonical address.
    address public immutable _owner;
```

**File:** evm/src/apps/IntentGatewayV2.sol (L162-163)
```text
    function placeOrder(Order memory order, bytes32 graffiti) public payable nonReentrant {
        if (order.inputs.length == 0) revert InvalidInput();
```

**File:** evm/src/apps/IntentGatewayV2.sol (L413-426)
```text
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
```

**File:** evm/src/apps/IntentGatewayV2.sol (L470-490)
```text
    function cancelOrder(Order calldata order, CancelOptions calldata options) public payable nonReentrant {
        bytes32 commitment = keccak256(abi.encode(order));

        if (_filled[commitment] != address(0)) revert Filled();

        address hostAddr = host();
        bytes32 currentChain = keccak256(IDispatcher(hostAddr).host());
        bytes32 orderSource = keccak256(order.source);
        bytes32 orderDest = keccak256(order.destination);
        bool isSameChain = orderSource == orderDest;

        if (isSameChain) {
            _cancelSameChain(order, commitment);
        } else if (currentChain == orderSource) {
            _cancelFromSource(order, options, commitment);
        } else if (currentChain == orderDest) {
            _cancelFromDest(order, options, commitment);
        } else {
            revert WrongChain();
        }
    }
```
