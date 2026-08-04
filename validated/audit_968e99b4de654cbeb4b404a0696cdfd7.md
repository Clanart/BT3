Based on the evidence gathered, I found a concrete local analog to the `AsyncVault.setLimits()` unbounded-parameter bug in the Tron variant of the Intent Gateway.

### Title
Unbounded, unrecoverable `protocolFeeBps`/`surplusShareBps` in Tron `IntentGatewayV2.setParams` can trap 100% of every user's escrowed order inputs - (File: `evm/tron/contracts/apps/IntentGatewayV2.sol`)

### Summary
The Tron port of the Intent Gateway (`evm/tron/contracts/apps/IntentGatewayV2.sol`) exposes a one-shot `setParams()` initializer that stores `Params` (including `protocolFeeBps` and `surplusShareBps`) with **no bounds validation**, unlike the canonical EVM implementation (`evm/src/apps/IntentGatewayV2.sol`), which explicitly rejects `surplusShareBps > 10000` and `protocolFeeBps >= 10000` with `InvalidInput()` (proven by `evm/tests/foundry/IntentGatewayV2Test.sol` lines 3580-3608). This is the same broken-invariant class as the `AsyncVault` report: a configuration struct with no min/max guard that directly controls how much of a user's deposited value is retained versus returned.

### Finding Description
`evm/tron/contracts/apps/IntentGatewayV2.sol` lines 300-305:
```solidity
function setParams(Params memory p) public {
    if (msg.sender != _admin) revert Unauthorized();
    _admin = address(0);
    _params = p;
}
```
No check exists for `p.protocolFeeBps`, `p.surplusShareBps`, or `p.priceOracle` being a contract. Compare this to the sibling EVM contract's `initialize`/`setParams`, which is proven (via the foundry test suite) to revert on `surplusShareBps > 10000` and `protocolFeeBps >= 10000`.

`protocolFeeBps` is consumed unguarded in `placeOrder()` (lines 342-368 of the same file):
```solidity
uint256 protocolFee = (originalAmount * protocolFeeBps) / 10_000;
uint256 reducedAmount = originalAmount - protocolFee;
```
If `protocolFeeBps` is set to `10000` (100%), every user's escrowed input is reduced to `0` before the commitment is computed and the tokens are pulled into escrow — the user's entire deposit is retained by the gateway as "protocol dust" on every single `placeOrder()` call, with no way for the user to prevent or recover it.

Critically, `setParams` is **one-shot**: the same call that applies the (unvalidated) parameters also zeroes `_admin`. Unlike `AsyncVault.setLimits()`, which the owner can call again to fix a bad configuration, here the misconfiguration becomes **permanent and irreversible** the moment it is set, because the only authorized caller (`_admin`) is erased in the same transaction that removed the safety margin.

### Impact Explanation
This directly matches the required impact class: loss of user funds. Every order placed after such a misconfiguration silently forfeits some or all of the user's escrowed input to protocol dust, with no bound stopping the value from reaching 100%, and no governance path to reverse it once `_admin` is zeroed. This is a strictly worse outcome than the original `AsyncVault` report (which at least allowed the owner to correct `setLimits()` after the fact).

### Likelihood Explanation
The absence of bounds checking is a deterministic code-level omission, verifiable by diffing against the parallel, already-hardened `evm/src/apps/IntentGatewayV2.sol` implementation and its dedicated revert tests (`testRevert_SetParams_SurplusShareBpsTooHigh`, `testRevert_SetParams_ProtocolFeeBpsTooHigh`, `testRevert_SetParams_EOAPriceOracle`). No such tests or checks exist for the Tron variant, indicating the validation logic was not ported. I was not able to confirm within this investigation whether `_params.protocolFeeBps` is additionally constrained anywhere upstream (e.g., at deploy/migration script level) before `setParams` is called — this should be verified against Tron deployment tooling before treating this as fully confirmed in production.

### Recommendation
Port the exact validation used in `evm/src/apps/IntentGatewayV2.sol` into the Tron variant's `setParams`/`initialize`: reject `surplusShareBps > 10000`, reject `protocolFeeBps >= 10000`, and require `priceOracle` to be a contract address (or zero). Additionally, reconsider the one-shot `_admin = address(0)` pattern for a parameter surface this sensitive — an unrecoverable path for a fee parameter that directly determines user fund retention should either remain governance-updatable or be validated even more conservatively than a normal owner-settable parameter.

### Proof of Concept
1. Deploy `evm/tron/contracts/apps/IntentGatewayV2.sol` with `admin = deployer`.
2. `deployer` calls `setParams(Params({..., protocolFeeBps: 10000, surplusShareBps: 0, ...}))` — succeeds with no revert (contrast with `evm/tests/foundry/IntentGatewayV2Test.sol:3595-3608`, which shows the EVM sibling reverting on this exact value).
3. `_admin` is now `address(0)` — the misconfiguration can never be corrected via `setParams` again.
4. Any user calls `placeOrder()` with `inputs = [{token, amount: X}]`.
5. `protocolFee = X * 10000 / 10000 = X`; `reducedAmount = X - X = 0`.
6. The user's full `X` tokens are pulled into escrow (`evm/tron/contracts/apps/IntentGatewayV2.sol` predispatch/escrow logic after line 368), but the commitment records `reducedAmount = 0`, meaning the solver/settlement side has no incentive or record to return anything to the user — the entire deposit is retained as protocol dust with no recovery path. [1](#0-0) [2](#0-1) [3](#0-2)

### Citations

**File:** evm/tron/contracts/apps/IntentGatewayV2.sol (L296-305)
```text
    /**
     * @notice Sets the parameters for the IntentGateway.
     * @param p The parameters to be set, encapsulated in a Params struct.
     */
    function setParams(Params memory p) public {
        if (msg.sender != _admin) revert Unauthorized();

        _admin = address(0);
        _params = p;
    }
```

**File:** evm/tron/contracts/apps/IntentGatewayV2.sol (L342-368)
```text
        // Calculate reduced inputs (after protocol fees) for commitment and escrow
        uint256 inputsLen = order.inputs.length;
        // Use destination-specific protocol fee, fallback to source chain fee if zero
        bytes32 destinationHash = keccak256(order.destination);
        uint256 protocolFeeBps = _destinationProtocolFees[destinationHash];
        if (protocolFeeBps == 0) {
            protocolFeeBps = _params.protocolFeeBps;
        }
        TokenInfo[] memory reducedInputs;
        bytes32 commitment;

        if (protocolFeeBps > 0) {
            reducedInputs = new TokenInfo[](inputsLen);
            for (uint256 i; i < inputsLen;) {
                uint256 originalAmount = order.inputs[i].amount;
                uint256 protocolFee = (originalAmount * protocolFeeBps) / 10_000;
                uint256 reducedAmount = originalAmount - protocolFee;
                address token = address(uint160(uint256(order.inputs[i].token)));

                // Emit DustCollected for protocol fee if non-zero
                if (protocolFee > 0) emit DustCollected(token, protocolFee);

                reducedInputs[i] = TokenInfo({token: order.inputs[i].token, amount: reducedAmount});
                unchecked {
                    ++i;
                }
            }
```

**File:** evm/tests/foundry/IntentGatewayV2Test.sol (L3580-3608)
```text
    /// @notice setParams rejects surplusShareBps > 10000.
    function testRevert_SetParams_SurplusShareBpsTooHigh() public {
        IntentGatewayV2 gw = _deployGatewayProxy();
        Params memory p = Params({
            host: address(host),
            dispatcher: address(dispatcher),
            solverSelection: false,
            surplusShareBps: 10001,
            protocolFeeBps: 0,
            priceOracle: address(0)
        });
        vm.expectRevert(IntentsBase.InvalidInput.selector);
        gw.initialize(p, new bytes[](0));
    }

    /// @notice setParams rejects protocolFeeBps >= 10000.
    function testRevert_SetParams_ProtocolFeeBpsTooHigh() public {
        IntentGatewayV2 gw = _deployGatewayProxy();
        Params memory p = Params({
            host: address(host),
            dispatcher: address(dispatcher),
            solverSelection: false,
            surplusShareBps: 5000,
            protocolFeeBps: 10000,
            priceOracle: address(0)
        });
        vm.expectRevert(IntentsBase.InvalidInput.selector);
        gw.initialize(p, new bytes[](0));
    }
```
