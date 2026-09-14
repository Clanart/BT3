## Title
DID Recovery Attestation Signature Omits Recovering-Coin Context, Enabling Cross-DID Signature Replay - (File: `chia/wallet/did_wallet/did_wallet_puzzles.py`)

### Summary
The DID recovery/attestation mechanism generates an `AGG_SIG_UNSAFE` authorization that a backup ID signs to approve transferring a DID to a new owner puzzle hash (`newpuz`). The signed message covers only `newpuz` and the signer's `pubkey` — it does **not** include the `recovering_coin_id` (the specific DID coin being recovered) inside the actual cryptographic message that gets signed. That binding is only expressed as a separate, unsigned `CREATE_COIN_ANNOUNCEMENT` condition baked into the quoted puzzle itself. This is structurally the same flaw as the Sherlock finding: a signature that authorizes an action for one context (`msg.sender`/one org) is reusable in a different context (another org/DID) because the digest never included the context-identifying value.

### Finding Description
`create_recovery_message_puzzle()` builds the "attestment" puzzle that a backup DID spends to vouch for a recovery: [1](#0-0) 

The puzzle emits two conditions when spent:
1. `CREATE_COIN_ANNOUNCEMENT(recovering_coin_id)` — asserted later by the recovering DID's spend to prove "some backup approved."
2. `AGG_SIG_UNSAFE(pubkey, newpuz)` — the actual cryptographic authorization, requiring a BLS signature by the backup's key over the message `newpuz`.

Critically, `recovering_coin_id` is only embedded as a *quoted puzzle literal* that determines the puzzle's tree hash (and therefore which throwaway/message coin must be created and spent), but it is **never part of the signed message**. `AGG_SIG_UNSAFE` in Chia consensus intentionally provides no coin binding at all — its message is checked verbatim against the raw bytes supplied, with no coin id, puzzle hash, or amount suffix appended (unlike `AGG_SIG_ME`/`AGG_SIG_PARENT`/`AGG_SIG_PUZZLE`, which do get such suffixes, see `chia/consensus/condition_tools.py` lines 56-96, 99-123).

Because the ephemeral "message coin" that carries this puzzle is a permissionless, zero-value coin (`create_spend_for_message`, same file, lines 158-172) — anyone can construct and fund a coin at any puzzle hash and spend it with any solution — an attacker who has ever observed one broadcast, valid `(pubkey, newpuz) -> signature` pair (which is fully public once included in any historical DID-recovery transaction) can:
1. Pick a *different* DID (`recovering_coin_id` = some other DID's current coin id) that lists the same backup pubkey as an authorized recovery signer with a satisfiable threshold (`num_of_backup_ids_needed`).
2. Re-derive `create_recovery_message_puzzle(new_recovering_coin_id, newpuz, pubkey)` themselves (all inputs are public/attacker-chosen except the reused signature).
3. Create and spend the corresponding zero-value message coin, replaying the previously captured signature to satisfy `AGG_SIG_UNSAFE(pubkey, newpuz)`.
4. Produce the required `CREATE_COIN_ANNOUNCEMENT(new_recovering_coin_id)` from this replayed spend, which the target DID's `assert_coin_announcement` recovery solution consumes as "valid backup approval" — without the backup ID ever cooperating for that specific DID.

This mirrors the Sherlock bug exactly: `digest = hash(msg.sender)` (missing contract address) → here `signed_message = newpuz` (missing `recovering_coin_id`/DID identity), enabling reuse of one valid signature across different "organizations" (DIDs) that share the same signer (backup ID).

### Impact Explanation
If exploitable, this allows unauthorized reassignment of DID singleton ownership (and by extension NFTs/DIDs/assets gated by DID ownership) to a puzzle hash from a previously observed, unrelated recovery transaction, without the backup signer's consent for that specific DID. This is a concrete "unauthorized coin movement / forged authorization" scenario reachable by any user who can observe chain history and submit a spend bundle — it requires no privileged access, matching the "single submitted spend bundle" reachability bar. Practical exploitation is bounded by the attacker's ability to control or influence `newpuz` (the reused signature only authorizes transfer to the exact puzzle hash from the original signature) and by finding two DIDs sharing an overlapping backup-id/threshold configuration — a realistic setup for custodial/organizational deployments that reuse a single "recovery" DID across many managed accounts.

### Likelihood Explanation
Requires: (a) a threshold ≤ number of colluding/observed attestations, (b) knowledge of at least one historical valid `(pubkey, newpuz)` signature pair (trivially obtainable from any public DID recovery transaction), and (c) a second DID sharing that backup pubkey in its recovery list. This is most likely in organizational/custodial contexts that reuse the same "recovery" DID as a shared backup signer for many accounts — directly analogous to "organizations managing multiple IPs... sharing the same signer" in the original report.

### Recommendation
Bind the `AGG_SIG_UNSAFE` message in `create_recovery_message_puzzle` to the specific recovering coin, e.g., sign over `recovering_coin_id + newpuz` (or the DID's launcher id) instead of `newpuz` alone, so a captured attestation cannot be replayed against a different DID coin. Alternatively, switch this condition to a coin-bound `AGG_SIG_ME`/`AGG_SIG_PUZZLE` variant scoped to the recovering coin so consensus-level suffixing prevents cross-context reuse.

### Proof of Concept
Conceptual (not run against a live network, derived from code reading):
1. DID_A and DID_B both list `backup_pub` in `backup_ids`, each with `num_of_backup_ids_needed = 1`.
2. Attacker legitimately recovers DID_A (which they own) to `newpuz = attacker_target_ph`, obtaining backup cooperation and thus a valid `sig = Sign(backup_sk, attacker_target_ph)` for `AGG_SIG_UNSAFE(backup_pub, attacker_target_ph)`, published on-chain as part of DID_A's recovery spend bundle.
3. Attacker builds `create_recovery_message_puzzle(recovering_coin_id=DID_B_current_coin_id, newpuz=attacker_target_ph, pubkey=backup_pub)`, creates a zero-value coin at that puzzle hash, and spends it reusing `sig` from step 2 (no cooperation from backup needed this time).
4. The resulting `CREATE_COIN_ANNOUNCEMENT(DID_B_current_coin_id)` is consumed by a recovery spend of DID_B, which the attacker constructs unilaterally, moving DID_B's ownership to `attacker_target_ph` without an independent, DID_B-specific backup approval.

**Uncertainty**: I was unable to trace the full `did_innerpuz.clsp` puzzle logic (CLVM source not indexed) to definitively confirm every detail of how `num_of_backup_ids_needed` attestations are collected/validated in the recovery inner-solution path, nor could I confirm from the Python driver code alone whether any additional binding (e.g., a nonce derived from all coins involved) is layered on outside what's shown here. A Devin session with full repo/file access (including the `.clsp` puzzle sources) would be needed to fully confirm this is exploitable end-to-end rather than mitigated by puzzle-level checks not visible in the indexed Python driver code.

### Citations

**File:** chia/wallet/did_wallet/did_wallet_puzzles.py (L138-155)
```python
def create_recovery_message_puzzle(recovering_coin_id: bytes32, newpuz: bytes32, pubkey: G1Element) -> Program:
    """
    Create attestment message puzzle
    :param recovering_coin_id: ID of the DID coin needs to recover
    :param newpuz: New wallet puzzle hash
    :param pubkey: New wallet pubkey
    :return: Message puzzle
    """
    puzzle = Program.to(
        (
            1,
            [
                [ConditionOpcode.CREATE_COIN_ANNOUNCEMENT, recovering_coin_id],
                [ConditionOpcode.AGG_SIG_UNSAFE, bytes(pubkey), newpuz],
            ],
        )
    )
    return puzzle
```
