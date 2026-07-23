Audit Report

## Title
Pusher Delegation Signature in `allowPushers` Can Be Replayed Within the Deadline Window to Nullify a Pusher's Revocation — (`smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol`)

## Summary
`CompressedOracleV1.allowPushers` validates a pusher's EIP-191 consent signature but never marks it as consumed. Because `revokePusher()` only zeroes `namespaceRemapping[pusher]` without invalidating the original signature, a creator can replay the identical `(deadline, signature)` tuple at any point before the deadline expires to silently re-establish the delegation the pusher just revoked. The code's own NatSpec acknowledges this risk but incorrectly treats the deadline as a complete fix.

## Finding Description
`allowPushers` builds a hash over `(chainid, address(this), deadline, pusher, msg.sender)` and writes `namespaceRemapping[pusher] = msg.sender` on successful signature recovery:

```solidity
// CompressedOracle.sol L204-209
bytes32 hash = MessageHashUtils.toEthSignedMessageHash(
    keccak256(abi.encode(block.chainid, address(this), deadline, pusher, msg.sender))
);
require(pusher == ECDSA.recover(hash, signatures[i]));
namespaceRemapping[pusher] = msg.sender;
```

There is no nonce, no used-signature bitmap, and no per-pusher revocation counter. The only replay guard is `_ensureDeadline`, which only rejects calls made **after** the deadline:

```solidity
// OracleBase.sol L124-126
function _ensureDeadline(uint256 deadline) internal view virtual {
    require(block.timestamp <= deadline, DeadlineExceeded());
}
```

`revokePusher()` clears the mapping but does not increment any counter or invalidate the original signature:

```solidity
// CompressedOracle.sol L238-243
function revokePusher() external {
    address creator = namespaceRemapping[msg.sender];
    if (creator == address(0) || creator == msg.sender) revert NoSelfRemapping();
    namespaceRemapping[msg.sender] = address(0);
    emit PusherRevoked(msg.sender, creator);
}
```

Exploit flow:
1. Pusher P signs consent with `deadline = block.timestamp + 365 days`.
2. Creator C calls `allowPushers` → `namespaceRemapping[P] = C`.
3. P calls `revokePusher()` → `namespaceRemapping[P] = address(0)`.
4. C immediately calls `allowPushers` with the original `(deadline, signature)` — the signature is still cryptographically valid and `_ensureDeadline` passes — `namespaceRemapping[P] = C` is restored.
5. P's subsequent `fallback()` pushes resolve `creator = namespaceRemapping[msg.sender] = C` and land in C's namespace, not P's own.

The `fallback()` push path at L315-316 reads `namespaceRemapping[msg.sender]` at call time, so the re-established mapping immediately redirects all future pushes.

## Impact Explanation
The broken revocation invariant enables bad-price execution. If P revoked because their signing key was compromised, C's replay keeps the compromised pusher active in C's namespace. Any pool whose `PriceProvider` is bound to C's feedId continues receiving price data from the compromised pusher. The staleness check (`maxTimeDelta`) does not protect here because P (or an attacker holding P's key) is still pushing fresh timestamps — the data is fresh but wrong for C's namespace. This satisfies the "bad-price execution" allowed impact: stale, inverted, or attacker-controlled bid/ask quotes reaching a pool swap, with direct loss of trader principal or LP fees.

## Likelihood Explanation
`allowPushers` is a public function callable by any address holding the original signature tuple. The creator is the only party who can replay (the signature commits to `msg.sender = creator`), but any address can be a creator in this registrationless design. Delegation deadlines are expected to be long-lived (the "zero setup transactions" design goal), so the replay window is wide. The creator has a clear economic incentive to replay if the pusher's revocation would remove a data source from their namespace. No on-chain mechanism prevents the replay before the pusher notices.

## Recommendation
Track consumed signatures with a per-pusher revocation nonce incremented on every `revokePusher()` or `removePushers()` call, and include it in the signed message:

```solidity
mapping(address => uint256) public pusherRevocationNonce;

// In allowPushers:
bytes32 hash = MessageHashUtils.toEthSignedMessageHash(
    keccak256(abi.encode(
        block.chainid, address(this), deadline,
        pusher, msg.sender,
        pusherRevocationNonce[pusher]
    ))
);

// In revokePusher / removePushers:
pusherRevocationNonce[pusher]++;
```

Alternatively, store a `mapping(bytes32 => bool) usedSignatures` and mark each signature hash as consumed on first use.

## Proof of Concept
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
assertEq(oracle.namespaceRemapping(pusher), address(0));

// 4. Creator replays the SAME signature — no new signature needed
vm.prank(creator);
oracle.allowPushers(deadline, _arr(pusher), _arr(sig));

// 5. Delegation is silently re-established
assertEq(oracle.namespaceRemapping(pusher), creator); // revocation nullified

// 6. Pusher's next push lands in creator's namespace
vm.prank(pusher);
(bool ok,) = address(oracle).call(_wordAt(0, 0, raw, tsMs));
assertTrue(ok);
assertEq(oracle.getOracleData(oracle.feedIdOf(creator, 0, 0)).price, decodedPrice);
assertEq(oracle.getOracleData(oracle.feedIdOf(pusher, 0, 0)).price, 0);
``` [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

### Citations

**File:** smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol (L192-212)
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

**File:** smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol (L315-316)
```text
        address creator = namespaceRemapping[msg.sender];
        if (creator == address(0)) creator = msg.sender;
```

**File:** smart-contracts-poc/contracts/oracles/compressed/OracleBase.sol (L124-126)
```text
    function _ensureDeadline(uint256 deadline) internal view virtual {
        require(block.timestamp <= deadline, DeadlineExceeded());
    }
```
