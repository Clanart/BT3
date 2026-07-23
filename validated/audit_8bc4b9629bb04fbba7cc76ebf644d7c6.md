The code confirms the vulnerability. The key facts:

1. `allowPushers` (L204-207) hashes `(chainid, address(this), deadline, pusher, msg.sender)` with no nonce or consumed-signature tracking. [1](#0-0) 

2. `_ensureDeadline` only rejects calls **after** expiry — it does not prevent replay within the window. [2](#0-1) 

3. `revokePusher()` clears the mapping but cannot invalidate the still-valid signature. [3](#0-2) 

4. The `fallback()` push path reads `namespaceRemapping[msg.sender]` at runtime, so a replayed delegation immediately redirects all subsequent pushes into the creator's namespace. [4](#0-3) 

5. The NatSpec at L186-191 explicitly acknowledges the deadline is the intended guard against post-revocation replay, confirming the design intent — and the gap. [5](#0-4) 

---

Audit Report

## Title
Signature Replay in `allowPushers` Nullifies Pusher Self-Revocation Within Deadline Window — (`smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol`)

## Summary
`CompressedOracleV1.allowPushers` validates a pusher's EIP-191 consent signature but never marks it as consumed. Because `revokePusher()` only zeroes `namespaceRemapping[pusher]` without invalidating the original signature, the creator can immediately replay the identical `(deadline, signature)` tuple to restore the delegation. This makes pusher self-revocation ineffective for the entire remaining lifetime of the original signature, causing the pusher's subsequent price pushes to silently land in the creator's namespace rather than the pusher's own.

## Finding Description
`allowPushers` constructs a hash over `(chainid, address(this), deadline, pusher, msg.sender)` and recovers the signer:

```solidity
// CompressedOracle.sol L204-209
bytes32 hash = MessageHashUtils.toEthSignedMessageHash(
    keccak256(abi.encode(block.chainid, address(this), deadline, pusher, msg.sender))
);
require(pusher == ECDSA.recover(hash, signatures[i]));
namespaceRemapping[pusher] = msg.sender;
```

There is no nonce, no used-signature bitmap, and no per-pusher revocation counter. The only replay guard is `_ensureDeadline`, which rejects calls only **after** the deadline — it does not prevent re-use of the same signature within the window.

`revokePusher()` clears the mapping:
```solidity
namespaceRemapping[msg.sender] = address(0);
```
But the original signature remains cryptographically valid. The creator can call `allowPushers` again with the identical `(deadline, signature)` tuple in the same block, writing `namespaceRemapping[pusher] = creator` back. The `fallback()` push path reads this mapping at call time, so all subsequent pushes from the pusher are silently redirected to the creator's namespace.

The NatSpec at L186-191 explicitly states the deadline is the fix for post-revocation replay — confirming the design intent — but the deadline only prevents replay after expiry, not within the window.

## Impact Explanation
**Bad-price execution reaching pool swaps.** After the creator replays the signature, the pusher's fresh price data lands in the creator's feedId namespace. Any pool whose `PriceProvider` is bound to the creator's feedId now receives price data the pusher did not intend for that context — including data pushed after the pusher believed they had revoked (e.g., because their key was compromised or they are serving a different market). The staleness check (`maxTimeDelta`) does not protect here because the pusher is still actively pushing fresh timestamps; the data is fresh but wrong for the creator's namespace. If the misdirected price deviates from the true market, swaps execute at a bad bid/ask, causing direct loss of trader principal or LP fees.

## Likelihood Explanation
- `allowPushers` is a public function callable by any address holding the original `(deadline, signature)` tuple; the creator already holds it from the initial delegation.
- The creator is the only party who can replay (the signature commits to `msg.sender = creator`), but the protocol explicitly allows any address to be a creator — the creator is semi-trusted, not fully trusted.
- Delegation deadlines are expected to be long-lived (the design goal is "zero setup transactions"), so the replay window is wide.
- The replay requires a single transaction with no additional off-chain material; it is repeatable every time the pusher calls `revokePusher()`.

## Recommendation
Include a per-pusher revocation nonce in the signed message and increment it on every `revokePusher()` or `removePushers()` call:

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
(bool ok,) = address(oracle).call(encodedSlotWord);
assertTrue(ok);
// Creator's feedId has the price; pusher's own feedId is stale
assertEq(oracle.getOracleData(oracle.feedIdOf(creator, 0, 0)).price, decodedPrice);
assertEq(oracle.getOracleData(oracle.feedIdOf(pusher,  0, 0)).price, 0);
```

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

**File:** smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol (L204-207)
```text
            bytes32 hash = MessageHashUtils.toEthSignedMessageHash(
                keccak256(abi.encode(block.chainid, address(this), deadline, pusher, msg.sender))
            );
            require(pusher == ECDSA.recover(hash, signatures[i]));
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
