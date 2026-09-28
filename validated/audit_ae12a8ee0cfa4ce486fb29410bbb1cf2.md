### Title
Untrusted scalar offset in deserialized `ReceivedOutput`/`Output` lets an attacker redefine which group key owns an output - ([File: networks/bitcoin/src/wallet/mod.rs](networks/bitcoin/src/wallet/mod.rs), [File: processor/src/networks/bitcoin.rs](processor/src/networks/bitcoin.rs))

### Summary
CVE-2016-10009 is an untrusted-search-path bug: bytes arriving over a channel the attacker controls (a forwarded agent socket) select which PKCS#11 module gets loaded and trusted. The structural analog in Serai is `ReceivedOutput`, which serializes a *claim* — a scalar `offset` — alongside an on-chain `TxOut`/`OutPoint`, and `Output::key()` in the processor resolves ownership of that output as `script_pubkey_key − G·offset`. Because both the script and the offset are attacker-controlled bytes read by `ReceivedOutput::read` / `Output::read`, an attacker can make an output resolve to an arbitrary group key, i.e. select which "key module" the funds are attributed to.

### Finding Description
`ReceivedOutput` bundles the spend authorization parameter (`offset`) with the output it claims to spend. It is deserialized from raw bytes with no binding check:

- `ReceivedOutput::read` reads `offset` via `Secp256k1::read_F` and then consensus-decodes the attacker-supplied `TxOut` and `OutPoint` (`networks/bitcoin/src/wallet/mod.rs:122-134`). Nothing verifies that `offset` is one of the legitimately registered offsets (`Scalar::ZERO`, `hash_to_F(KEY_DST, "branch" | "change" | "forward")` in `processor/src/networks/bitcoin.rs:333-344`), or that `p2tr_script_buf(key + G·offset) == output.script_pubkey`.
- `Output::read` consumes this via `ReceivedOutput::read(reader)` (`processor/src/networks/bitcoin.rs:145-166`).
- Ownership is then derived purely arithmetically: `Output::key()` computes `read_G(script_pubkey key) − G·self.output.offset()` (`processor/src/networks/bitcoin.rs:112-122`). The attacker controls both terms: they choose any valid P2TR `script_pubkey` (e.g. one they created and know the discrete log of) and any `offset`.
- The only defense against this is the chain-scanner path, where `scan_transaction` derives the offset from the `Scanner`'s own `scripts` map (`networks/bitcoin/src/wallet/mod.rs:199-214`) and the processor asserts `output.key() == key` (`processor/src/multisigs/scanner.rs:562-563`). Any consumer that accepts a serialized `ReceivedOutput`/`Output` — as produced by `Output::read`, `ReceivedOutput::read` — bypasses that derivation entirely.

So a forged blob `{ offset = o_a, script_pubkey = p2tr(K_a), outpoint = attacker-chosen }` where the attacker picked `K_a = K_target + o_a·G` deserializes to an `Output` whose `key()` returns `K_target` — Serai's multisig key — even though the actual output on chain (if any) pays to `K_a`, which Serai cannot sign for (spending requires the discrete log of the offset key, which only the attacker holds).

### Impact Explanation
Funds can be reported as received by a Serai multisig that are not spendable by it: `Output::key()` attributes the output to `K_target`, `balance()` reports the attacker-chosen `value`, and `id()`/`tx_id()` commit to the attacker-chosen outpoint. Downstream, the scheduler/signing path (`SignableTransaction::multisig` does check `p2tr_script_buf(offset.group_key()) == prevout.script_pubkey`, `wallet/send.rs:276-279`) will simply fail to produce a valid spend, so any accounting that credited the deposit based on `key()`/`balance()` grants value for coins the validator set can never move — direct protocol value loss. Conversely an offset can strip attribution (`key()` returning a different multisig's key), misrouting outputs between key sets.

### Likelihood Explanation
Reachability requires a path where serialized `Output`/`ReceivedOutput` bytes cross a trust boundary (coordinator→processor messages, DB entries populated from network data, or plan/scheduler inputs via `Output::read`). The scanner-asserted path is safe; the risk materializes on any code path consuming these types without re-deriving the offset. Medium-to-high likelihood of the attack being possible wherever the serialized form is accepted, since the attacker needs no secret — only a valid P2TR script and arithmetic on public keys.

### Recommendation
Do not trust the serialized `offset`. Either (a) in `ReceivedOutput::read`/`Output::read`, re-derive ownership by requiring the caller to pass the `Scanner` and verify `scanner.scripts.get(&output.script_pubkey) == Some(offset)`, or (b) make `Output::key()` verify `p2tr_script_buf(key() + G·offset) == script_pubkey` before returning, so a forged offset fails loudly instead of silently reattributing ownership.

### Proof of Concept
```rust
// Attacker goal: get an Output attributed to Serai's group key K_target
// while the real coins pay to attacker key K_a.

// 1. Pick any scalar o_a and publish/spend to a P2TR output with
//    output key  K_a = K_target + o_a * G
let o_a = Scalar::random(&mut OsRng);
let k_a = k_target + (ProjectivePoint::GENERATOR * o_a);
let script = p2tr_script_buf(k_a).unwrap(); // even-Y ensured

// 2. Serialize a forged ReceivedOutput claiming offset = o_a:
//    ReceivedOutput::read will happily accept it (mod.rs:122-134).
let forged = ReceivedOutput {
  offset: o_a,
  output: TxOut { value: Amount::from_sat(100_000), script_pubkey: script },
  outpoint: OutPoint::new(attacker_txid, 0),
};
let bytes = forged.serialize();

// 3. Victim deserializes via Output::read -> ReceivedOutput::read
//    (processor/src/networks/bitcoin.rs:156).
let output = Output::read(&mut bytes.as_slice()).unwrap();

// 4. output.key() = read_G(K_a) - G*o_a = K_target  (bitcoin.rs:112-122)
//    -> reported as belonging to K_target's multisig, balance credited,
//       yet the UTXO is only spendable by the holder of dlog(K_a).
assert_eq!(output.key(), k_target);
```

The fix parity is direct: just as OpenSSH 7.4 stopped honoring attacker-chosen module paths over a forwarded socket, `ReceivedOutput` must stop honoring an attacker-chosen offset that selects which key "owns" an output.