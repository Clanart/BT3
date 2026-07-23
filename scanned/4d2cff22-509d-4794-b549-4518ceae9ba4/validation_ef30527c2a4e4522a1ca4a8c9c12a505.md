### Title
Pusher Delegation Signature in `allowPushers` Can Be Replayed Within the Deadline Window to Nullify a Pusher's Revocation — (`smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol`)

---

### Summary

`CompressedOracleV1.allowPushers` validates a pusher's EIP-191 consent signature but never marks it as consumed. Within the signed deadline window, the creator can replay the identical signature an unlimited number of times. Because `revokePusher()` only clears `namespaceRemapping[pusher]` in storage, a single replay call by the creator immediately re-establishes the delegation the pusher just revoked, making revocation ineffective for the entire lifetime of the original signature.

---

### Finding Description

`allowPushers` builds a hash over `(chainid, address(this), deadline, pusher, msg.sender)` and recovers the pusher's address from the supplied signature:

```solidity
// CompressedOracle.sol L204-L209
bytes32 hash = MessageHashUtils.toEthSignedMessageHash(
    keccak256(abi.encode(block.chainid, address(this), deadline, pusher, msg.sender))
);
require(pusher == ECDSA.recover(hash, signatures[i]));

namespaceRemapping[pusher] = msg.sender;
```

There is no nonce, no used-signature bitmap, and no per-pusher revocation counter. The only replay guard is `_ensureDeadline`, which only rejects calls made **after** the deadline:

```solidity
// OracleBase.sol L124-L126
function _ensureDeadline(uint256 deadline) internal view virtual {
    require(block.timestamp <= deadline, DeadlineExceeded());
}
```

`revokePusher()` clears the mapping:

```solidity
// CompressedOracle.sol L238-L243
function revokePusher() external {
    address creator = namespaceRemapping[msg.sender];
    if (creator == address(0) || creator == msg.sender) revert NoSelfRemapping();
    namespaceRemapping[msg.sender] = address(0);
    emit PusherRevoked(msg.sender, creator);
}
```

But because the original signature is still valid (deadline has not expired), the creator can immediately call `allowPushers` again with the same `(deadline, signature)` tuple, writing `namespaceRemapping[pusher] = creator` back. The code's own NatSpec acknowledges the risk ("an undated signature could re-establish a delegation AFTER the pusher revoked it") but treats the deadline as the complete fix — it is not, because the deadline only prevents replay **after** expiry, not within the window.

---

### Impact Explanation

**Broken revocation invariant → potential bad-price execution.**

Concrete path:

1. Pusher P signs a delegation to Creator C with a long deadline (e.g., 365 days).
2. C calls `allowPushers` — P's `fallback()` pushes land in C's namespace.
3. P calls `revokePusher()` — intending to push into their own namespace from now on.
4. C immediately calls `allowPushers` with the original `(deadline, signature)` — `namespaceRemapping[P] = C` is restored.
5. P, unaware of the re-establishment, continues pushing data intended for their own namespace; those pushes silently land in C's namespace.
6. Any pool whose `PriceProvider` is bound to C's feedId now receives price data that P did not intend for that context, or receives data from a pusher whose key may be compromised (the very reason P revoked).
7. If the misdirected price deviates enough from the true market, swaps execute at a bad bid/ask → direct loss of trader principal or LP fees.

The staleness check (`maxTimeDelta`) does not protect here because P is still actively pushing fresh timestamps — the data is fresh but wrong for C's namespace.

---

### Likelihood Explanation

- `allowPushers` is a public, permissionless function callable by any address that holds the original signature tuple.
- The creator is the only party who can replay (the signature commits to `msg.sender = creator`), but the creator is a semi-trusted party in this registrationless design — the protocol explicitly allows any address to be a creator.
- Delegation deadlines are expected to be long-lived (the design is "zero setup transactions"), so the replay window is wide.
- No on-chain monitoring or event-based alerting can prevent the creator from replaying before the pusher notices.

---

### Recommendation

Track consumed signatures. The simplest fix is a per-pusher revocation nonce that is incremented on every successful `revokePusher()` or `removePushers()` call and included in the signed message:

```solidity
mapping(address => uint256) public pusherRevocationNonce;

// In allowPushers:
bytes32 hash = MessageHashUtils.toEthSignedMessageHash(
    keccak256(abi.encode(
        block.chainid, address(this), deadline,
        pusher, msg.sender,
        pusherRevocationNonce[pusher]   // <-- add nonce
    ))
);

// In revokePusher / removePushers:
pusherRevocationNonce[pusher]++;
```

Alternatively, store a `mapping(bytes32 => bool) usedSignatures` and mark each signature hash as consumed on first use.

---

### Proof of Concept

```solidity
// 1. Pusher signs consent with deadline = block.timestamp + 365 days
bytes memory sig = _signConsent(PUSHER_KEY, deadline, pusher, creator);

// 2. Creator establishes delegation
vm.prank(creator);
oracle.allowPushers(deadline, _arr(pusher), _arr(sig));
assertEq(oracle.namespaceRemapping(pusher), creator);

// 3. Pusher revokes
vm.prank(pusher);
oracle.revokePusher();
assertEq(oracle.namespaceRemapping(pusher), address(0)); // revoked

// 4. Creator replays the SAME signature — no new signature needed
vm.prank(creator);
oracle.allowPushers(deadline, _arr(pusher), _arr(sig));

// 5. Delegation is silently re-established
assertEq(oracle.namespaceRemapping(pusher), creator); // revocation nullified

// 6. Pusher's next push lands in creator's namespace, not pusher's own
vm.prank(pusher);
(bool ok,) = address(oracle).call(_wordAt(0, 0, raw, tsMs));
assertTrue(ok);
assertEq(oracle.getOracleData(oracle.feedIdOf(creator, 0, 0)).price, decodedPrice);
assertEq(oracle.getOracleData(oracle.feedIdOf(pusher,  0, 0)).price, 0); // pusher's own ns is stale
``` [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

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

**File:** smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol (L236-243)
```text
    /// @notice Allows a pusher to self-revoke their delegation. After revocation the
    ///         wallet pushes into its OWN namespace again (the registrationless default).
    function revokePusher() external {
        address creator = namespaceRemapping[msg.sender];
        if (creator == address(0) || creator == msg.sender) revert NoSelfRemapping();
        namespaceRemapping[msg.sender] = address(0);
        emit PusherRevoked(msg.sender, creator);
    }
```

**File:** smart-contracts-poc/contracts/oracles/compressed/OracleBase.sol (L124-126)
```text
    function _ensureDeadline(uint256 deadline) internal view virtual {
        require(block.timestamp <= deadline, DeadlineExceeded());
    }
```
