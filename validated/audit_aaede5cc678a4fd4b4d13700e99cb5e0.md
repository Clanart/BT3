## Title
Unregistered state machines default to `address(this)` in `IntentGatewayV2.instance()`, allowing forged cross-chain messages to authenticate as the gateway itself - (File: `evm/tron/contracts/apps/IntentGatewayV2.sol`)

### Summary
This mirrors the Tigris "0x0 default referral" bug class: a mapping lookup miss (unset value) is silently coerced into a *meaningful, trusted identity* instead of being rejected. In Tigris, `_referred[user] == 0x0` (unset) collided with an attacker-claimable `_referral[0x0]`. In `IntentGatewayV2.sol` (Tron variant of the Intents gateway), `_instances[keccak256(stateMachineId)] == address(0)` (unregistered chain) is remapped to `address(this)` — i.e., "no registration" is treated as "trust myself" — and this value is used directly as the required sender identity in `authenticate()`.

### Finding Description
`_instances` maps `keccak256(stateMachineId) => gateway address`, populated only via governance-driven `NewDeployment` requests [1](#0-0) .

The lookup helper falls back to `address(this)` instead of reverting when a state machine has never been registered:
```solidity
function instance(bytes calldata stateMachineId) public view returns (address) {
    address gateway = _instances[keccak256(stateMachineId)];
    return gateway == address(0) ? address(this) : gateway;
}
``` [2](#0-1) 

This value feeds directly into the inbound authentication check:
```solidity
function authenticate(PostRequest calldata request) internal view {
    if (request.from.length != 20) revert InvalidInput();
    address module = address(bytes20(request.from));
    if (instance(request.source) != module) revert Unauthorized();
}
``` [3](#0-2) 

Contrast this with the mainline EVM `IntentsBase.sol`, which correctly reverts with `UnknownInstance()` on an unregistered chain instead of returning a trusted fallback:
```solidity
function _instance(bytes calldata stateMachineId) internal view returns (address) {
    address gateway = _instances[keccak256(stateMachineId)];
    if (gateway == address(0)) revert UnknownInstance();
    return gateway;
}
``` [4](#0-3) 

Because Hyperbridge deployments are explicitly designed to share the same address across chains via deterministic `CREATE2` (a pattern documented elsewhere in the repo — e.g. "The adapter must have the same address on all chains so it can verify cross-chain message sources" [5](#0-4) ), an attacker who deploys *any* contract at the gateway's own predicted `address(this)` on an EVM-compatible state machine that Hyperbridge's ISMP host recognizes as valid consensus/state, but for which governance never registered a `Deployment` (i.e., `_instances[keccak256(thatChain)]` is still zero), automatically satisfies `instance(request.source) == module` for every message whose `request.from == bytes20(address(this))`. No admin, relayer, or prover collusion is required — the attacker only needs to control an ordinary contract on an already-supported chain and dispatch a genuine (but attacker-authored) ISMP `PostRequest` from it.

### Impact Explanation
Any request kind processed via `onAccept` — including `RedeemEscrow` (release escrowed tokens to a solver) or `RefundEscrow` — is guarded only by `authenticate()`. Once authentication is defeated for any not-yet-registered chain, the attacker can submit a forged `RedeemEscrow`/withdrawal body referencing arbitrary commitments and beneficiaries, causing the gateway to release escrowed order funds to an attacker-chosen address without a legitimate underlying fill or order flow. This is a direct fund-loss / unauthorized-execution path consistent with the bounty's "stealing or loss of funds" and "false proof/state acceptance" categories — the gateway falsely accepts an unregistered/foreign message as coming from a trusted sibling instance.

### Likelihood Explanation
Likelihood is high for any Hyperbridge deployment that (a) uses this Tron `IntentGatewayV2.sol` contract or any port of its `instance()` fallback, and (b) has not yet registered every EVM-compatible state machine ISMP supports. The attack requires no privileged role, no relayer/prover compromise, and no governance action — only deploying a plain contract at a computable address on a supported-but-unregistered chain and issuing one crafted PostRequest. This is exactly analogous to the confirmed Tigris finding: the "default/unset" sentinel is itself a valid, attacker-reachable identity.

### Recommendation
Change `instance()` in `evm/tron/contracts/apps/IntentGatewayV2.sol` to revert (as `IntentsBase.sol` already does with `UnknownInstance()`) when `_instances[keccak256(stateMachineId)] == address(0)`, rather than falling back to `address(this)`. Any code path that relies on the "same-chain" self-referential meaning of `instance()` returning `address(this)` should instead check `keccak256(stateMachineId) == keccak256(currentChain)` explicitly rather than overloading the unregistered-chain sentinel.

### Proof of Concept
1. Attacker identifies an EVM-compatible `stateMachineId` that Hyperbridge's ISMP host accepts consensus/state proofs for, but for which the `IntentGatewayV2` deployment on Tron has never received a `NewDeployment` registration (`_instances[keccak256(id)] == 0`).
2. Attacker deploys any contract (even a minimal dispatcher stub) via `CREATE2` at the same address as the target `IntentGatewayV2` (`address(this)`), using the same salt/deployer pattern documented for Hyperbridge adapters [6](#0-5) , on that unregistered chain.
3. From that contract, attacker dispatches a real ISMP `PostRequest` with `from = abi.encodePacked(address(this))`, `to = <target IntentGatewayV2 address>`, `dest = <target chain>`, and `body = bytes1(RequestKind.RedeemEscrow) || abi.encode(WithdrawalRequest{...})` referencing an existing or attacker-influenced commitment/beneficiary.
4. Once relayed and proven (a legitimate step, not requiring a malicious relayer), `onAccept` calls `authenticate(request)`, which calls `instance(request.source)` → returns `address(this)` (default fallback) → equals `module` (attacker's contract address) → `Unauthorized()` is never triggered.
5. The forged request is processed as if it came from a genuine sibling `IntentGatewayV2` instance, releasing escrowed funds per the attacker-supplied `WithdrawalRequest`. [7](#0-6)

### Citations

**File:** evm/tron/contracts/apps/IntentGatewayV2.sol (L125-129)
```text
    /**
     * @dev Mapping to store instances of contracts.
     * The key the keccak(stateMachineId) and the value is the address of a known contract instance.
     */
    mapping(bytes32 => address) public _instances;
```

**File:** evm/tron/contracts/apps/IntentGatewayV2.sol (L281-294)
```text
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

**File:** evm/src/apps/intentsv2/IntentsBase.sol (L358-362)
```text
    function _instance(bytes calldata stateMachineId) internal view returns (address) {
        address gateway = _instances[keccak256(stateMachineId)];
        if (gateway == address(0)) revert UnknownInstance();
        return gateway;
    }
```

**File:** docs/content/developers/evm/lz-endpoint.mdx (L61-68)
```text
The adapter must have the same address on all chains so it can verify cross-chain message sources.

```solidity lineNumbers
import {HyperbridgeLzEndpoint} from "@hyperbridge/lz-endpoint/HyperbridgeLzEndpoint.sol";

bytes32 salt = keccak256("hyperbridge-lz-v1");
HyperbridgeLzEndpoint endpoint = new HyperbridgeLzEndpoint{salt: salt}(admin);
```
```
