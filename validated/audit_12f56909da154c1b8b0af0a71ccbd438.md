The vulnerability is real. Here is the full analysis:

---

### Title
`allowPushers` Signature Replay After `revokePusher` Re-hijacks Pusher Namespace Without Fresh Consent — (`smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol`)

---

### Summary

`allowPushers` signs over `(chainid, address(this), deadline, pusher, creator)` with no nonce and no used-signature tracking. After a pusher self-revokes via `revokePusher()`, the creator can replay the original pre-revocation signature (while `block.timestamp < deadline`) to unconditionally overwrite `namespaceRemapping[pusher]` back to `creator`. The pusher's revocation is silently undone.

---

### Finding Description

`allowPushers` constructs its digest as:

```solidity
keccak256(abi.encode(block.chainid, address(this), deadline, pusher, msg.sender))
``` [1](#0-0) 

The only replay guard is `_ensureDeadline(deadline)`, which checks `block.timestamp <= deadline`. There is no nonce, no per-`(pusher, creator)` used-signature bitmap, and no check that `namespaceRemapping[pusher]` is currently `address(0)` before writing. [2](#0-1) 

`revokePusher()` clears the mapping:

```solidity
namespaceRemapping[msg.sender] = address(0);
``` [3](#0-2) 

Because the signature is stateless and the deadline has not expired, the creator can immediately call `allowPushers` again with the identical `(deadline, [pusher], [sig])` arguments. The ECDSA check passes, and `namespaceRemapping[pusher] = creator` is written again — without any new consent from the pusher.

The code comment at lines 186–191 explicitly acknowledges the invariant the deadline is supposed to enforce:

> *"the signed consent carries no timestamp of its own, so an undated signature could re-establish a delegation AFTER the pusher revoked it."* [4](#0-3) 

The deadline prevents replay *after expiry*, but does nothing to prevent replay *within the deadline window after revocation*. The stated invariant is not enforced.

---

### Impact Explanation

After the replay, every subsequent push from the pusher's EOA lands in the **creator's namespace** instead of the pusher's own namespace. If the pusher had revoked in order to push prices into their own namespace (e.g., to serve their own pools), those pools now receive no updates — their oracle feeds go stale. Stale prices reaching a pool swap constitute **bad-price execution**, which is an accepted impact under the contest rules. The pusher has no on-chain visibility that the remapping was reinstated.

---

### Likelihood Explanation

The creator holds the original signature and knows the deadline. Replaying it is a single transaction requiring no special privilege beyond being the original `msg.sender` of the first `allowPushers` call. The window is the full remaining lifetime of the deadline (up to whatever the pusher signed, e.g., 1 hour). The pusher has no way to invalidate the signature short of waiting for the deadline to expire.

---

### Recommendation

Mark each `(pusher, creator, deadline)` tuple as consumed on first use, or include a per-pusher nonce in the signed digest:

```solidity
mapping(bytes32 => bool) private _usedConsents;

// inside allowPushers loop:
bytes32 consentKey = keccak256(abi.encode(chainid, address(this), deadline, pusher, msg.sender));
require(!_usedConsents[consentKey], "consent already used");
_usedConsents[consentKey] = true;
```

Alternatively, include a monotonically increasing per-pusher nonce in the signed message so that `revokePusher` can increment it, invalidating all outstanding signatures.

---

### Proof of Concept

```solidity
// Foundry test sketch
function testReplayAfterRevoke() public {
    uint256 deadline = block.timestamp + 1 hours;
    bytes memory sig = _signConsent(PUSHER_KEY, deadline, pusher, creator);

    address[] memory pushers = new address[](1);
    pushers[0] = pusher;
    bytes[] memory sigs = new bytes[](1);
    sigs[0] = sig;

    // Step 1: creator establishes delegation
    vm.prank(creator);
    oracle.allowPushers(deadline, pushers, sigs);
    assertEq(oracle.namespaceRemapping(pusher), creator);

    // Step 2: pusher revokes
    vm.prank(pusher);
    oracle.revokePusher();
    assertEq(oracle.namespaceRemapping(pusher), address(0));

    // Step 3: creator replays the SAME signature before deadline
    vm.prank(creator);
    oracle.allowPushers(deadline, pushers, sigs); // succeeds — no revert
    assertEq(oracle.namespaceRemapping(pusher), creator); // hijacked again
}
```

The replay succeeds because `_ensureDeadline` passes and the ECDSA digest is identical to the first call. `namespaceRemapping[pusher]` is overwritten to `creator` without any new consent from the pusher.

### Citations

**File:** smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol (L186-191)
```text
    /// @notice Delegates pusher wallets into the caller's namespace. The pusher's EIP-191
    ///         signature is REQUIRED — without it anyone could remap a foreign pusher
    ///         wallet into their own namespace and silently swallow its pushes. The
    ///         deadline is likewise required: the signed consent carries no timestamp of
    ///         its own, so an undated signature could re-establish a delegation AFTER the
    ///         pusher revoked it.
```

**File:** smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol (L192-211)
```text
    function allowPushers(uint256 deadline, address[] calldata pushers, bytes[] memory signatures) external {
        _ensureDeadline(deadline);

        uint256 l = pushers.length;
        require(l == signatures.length);
        for (uint256 i; i < l; i++) {
            address pusher = pushers[i];

            if (pusher == msg.sender) {
                revert NoSelfRemapping();
            }

            bytes32 hash = MessageHashUtils.toEthSignedMessageHash(
                keccak256(abi.encode(block.chainid, address(this), deadline, pusher, msg.sender))
            );
            require(pusher == ECDSA.recover(hash, signatures[i]));

            namespaceRemapping[pusher] = msg.sender;
            emit PusherAuthorized(pusher, msg.sender);
        }
```

**File:** smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol (L238-243)
```text
    function revokePusher() external {
        address creator = namespaceRemapping[msg.sender];
        if (creator == address(0) || creator == msg.sender) revert NoSelfRemapping();
        namespaceRemapping[msg.sender] = address(0);
        emit PusherRevoked(msg.sender, creator);
    }
```
