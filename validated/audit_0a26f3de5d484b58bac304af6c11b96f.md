### Title
`IntentGatewayV2.instance()` fallback silently authenticates unregistered source chains for escrow withdrawal - ([File: evm/tron/contracts/apps/IntentGatewayV2.sol])

### Summary
The Standard bug reduces to: a value moved into a secondary/side accounting path (Gamma vault collateral) was silently exempted from the *same* enforcement (`getAcceptedTokens`/liquidation) that every other collateral asset goes through, so a fallback/omission in one code path broke an invariant relied on by another. The Hyperbridge analog is in the Tron build of `IntentGatewayV2`: the module-identity check that gates cross-chain escrow release (`RedeemEscrow`/`RefundEscrow`) uses an `instance()` helper that silently falls back to `address(this)` for any state machine that has never been explicitly registered, instead of reverting like the canonical EVM implementation does. This turns an "unknown chain" case into an implicitly-trusted one.

### Finding Description
In the canonical EVM intents implementation, resolving a remote gateway address is strict: [1](#0-0) 

`_instance()` reverts with `UnknownInstance` if `_instances[keccak256(stateMachineId)]` has never been set. `_authenticate()` relies on this revert to guarantee that a `RedeemEscrow`/`RefundEscrow` post request can only be accepted if it comes from a chain whose gateway address has been explicitly registered via governance's `NewDeployment` message: [2](#0-1) 

The Tron variant of the same contract implements the equivalent helper differently: [3](#0-2) 

```solidity
function instance(bytes calldata stateMachineId) public view returns (address) {
    address gateway = _instances[keccak256(stateMachineId)];
    return gateway == address(0) ? address(this) : gateway;
}

function authenticate(PostRequest calldata request) internal view {
    if (request.from.length != 20) revert InvalidInput();
    address module = address(bytes20(request.from));
    if (instance(request.source) != module) revert Unauthorized();
}
```

Here, `instance()` never reverts — for **any** `request.source` that has not yet been registered with `NewDeployment` (see `onAccept`'s handling at [4](#0-3) ), it returns `address(this)` (the destination contract's own address) instead of failing closed. `authenticate()` then only checks `instance(request.source) == module`, so the check is satisfied whenever `request.from == address(this)`, regardless of whether `request.source` is a chain the operator ever intended to trust.

This exactly mirrors the Gamma-vault flaw's structure: the *primary* enforcement path (`_instance()` reverting on unknown chains in the EVM build) exists and is documented, but the *secondary* implementation of the same logic (the Tron/EVM-compatible build) has a default branch that silently substitutes a "safe-looking" value (`address(this)`) instead of failing, so a case the author assumed was impossible ("no chain is ever un-registered") is instead treated as implicitly authorized — precisely as the Gamma vault's assets were implicitly treated as "not subject to liquidation" because they fell outside the `getAcceptedTokens()` loop.

### Impact Explanation
`RedeemEscrow`/`RefundEscrow` messages that pass `authenticate()` directly drive `withdraw()`, which transfers real escrowed user funds (native or ERC-20) to an attacker-chosen `beneficiary` address embedded in the `WithdrawalRequest`: [5](#0-4) 

Because this check is the *only* authorization gate on escrow release for cross-chain fills/cancels, any bypass here is a direct path to unauthorized fund transfer out of escrow — the same fund-safety class ("stealing or loss of funds", "unauthorized transaction or execution") called out in the bounty's impact gate. The exposure window is any state machine that Hyperbridge's consensus layer already recognizes (so the relayer/prover pipeline is entirely legitimate — no compromised relayer or forged proof is needed) but for which this specific `IntentGatewayV2` deployment has not yet had `NewDeployment` called (a normal, expected operational state during onboarding of new chains, or simply any chain the gateway operator never intends to support).

### Likelihood Explanation
The likelihood hinges on whether an attacker can get an ISMP `PostRequest` accepted by the destination host with `request.from` set to bytes equal to `address(this)` (the destination gateway's own address) from a `request.source` chain that has no `_instances` entry. Source-chain ISMP dispatchers typically force `request.from = msg.sender` of the dispatching contract, so exploitation requires the attacker to control a contract at that specific address on the unregistered source chain — feasible in principle via CREATE2 address-mirroring (a technique the codebase itself relies on for deterministic deployment across chains, as noted in `_blockNumber()`'s comments about "preserving deterministic CREATE2 deployment addresses"). This is a real, code-provable divergence between the two production contracts rather than a theoretical concern, but full exploitability depends on deployment-specific factors (CREATE2 factory/salt reuse) that could not be fully confirmed from this index alone.

### Recommendation
Make `instance()` in the Tron/EVM-compatible `IntentGatewayV2` revert on an unregistered `stateMachineId`, mirroring `_instance()` in `IntentsBase.sol` exactly, so `authenticate()` fails closed instead of falling back to `address(this)`. Add a regression test asserting that `onAccept` with `RedeemEscrow`/`RefundEscrow` reverts (rather than defaulting to self-authorization) when `request.source` has no `NewDeployment` entry.

### Proof of Concept
Conceptual PoC (cannot be fully executed without the Tron test harness, but derivable directly from the cited code):
1. Governance never calls `NewDeployment` for `stateMachineId = "EVM-999"` on the destination `IntentGatewayV2` (Tron build).
2. An attacker deploys a contract on `EVM-999` at an address `A` that they engineer (e.g., via a shared CREATE2 factory/salt) to equal `address(destinationGateway)`.
3. That contract dispatches a legitimate ISMP `PostRequest` to the destination gateway with body `RequestKind.RedeemEscrow` and a `WithdrawalRequest{commitment, tokens, beneficiary: attacker}` for a commitment that has real escrow (from a prior, unrelated order placed on the destination chain, or via any known/leaked commitment with nonzero escrow).
4. A normal (non-malicious) relayer delivers the message; the destination `IsmpHost` calls `onAccept`.
5. `authenticate()` computes `instance(request.source)` → `_instances[keccak256("EVM-999")] == address(0)` → returns `address(this)` → equals `module` (attacker's contract address `A == address(this)`), so authentication passes.
6. `withdraw()` executes, transferring escrowed tokens to the attacker-controlled `beneficiary`.

### Citations

**File:** evm/src/apps/intentsv2/IntentsBase.sol (L352-362)
```text
    /**
     * @dev Resolves the IntentGateway instance address for a given state machine.
     * Reverts with `UnknownInstance` if no remote deployment has been registered for that chain.
     * @param stateMachineId The raw state machine identifier bytes.
     * @return The gateway address for the given state machine.
     */
    function _instance(bytes calldata stateMachineId) internal view returns (address) {
        address gateway = _instances[keccak256(stateMachineId)];
        if (gateway == address(0)) revert UnknownInstance();
        return gateway;
    }
```

**File:** evm/src/apps/intentsv2/ExtrinsicIntents.sol (L56-67)
```text
    /**
     * @dev Authenticates an incoming cross-chain post request by verifying that the
     * sender module matches the registered gateway instance for the source chain.
     * Reverts with InvalidInput if the sender address is malformed, or Unauthorized
     * if the sender is not the expected gateway.
     * @param request The incoming post request to authenticate.
     */
    function _authenticate(PostRequest calldata request) internal view {
        if (request.from.length != 20) revert InvalidInput();
        address module = address(bytes20(request.from));
        if (_instance(request.source) != module) revert Unauthorized();
    }
```

**File:** evm/tron/contracts/apps/IntentGatewayV2.sol (L278-294)
```text
    /**
     * @dev Fetch the IntentGateway contract instance for a chain.
     */
    function instance(bytes calldata stateMachineId) public view returns (address) {
        address gateway = _instances[keccak256(stateMachineId)];
        return gateway == address(0) ? address(this) : gateway;
    }

    /**
     * @dev Checks that the request originates from a known instance of the IntentGateway.
     */
    function authenticate(PostRequest calldata request) internal view {
        if (request.from.length != 20) revert InvalidInput();
        address module = address(bytes20(request.from));
        // IntentGateway only accepts incoming assets from itself or known instances
        if (instance(request.source) != module) revert Unauthorized();
    }
```

**File:** evm/tron/contracts/apps/IntentGatewayV2.sol (L628-634)
```text
        // only hyperbridge is permitted to perfom these actions
        if (keccak256(incoming.request.source) != keccak256(IDispatcher(host()).hyperbridge())) revert Unauthorized();
        if (kind == RequestKind.NewDeployment) {
            NewDeployment memory body = abi.decode(incoming.request.body[1:], (NewDeployment));
            _instances[keccak256(body.stateMachineId)] = body.gateway;

            emit NewDeploymentAdded({stateMachineId: body.stateMachineId, gateway: body.gateway});
```

**File:** evm/tron/contracts/apps/IntentGatewayV2.sol (L682-721)
```text
    function withdraw(WithdrawalRequest memory body, bool isRefund) internal {
        address beneficiary = address(uint160(uint256(body.beneficiary)));
        _filled[body.commitment] = beneficiary;

        // redeem escrowed tokens
        uint256 len = body.tokens.length;
        for (uint256 i; i < len;) {
            address token = address(uint160(uint256(body.tokens[i].token)));
            uint256 amount = body.tokens[i].amount;
            if (_orders[body.commitment][token] == 0) revert UnknownOrder();

            if (token == address(0)) {
                (bool sent,) = beneficiary.call{value: amount}("");
                if (!sent) revert InsufficientNativeToken();
            } else {
                (bool success,) = token.call(abi.encodeWithSelector(IERC20.transfer.selector, beneficiary, amount));
                if (!success) revert TransferFailed();
            }

            _orders[body.commitment][token] -= amount;
            unchecked {
                ++i;
            }
        }

        // redeem tx fees
        uint256 fees = _orders[body.commitment][TRANSACTION_FEES];
        if (fees > 0) {
            address feeToken = IDispatcher(host()).feeToken();
            (bool success,) = feeToken.call(abi.encodeWithSelector(IERC20.transfer.selector, beneficiary, fees));
            if (!success) revert TransferFailed();
            delete _orders[body.commitment][TRANSACTION_FEES];
        }

        if (isRefund) {
            emit EscrowRefunded({commitment: body.commitment});
        } else {
            emit EscrowReleased({commitment: body.commitment});
        }
    }
```
