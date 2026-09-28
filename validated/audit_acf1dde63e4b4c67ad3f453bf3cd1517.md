### Title
Deposits sent to a deterministically-known multisig address before its activation block are never scanned, permanently locking funds - (File: processor/src/multisigs/scanner.rs)

### Summary
Analogous to pre-funding a deterministic escrow PDA before bonding-curve creation (which breaks the `sol_escrow_lamports == real_sol_reserves` invariant), Serai's processor `Scanner` silently ignores any outputs paid to a multisig key's Bitcoin address in blocks strictly earlier than the key's `activation_number`. Because the group key — and therefore its tweaked P2TR address — is publicly derivable once a new multisig set is announced but before it activates, an unprivileged party can send BTC to that address in a pre-activation block. The output is never emitted as a `ScannerEvent::Block`, never written to `ram_outputs`/`seen`, and never enters the wallet's spendable UTXO set, so the funds sit at an address the network will not spend.

### Finding Description
`Scanner::register_key` stores each group key with an `activation_number` (`processor/src/multisigs/scanner.rs:284-298`). In `Scanner::run`, for every block the scanner iterates registered keys and explicitly skips keys whose activation is later than the block being scanned:

```rust
for (activation_number, key) in scanner.keys.clone() {
  if activation_number > block_being_scanned {
    continue;
  }
```
(`processor/src/multisigs/scanner.rs:550-553`)

For Bitcoin, `network.get_outputs(&block, key)` builds a `networks::bitcoin::wallet::Scanner` that matches `output.script_pubkey` against the key's tweaked P2TR script (`networks/bitcoin/src/wallet/mod.rs:199-214`). Scanning is strictly forward from `ram_scanned + 1` (`scanner.rs:492`), so an output created before activation is skipped by the `activation_number > block_being_scanned` check and is never revisited — there is no catch-up scan for pre-activation history of newly registered keys. This mirrors H-02: state that exists at a deterministic address before the protocol's "creation" point (here, key activation) is treated as nonexistent by the accounting logic rather than initialized into it.

### Impact Explanation
The multisig address is computable by anyone who learns the pending group key (it is announced/derivable ahead of activation for rotation). An attacker sends an above-dust output (`>= N::DUST`, `scanner.rs:564`) to it in a block before `activation_number`. The output is never reported as received, so it is never added to the spendable set and never consolidated — the BTC is bricked at a network-controlled address (or requires out-of-band/manual recovery). This is "funds received that are not spendable": the protocol cannot distinguish the pre-funded output from a deposit because it simply never looks.

### Likelihood Explanation
Medium-low: exploitation requires knowing the pre-activation group key and timing a transaction into a pre-activation block, both feasible for any observer of the coordinator's announced upcoming multisig. The attacker burns their own funds, so the payoff is griefing (stranding protocol liquidity / forcing an unplanned recovery or a panic path), not theft — analogous in spirit to the Medium-likelihood DoS in H-02.

### Recommendation
On key registration, scan (or reject-by-policy) history for the new key's address back to a defined start, or treat pre-activation outputs to registered addresses as an error condition that must be swept/acknowledged — mirroring H-02's "initialize `real_sol_reserves` to existing balance or sweep" recommendation. Concretely: for each newly registered key, scan blocks `[0, activation_number)` for its script and either credit or explicitly quarantine any found outputs, rather than the unconditional `continue` at `scanner.rs:551`.

### Proof of Concept
1. Observe the coordinator announcing the next multisig group key `K_new` with activation block `A` (`register_key`, `scanner.rs:276-303`).
2. Derive the tweaked P2TR address: `p2tr_script_buf(tweak_keys(K_new).group_key())` (`networks/bitcoin/src/wallet/mod.rs:46-86`).
3. In block `A-1`, broadcast a transaction paying ≥ dust to that address.
4. The scanner reaches `A-1`, evaluates `activation_number (A) > block_being_scanned (A-1)` → `continue` (`scanner.rs:551`); the output is never pushed to `outputs`, never saved via `save_outputs`, and never marked `seen`.
5. Scanning proceeds forward only (`(ram_scanned + 1) ..= latest_block_to_scan`, `scanner.rs:492`); the pre-activation output is permanently absent from the spendable set while remaining on-chain at the multisig address.