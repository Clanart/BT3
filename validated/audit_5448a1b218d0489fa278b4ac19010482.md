### Title
`FrozenStatus.Incoming` permanently blocks `handlePostRequestTimeouts`, locking escrowed relayer fees owed via timeout refunds - (File: evm/src/core/HandlerV2.sol)

### Summary

### Finding Description
`EvmHost` exposes a `FrozenStatus` enum (`None`, `Incoming`, `Outgoing`, `All`) that governance/admin can set to halt specific classes of protocol operation during a security incident (e.g. a faulty consensus client or byzantine counterparty). [1](#0-0)  Intuitively, `Incoming` is meant only to stop *newly delivered* cross-chain messages (post requests, get responses) from a compromised source. However, `HandlerV2`'s `notFrozen(host)` modifier — which reverts when `state == FrozenStatus.Incoming || state == FrozenStatus.All` — is applied uniformly to `handlePostRequests`, `handleGetResponses`, **and** `handlePostRequestTimeouts`/`handleGetRequestTimeouts`. [2](#0-1) [3](#0-2)  `handlePostRequestTimeouts` is the sole path that refunds the payer's escrowed relayer fee for a request this host *originally dispatched* to a (possibly unrelated, healthy) destination that never delivered it in time. [4](#0-3) 

Because freezing is host-wide rather than scoped to the specific offending counterparty/state machine, an admin who sets `FrozenStatus.Incoming` to stop malicious inbound proofs from one chain simultaneously blocks timeout-refund processing for every other counterparty this host has outgoing requests pending against — exactly the same broken invariant as the `AutoCompound` bug: a deactivation/kill-switch flag intended for one purpose (stopping new activity) incidentally locks funds that are already owed to a user (the timeout refund) with no alternate withdrawal path, since `handlePostRequestTimeouts` is the only function that credits the refund.

### Impact Explanation
Any payer with a pending outgoing `PostRequest` whose timeout has elapsed cannot claim their refund for as long as the host remains in `FrozenStatus.Incoming`/`All`, even though their request has nothing to do with the reason the freeze was applied. Funds (relayer fee/dispatch fee) already escrowed via `requestCommitments`/`FeeMetadata` sit stuck in the host contract, unrecoverable by the rightful beneficiary — a fund-lock condition matching "loss of funds" / "unauthorized... logic" class impacts called out in the bounty scope.

### Likelihood Explanation
This requires only a routine, expected governance action (freezing incoming messages in response to a fault on one counterparty) — not a malicious/compromised admin — combined with the pre-existing, common state of having outstanding timed-out requests to any destination. Given that freezes are the designed incident-response mechanism (per the consensus-proof fault-handling doc), and the fault only concerns a single chain's consensus, while `_frozen` is a single host-wide flag with no per-state-machine granularity, this scenario is readily triggered during any real security incident, not a contrived edge case.

### Recommendation
Scope the `notFrozen` gating for timeout-handling functions (`handlePostRequestTimeouts`, `handleGetRequestTimeouts`) separately from inbound-delivery functions (`handlePostRequests`, `handleGetResponses`), or make `FrozenStatus` per-state-machine so freezing incoming traffic from one counterparty does not block refund/timeout settlement tied to other counterparties. At minimum, timeout refund processing should not be blocked by `FrozenStatus.Incoming` (only `Outgoing`/`All`, if it must be blocked at all), mirroring the `AutoCompound` mitigation of decoupling the kill-switch from already-owed balances.

### Proof of Concept
1. App `A` dispatches a `PostRequest` from `EvmHost` on chain `X` to chain `Y`, escrowing a relayer fee (`FeeMetadata.fee`) tracked under `requestCommitments`.
2. Chain `Y`'s consensus client develops a fault (or is otherwise compromised/eclipsed); governance calls the host's freeze setter to set `_frozen = FrozenStatus.Incoming` on chain `X`'s host, intending only to stop new incoming proofs from `Y`.
3. Before `Y` ever delivers the post request, `leaf.request.timeout()` elapses.
4. App `A` (or any relayer on its behalf) submits a valid non-membership proof to `handlePostRequestTimeouts` to reclaim the escrowed fee.
5. The call reverts with `HostFrozen()` because `notFrozen(host)` sees `FrozenStatus.Incoming`, even though this timeout has nothing to do with the fault that triggered the freeze. [5](#0-4) 
6. The escrowed fee remains locked in the host contract for the entire duration of the freeze, with no alternate path to reclaim it — precisely analogous to `AutoCompound.withdrawLeftoverBalances` reverting once its vault is deactivated.

Note: I was unable to fully verify how/whether governance can subsequently un-freeze the host (e.g. an explicit `setFrozenStatus`/admin function) within the indexed portion of `EvmHost.sol`, since the freeze-setter function body itself was not returned by search; this affects only how long the lock persists, not the existence of the blocking path itself, which is directly confirmed in `HandlerV2.sol`.

### Citations

**File:** sdk/packages/core/contracts/libraries/Message.sol (L24-33)
```text
enum FrozenStatus {
    /// @notice Normal operation - all functions are enabled
    None,
    /// @notice Incoming messages are blocked - prevents receiving cross-chain messages
    Incoming,
    /// @notice Outgoing messages are blocked - prevents sending cross-chain messages
    Outgoing,
    /// @notice All operations are frozen - complete protocol halt
    All
}
```

**File:** evm/src/core/HandlerV2.sol (L108-112)
```text
    modifier notFrozen(IHost host) {
        FrozenStatus state = host.frozen();
        if (state == FrozenStatus.Incoming || state == FrozenStatus.All) revert HostFrozen();
        _;
    }
```

**File:** evm/src/core/HandlerV2.sol (L254-270)
```text
    function handlePostRequestTimeouts(IHost host, PostRequestTimeoutMessage calldata message)
        external
        notFrozen(host)
    {
        uint256 delay = block.timestamp - host.stateMachineCommitmentUpdateTime(message.height);
        uint256 challengePeriod = host.challengePeriod();
        if (challengePeriod != 0 && challengePeriod > delay) revert ChallengePeriodNotElapsed();

        // fetch the state commitment
        StateCommitment memory state = host.stateMachineCommitment(message.height);
        if (state.stateRoot == bytes32(0)) revert StateCommitmentNotFound();
        uint256 timeoutsLength = message.timeouts.length;

        for (uint256 i = 0; i < timeoutsLength; ++i) {
            PostRequest memory request = message.timeouts[i];
            // timed-out?
            if (request.timeout() > state.timestamp) revert MessageNotTimedOut();
```

**File:** docs/content/developers/evm/api/ihandler.mdx (L121-149)
```text
### handlePostRequestTimeouts()

Processes timed-out POST requests and triggers refunds.

```solidity lineNumbers
function handlePostRequestTimeouts(
    IHost host,
    PostRequestTimeoutMessage calldata message
) external
```

| Parameter | Type | Description |
|-----------|------|-------------|
| `host` | `IHost` | The IHost contract |
| `message` | `PostRequestTimeoutMessage` | Struct containing timeout proof and requests |

**Access:** Permissionless (can be called by anyone)

**Process:**
1. Verifies timeout proof
2. For each request:
   - Validates timeout timestamp has passed
   - Calls `onPostRequestTimeout()` on source application
   - Refunds relayer fee to payer (only if callback succeeds)

**Important:**
- Application timeout callback is called **before** refund
- If callback reverts, no refund occurs
- Timeout can be resubmitted until callback succeeds
```
