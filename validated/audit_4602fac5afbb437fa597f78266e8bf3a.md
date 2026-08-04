## Title
Stale LayerZero recovery `delegate` permission survives OApp ownership transfer, letting a removed delegate destroy in-flight cross-chain messages - (`sdk/packages/lz-endpoint/contracts/HyperbridgeLzEndpoint.sol`)

## Summary

The external report's core broken invariant is: a permission mapping is keyed by a *persistent identity* (the safe handler) rather than the *current controller* (the owner), so when control is transferred, stale permissions granted by the previous controller remain active and are never revoked. The same pattern exists in `HyperbridgeLzEndpoint.sol`'s recovery-delegate mechanism: `_delegates[oapp]` is keyed by the immutable OApp contract address, but the actual authority to set/rely on that delegate flows from the OApp's mutable `Ownable` ownership. Nothing clears `_delegates[oapp]` when the OApp's ownership changes hands, so a delegate appointed by a previous owner keeps standing authority to permanently destroy that OApp's stuck inbound messages after the OApp is sold/transferred to a new owner.

## Finding Description

`HyperbridgeLzEndpoint` stores a single mapping from OApp address to an authorized recovery delegate: [1](#0-0) 

`setDelegate` lets the caller (the OApp contract, acting on behalf of whoever currently controls it via `onlyOwner` on the OApp side) register a delegate: [2](#0-1) 

That delegate is then permanently trusted by `onlyOAppOrDelegate` for the destructive recovery primitives `clear`, `skip`, `nilify`, and `burn`: [3](#0-2) [4](#0-3) 

The adapter's own docs confirm the OApp identity (and hence the `_delegates[oapp]` key) is meant to persist across ownership/proxy changes — "token balances, peer configurations, and **ownership** are preserved in proxy storage": [5](#0-4) 

This is the exact bug class from the report: `handlerCan` was keyed only by the fixed `safeHandler` identity and never revoked when the safe's `owner` field changed via `transferSAFEOwnership`, so the old owner's approved addresses kept authority. Here, `_delegates[oapp]` is keyed only by the fixed OApp address and is never revoked when the OApp's `Ownable` owner changes — there is no hook analogous to clearing `safeCan` on transfer. Any OApp that is sold, has its admin key rotated, or has ownership transferred to a new party (e.g. a DAO, new team, or new deployment owner) leaves the endpoint trusting whichever delegate the *previous* owner last configured.

## Impact Explanation

`clear`, `skip`, `nilify`, and `burn` are irreversible, permission-gated destructive actions over an OApp's stuck inbound payloads (messages whose `lzReceive` reverted and are held pending retry): [6](#0-5) 

Because delivery of value (e.g. OFT token minting) happens inside the receiving OApp's `lzReceive` call and only succeeds once the payload is retried successfully, a payload that is `nilify`'d then `burn`'d is permanently and unrecoverably destroyed — the underlying transferred value is never delivered and can never be retried again. A stale delegate retained from a prior owner can therefore unilaterally and permanently strand or destroy in-flight cross-chain transfers belonging to the new owner's OApp, without the new owner's knowledge or consent — matching the bounty's "unauthorized execution / transaction manipulation / fund loss" criteria. This is not a relayer/prover/node trust assumption: the delegate is an ordinary EOA/contract address that was legitimately authorized once, exactly like the previous safe owner in the source report, and the vulnerability is that authorization silently outlives the relationship that justified it.

## Likelihood Explanation

Any project using this adapter that ever rotates an OApp's admin/owner (a routine operational event — team changes, governance handoff, delegate key rotation, contract sale) will trigger this automatically; no attacker cooperation, malformed proof, or privileged Hyperbridge actor is required. The only actor needed is the previously-appointed delegate itself, who needs no special access beyond the authority they were already granted — they simply retain it longer than intended and call the four public recovery functions directly.

## Recommendation

Bind the `_delegates` entry to the OApp's current effective owner rather than assuming it survives independently of ownership, mirroring the `safeCan`-style fix recommended in the source report: either (a) require the OApp to re-affirm its delegate on every ownership change by having `setDelegate` accept and store an owner-scoped key (`_delegates[oapp][owner]`) that the endpoint checks against a live `Ownable.owner()` read on the OApp, or (b) expose a `clearDelegate(address oapp)` callable by the OApp itself and require OApp upgrade/ownership-transfer flows to always reset the delegate to `address(0)` before completing the transfer, so no delegate carries over implicitly.

## Proof of Concept

1. OApp `X` (an `Ownable` OFT/OApp) is deployed with owner `Alice`. Alice calls her OApp's `setDelegate(Bob)`, which internally calls `HyperbridgeLzEndpoint.setDelegate(Bob)` as `msg.sender == X`, setting `_delegates[X] = Bob`.
2. Alice sells/transfers the OApp: `X.transferOwnership(Carol)`. Nothing in `HyperbridgeLzEndpoint` observes or reacts to this — `_delegates[X]` still equals `Bob`.
3. A cross-chain transfer to `X` arrives; `X.lzReceive` reverts (e.g., paused or a transient failure), so the payload is stored: `_inboundPayloadHashes[X][srcEid][sender][nonce] = payloadHash` (see lines 391-395).
4. Bob, no longer trusted by Carol and with no relationship to the current owner, calls `nilify(X, srcEid, sender, nonce, payloadHash)` then `burn(X, srcEid, sender, nonce, NIL_PAYLOAD_HASH)` — both succeed because `onlyOAppOrDelegate(X)` still authorizes `Bob` via the stale `_delegates[X]` entry.
5. The payload is permanently deleted; the bridged value in that message is never delivered to `X` and can never be retried, with Carol (the current owner) having had no way to prevent it since she was never made aware `Bob` still held delegate rights on the endpoint.

### Citations

**File:** sdk/packages/lz-endpoint/contracts/HyperbridgeLzEndpoint.sol (L139-146)
```text
    /// @notice Per-OApp recovery delegate authorized to manage stuck inbound payloads
    mapping(address => address) internal _delegates;

    /// @notice Restricts inbound-payload recovery to the target OApp or its configured delegate
    modifier onlyOAppOrDelegate(address oapp) {
        if (msg.sender != oapp && msg.sender != _delegates[oapp]) revert UnauthorizedRecovery();
        _;
    }
```

**File:** sdk/packages/lz-endpoint/contracts/HyperbridgeLzEndpoint.sol (L416-433)
```text
    function retryPayload(
        address receiver,
        Origin calldata origin,
        bytes32 guid,
        bytes calldata message
    ) external payable {
        bytes32 stored = _inboundPayloadHashes[receiver][origin.srcEid][origin.sender][origin.nonce];
        if (stored == bytes32(0) || stored == NIL_PAYLOAD_HASH || stored != keccak256(abi.encode(guid, message))) {
            revert InvalidPayloadHash();
        }

        // Clear first; if the retry reverts, this deletion rolls back with the rest of the tx and
        // the payload remains recoverable.
        delete _inboundPayloadHashes[receiver][origin.srcEid][origin.sender][origin.nonce];

        ILayerZeroReceiver(receiver).lzReceive{value: msg.value}(origin, guid, message, msg.sender, "");
        emit InboundPayloadResolved(receiver, origin.srcEid, origin.sender, origin.nonce);
    }
```

**File:** sdk/packages/lz-endpoint/contracts/HyperbridgeLzEndpoint.sol (L469-483)
```text
    /// @inheritdoc ILayerZeroEndpointV2
    /// @notice Discards a stored failed payload without executing it. Callable by the OApp or its delegate.
    function clear(
        address _oapp,
        Origin calldata _origin,
        bytes32 _guid,
        bytes calldata _message
    ) external override onlyOAppOrDelegate(_oapp) {
        bytes32 stored = _inboundPayloadHashes[_oapp][_origin.srcEid][_origin.sender][_origin.nonce];
        if (stored == bytes32(0) || stored == NIL_PAYLOAD_HASH || stored != keccak256(abi.encode(_guid, _message))) {
            revert InvalidPayloadHash();
        }
        delete _inboundPayloadHashes[_oapp][_origin.srcEid][_origin.sender][_origin.nonce];
        emit InboundPayloadResolved(_oapp, _origin.srcEid, _origin.sender, _origin.nonce);
    }
```

**File:** sdk/packages/lz-endpoint/contracts/HyperbridgeLzEndpoint.sol (L498-503)
```text
    /// @inheritdoc ILayerZeroEndpointV2
    /// @notice Authorizes a delegate to perform inbound-payload recovery on the caller OApp's behalf.
    function setDelegate(address _delegate) external override {
        _delegates[msg.sender] = _delegate;
        emit RecoveryDelegateSet(msg.sender, _delegate);
    }
```

**File:** sdk/packages/lz-endpoint/contracts/HyperbridgeLzEndpoint.sol (L512-553)
```text
    /// discarding any stored payload at that slot. Callable by the OApp or its delegate.
    function skip(
        address _oapp,
        uint32 _srcEid,
        bytes32 _sender,
        uint64 _nonce
    ) external override onlyOAppOrDelegate(_oapp) {
        uint64 expected = _inboundNonce[_oapp][_srcEid][_sender] + 1;
        if (_nonce != expected) revert InvalidNonce(expected, _nonce);
        _inboundNonce[_oapp][_srcEid][_sender] = _nonce;
        delete _inboundPayloadHashes[_oapp][_srcEid][_sender][_nonce];
        emit InboundNonceSkippedBy(_oapp, _srcEid, _sender, _nonce);
    }

    /// @notice Marks a stored payload as nil (deliberately un-executable) prior to burning it.
    function nilify(
        address _oapp,
        uint32 _srcEid,
        bytes32 _sender,
        uint64 _nonce,
        bytes32 _payloadHash
    ) external override onlyOAppOrDelegate(_oapp) {
        bytes32 stored = _inboundPayloadHashes[_oapp][_srcEid][_sender][_nonce];
        if (stored == bytes32(0) || stored == NIL_PAYLOAD_HASH) revert PayloadNotFound();
        if (stored != _payloadHash) revert InvalidPayloadHash();
        _inboundPayloadHashes[_oapp][_srcEid][_sender][_nonce] = NIL_PAYLOAD_HASH;
        emit InboundPayloadNilified(_oapp, _srcEid, _sender, _nonce, _payloadHash);
    }

    /// @notice Permanently removes a previously nilified payload.
    function burn(
        address _oapp,
        uint32 _srcEid,
        bytes32 _sender,
        uint64 _nonce,
        bytes32 _payloadHash
    ) external override onlyOAppOrDelegate(_oapp) {
        if (_payloadHash != NIL_PAYLOAD_HASH) revert InvalidPayloadHash();
        if (_inboundPayloadHashes[_oapp][_srcEid][_sender][_nonce] != NIL_PAYLOAD_HASH) revert PayloadNotNilified();
        delete _inboundPayloadHashes[_oapp][_srcEid][_sender][_nonce];
        emit InboundPayloadBurned(_oapp, _srcEid, _sender, _nonce);
    }
```

**File:** sdk/packages/lz-endpoint/README.md (L123-128)
```markdown
### What's preserved

- Token balances (ERC20 storage)
- Peer mappings (`peers[eid] => bytes32`)
- Ownership
- All other proxy storage
```
