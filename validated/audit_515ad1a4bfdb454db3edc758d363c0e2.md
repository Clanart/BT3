### Title
`IntentGatewayV2.instance()` fallback to `address(this)` on unregistered chains defeats source authentication, enabling unauthorized escrow drain — ([File: evm/tron/contracts/apps/IntentGatewayV2.sol])

### Summary
`IntentGatewayV2` on the Tron variant resolves the trusted peer/gateway for an incoming chain via `instance(stateMachineId)`. Unlike the canonical EVM `IntentsBase._instance()` (which reverts with `UnknownInstance` when no peer is registered for a chain), the Tron `instance()` silently falls back to `address(this)` when the chain has no registered deployment. Because `authenticate()` compares `request.from` against this fallback value, a message coming from *any* chain not yet explicitly registered as a peer — but still trusted by the local `IsmpHost`'s consensus clients — passes authentication as long as the attacker sets `request.from = address(this)` (the local gateway's own address bytes). This lets an unprivileged actor forge `RedeemEscrow`/`RefundEscrow` requests that drain escrowed order funds to an arbitrary beneficiary.

### Finding Description
Compare the two implementations:

EVM canonical (safe): [1](#0-0) 

Tron variant (vulnerable): [2](#0-1) 

`instance()` returns `address(this)` instead of reverting for any `stateMachineId` that has never been registered via `NewDeployment` governance:
```
function instance(bytes calldata stateMachineId) public view returns (address) {
    address gateway = _instances[keccak256(stateMachineId)];
    return gateway == address(0) ? address(this) : gateway;
}
```

`authenticate()` uses this value as the sole check that an incoming `PostRequest` originated from a legitimate, registered `IntentGatewayV2` peer:
```
function authenticate(PostRequest calldata request) internal view {
    if (request.from.length != 20) revert InvalidInput();
    address module = address(bytes20(request.from));
    if (instance(request.source) != module) revert Unauthorized();
}
``` [3](#0-2) 

`onAccept()` invokes `authenticate()` before dispatching `withdraw()` for `RedeemEscrow`/`RefundEscrow`, which pays out escrowed order tokens and fee-token balances to `body.beneficiary`: [4](#0-3) [5](#0-4) 

The Host's `dispatchIncoming` delivers any correctly-proven ISMP `PostRequest` to the module identified by `request.to`, based only on the destination module id, not on any specific whitelist of source chains for that module: [6](#0-5) . The ISMP core handler routes messages purely by `router.module_for_id(request.to)` plus generic replay/timeout/proof checks; it does not know or enforce which source chains a given application module considers "registered": [7](#0-6) .

**Broken invariant:** `IntentGatewayV2` on Tron assumes "no explicit peer registered for chain X" implies "reject", but its `instance()` implementation instead treats it as "trust `address(this)`". Since `request.from` is attacker-controlled data inside a message dispatched from any state machine the host already trusts (any chain with a valid consensus client — this does not require a malicious relayer, prover, or governance actor, only a normal contract deployed by anyone on an already-bridged chain), an attacker deploys a trivial contract on any *unregistered-with-this-gateway* but bridge-connected chain, dispatches an ISMP POST with `to` = this Tron gateway's module id and `from` = `abi.encodePacked(address(thisGateway))`, body = `RedeemEscrow`/`RefundEscrow` payload naming themselves as `beneficiary` and any live `commitment`/token amounts. `authenticate()` computes `instance(request.source)`, finds no entry, returns `address(this)`, which equals the forged `module` (`bytes20(request.from)`), and passes.

### Impact Explanation
This is a direct, unauthorized fund-drain path on escrowed intent-gateway order funds and accumulated protocol/relayer fees, reachable by any unprivileged actor who can dispatch a message from a chain the host already trusts (a normal, permissionless bridge action — not requiring a malicious relayer, prover, or governance key). It matches the bounty's "stealing or loss of funds," "unauthorized transaction or execution," and "wrong beneficiary or amount" categories for the bridge custody / intent settlement class.

### Likelihood Explanation
Likelihood is high wherever this Tron `IntentGatewayV2` is deployed with at least one order escrow outstanding and at least one bridge-connected chain that has not (yet) been explicitly registered as a peer for that gateway (a very common, even default, initial state — peers are added incrementally via `NewDeployment` governance messages over time). The attacker only needs to control a contract on any such chain and pay for a normal ISMP dispatch; no privileged role or compromised infrastructure is required.

### Recommendation
Make `instance()` fail closed like the canonical `IntentsBase._instance()`: revert (e.g., with `UnknownInstance`) when `_instances[keccak256(stateMachineId)] == address(0)`, instead of defaulting to `address(this)`. Audit all other Tron-specific contract variants for the same fallback pattern, and add a regression test asserting that `authenticate()` rejects requests from any state machine that has not been explicitly registered via `NewDeployment`.

### Proof of Concept
1. Deploy `IntentGatewayV2` (Tron variant) with `_instances` containing no entry for state machine `"CHAIN_X"` (default/unconfigured state).
2. A user places an order on some source chain and it becomes escrowed with a live `commitment` in `_orders`.
3. Attacker deploys any contract on `CHAIN_X` (a state machine the local `IsmpHost` already has a working consensus client for) and dispatches an ISMP `PostRequest` with:
   - `source = "CHAIN_X"`
   - `to` = the Tron gateway's registered module id
   - `from = abi.encodePacked(address(tronGatewayContract))` (the Tron gateway's own address)
   - `body = abi.encodePacked(uint8(RequestKind.RedeemEscrow), abi.encode(WithdrawalRequest({commitment: <live commitment>, tokens: [...], beneficiary: bytes32(uint256(uint160(attacker)))})))`
4. Once the relayer submits the proof and the host's handler verifies membership/consensus (both legitimate, since `CHAIN_X` is a genuinely supported chain), `EvmHost.dispatchIncoming` calls `IntentGatewayV2.onAccept`.
5. `onAccept` → `authenticate(request)` computes `instance("CHAIN_X")`, which returns `address(this)` (fallback, since unregistered) — equal to `bytes20(request.from)` set by the attacker — authentication passes.
6. `withdraw()` executes, transferring the escrowed tokens and fees to the attacker's `beneficiary` address, draining the order.

### Citations

**File:** evm/src/apps/intentsv2/IntentsBase.sol (L358-362)
```text
    function _instance(bytes calldata stateMachineId) internal view returns (address) {
        address gateway = _instances[keccak256(stateMachineId)];
        if (gateway == address(0)) revert UnknownInstance();
        return gateway;
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

**File:** evm/src/core/EvmHost.sol (L794-818)
```text
    function dispatchIncoming(PostRequest memory request, address relayer) external restrict(_hostParams.handler) {
        address destination = _bytesToAddress(request.to);
        uint256 size;
        assembly {
            size := extcodesize(destination)
        }
        if (size == 0) {
            // instead of reverting the entire batch, early return here.
            return;
        }

        // replay protection
        bytes32 commitment = request.hash();
        _requestReceipts[commitment] = relayer;

        (bool success,) = address(destination)
            .call(abi.encodeWithSelector(IApp.onAccept.selector, IncomingPostRequest(request, relayer)));

        if (!success) {
            // so that it can be retried
            delete _requestReceipts[commitment];
            return;
        }
        emit PostRequestHandled({commitment: commitment, relayer: relayer});
    }
```

**File:** modules/ismp/core/src/handlers/request.rs (L55-93)
```rust
	for req in msg.requests.iter() {
		let req = Request::Post(req.clone());
		// If a receipt exists for any request then it's a duplicate and it is not dispatched
		if host.request_receipt(&req).is_some() {
			Err(Error::DuplicateRequest { meta: req.clone().into() })?
		}

		// can't dispatch timed out requests
		if req.timed_out(host.timestamp()) {
			Err(Error::RequestTimeout { meta: req.clone().into() })?
		}

		// either the host is a router and can accept requests on behalf of any chain
		// or the request must be intended for this chain
		if req.dest_chain() != host.host_state_machine() && !host.is_router() {
			Err(Error::InvalidRequestDestination { meta: req.clone().into() })?
		}

		let source_chain = req.source_chain();

		// in order to allow proxies, the host must configure the given state machine
		// as it's proxy and must not have a state machine client for the source chain
		let allow_proxy = host.is_allowed_proxy(&msg.proof.height.id.state_id) &&
			check_state_machine_client(source_chain);

		// check if the request is allowed to be proxied
		if source_chain != msg.proof.height.id.state_id && !allow_proxy {
			Err(Error::RequestProxyProhibited { meta: req.clone().into() })?
		}
	}

	// Verify membership proof
	let state = host.state_machine_commitment(msg.proof.height)?;
	let commitments = msg
		.requests
		.iter()
		.map(|post| hash_request::<H>(&Request::Post(post.clone())))
		.collect();
	state_machine.verify_membership(host, commitments, state, &msg.proof)?;
```
