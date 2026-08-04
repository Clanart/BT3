### Title
`instance()` fallback to `address(this)` on unregistered state machines allows spoofed `RedeemEscrow`/`RefundEscrow` cross-chain messages to drain escrowed funds - ([File: evm/tron/contracts/apps/IntentGatewayV2.sol])

### Summary
The Tron variant of `IntentGatewayV2` implements `instance()` differently from the canonical EVM implementation. Where the canonical EVM `IntentsBase._instance()` reverts with `UnknownInstance` when no peer deployment is registered for a state machine [1](#0-0) , the Tron contract's `instance()` silently falls back to `address(this)`: [2](#0-1) 

This fallback is used directly in message authentication for settlement (`RedeemEscrow`) and refund (`RefundEscrow`) flows.

### Finding Description
`authenticate()` is the sole gate protecting `withdraw()` — the function that releases escrowed user funds to a beneficiary: [3](#0-2) 

`onAccept` routes any incoming `RedeemEscrow`/`RefundEscrow` request straight through `authenticate()` into `withdraw()`, which transfers escrowed tokens to the beneficiary named in the request body: [4](#0-3) [5](#0-4) 

`authenticate()` checks `instance(request.source) == module`, where `module` is the sending contract address on the *source* chain (`request.from`) as reported by the ISMP host. Because `instance()` returns `address(this)` for any `stateMachineId` that has no entry in `_instances` (instead of reverting), **any state machine that Hyperbridge's consensus/host layer trusts but for which this specific IntentGateway deployment has not yet had a peer registered via `NewDeployment`** will authenticate a message whose `from` field equals `address(this)` — i.e., the gateway's own address. Since gateway proxies are deployed deterministically via CREATE2 at the *same address across chains* (per the design comments in the canonical `IntentGatewayV2.initialize` docstring: "identical across chains under deterministic CREATE2, so no peer address is carried in the proxy's init data" [6](#0-5) ), an attacker can permissionlessly deploy any bytecode to that exact predicted address on a chain the gateway hasn't yet peered with, then dispatch a forged `RedeemEscrow`/`RefundEscrow` ISMP PostRequest from that chain with `from = address(this)`. The Tron gateway's `authenticate()` treats this as a legitimate sibling instance and executes `withdraw()`, transferring real escrowed tokens to an attacker-chosen beneficiary.

This directly mirrors the reported bug class: the AAVE report flagged an address that silently resolves to the wrong/non-existent target (Lending Pool Core vs Lending Pool) breaking an invariant; here the address resolution (`instance()`) silently substitutes an untrusted default (`address(this)`) instead of failing closed, breaking the "only a known peer gateway may authorize escrow release" invariant.

### Impact Explanation
This is a false-proof/false-authorization acceptance leading directly to theft of bridged/escrowed user funds — matching the bounty's core impact categories (unauthorized execution, false state acceptance, fund loss to the wrong beneficiary). No relayer, prover, or admin compromise is required: the attacker only needs a state machine that the host already trusts for consensus (any chain Hyperbridge already tracks) but on which this specific gateway instance is not yet registered, and permissionless CREATE2 deployment rights on that chain to occupy the deterministic gateway address before governance completes onboarding.

### Likelihood Explanation
Likelihood is tied to the operational rollout process: new chains are commonly added to the ISMP host/consensus layer before every application-level peer (`_instances` entry via `NewDeployment`) is registered for every gateway deployment. During that window — which is a normal, expected state during phased chain rollout, not a testnet-only or malicious-relayer condition — this contract silently accepts spoofed settlement messages instead of reverting, unlike the canonical `IntentsBase._instance()` which fails closed.

### Recommendation
Change `instance()` in `evm/tron/contracts/apps/IntentGatewayV2.sol` to match the canonical `IntentsBase._instance()` behavior: revert with an `UnknownInstance` error when `_instances[keccak256(stateMachineId)] == address(0)`, rather than falling back to `address(this)`. Audit all other Tron-specific deviations from the canonical `evm/src/apps/intentsv2/*` contracts for similar silent-fallback patterns.

### Proof of Concept
1. Hyperbridge governance adds a new EVM-compatible state machine `S` to the ISMP host's trusted consensus clients (a normal onboarding step), but has not yet called `NewDeployment` to register `S`'s IntentGateway peer address on the target Tron-variant gateway `G` deployed at address `A` (deterministic CREATE2 address, identical across all EVM chains).
2. On chain `S`, before the real gateway is deployed there, the attacker deploys arbitrary bytecode to address `A` via the same CREATE2 factory/salt scheme (permissionless on an as-yet-unclaimed address).
3. From that attacker-controlled contract at address `A` on chain `S`, the attacker dispatches an ISMP PostRequest to `G` with `from = A`, `body = [RequestKind.RedeemEscrow, WithdrawalRequest{commitment: <any pending order commitment on G>, tokens: <escrowed tokens>, beneficiary: attacker}]`.
4. `G.onAccept()` calls `authenticate(request)`; `instance(S)` returns `address(this) == A` (fallback, since `_instances[keccak256(S)] == address(0)`); `module == A` matches, so authentication passes.
5. `withdraw()` executes, transferring the escrowed tokens for that commitment to the attacker's beneficiary address, stealing funds that belonged to the legitimate order.

### Citations

**File:** evm/src/apps/intentsv2/IntentsBase.sol (L358-362)
```text
    function _instance(bytes calldata stateMachineId) internal view returns (address) {
        address gateway = _instances[keccak256(stateMachineId)];
        if (gateway == address(0)) revert UnknownInstance();
        return gateway;
    }
```

**File:** evm/tron/contracts/apps/IntentGatewayV2.sol (L278-284)
```text
    /**
     * @dev Fetch the IntentGateway contract instance for a chain.
     */
    function instance(bytes calldata stateMachineId) public view returns (address) {
        address gateway = _instances[keccak256(stateMachineId)];
        return gateway == address(0) ? address(this) : gateway;
    }
```

**File:** evm/tron/contracts/apps/IntentGatewayV2.sol (L286-294)
```text
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

**File:** evm/tron/contracts/apps/IntentGatewayV2.sol (L620-626)
```text
    function onAccept(IncomingPostRequest calldata incoming) external override onlyHost {
        RequestKind kind = RequestKind(uint8(incoming.request.body[0]));
        if (kind == RequestKind.RedeemEscrow || kind == RequestKind.RefundEscrow) {
            authenticate(incoming.request);
            WithdrawalRequest memory body = abi.decode(incoming.request.body[1:], (WithdrawalRequest));
            return withdraw(body, kind == RequestKind.RefundEscrow);
        }
```

**File:** evm/tron/contracts/apps/IntentGatewayV2.sol (L682-720)
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
```

**File:** evm/src/apps/IntentGatewayV2.sol (L94-102)
```text
    /**
     * @dev One-time init (the `initializer` modifier caps it to a single call). Registers the
     * initial cross-chain peers, each bound to `address(this)`; `_instance` reverts with
     * `UnknownInstance` for any chain not registered here or later via `onAccept` governance.
     *
     * @param p The initial gateway configuration parameters.
     * @param peerChains State-machine ids of the cross-chain peers to register. Each is bound to
     * this gateway's own address, identical across chains under deterministic CREATE2, so no peer
     * address is carried in the proxy's init data.
```
