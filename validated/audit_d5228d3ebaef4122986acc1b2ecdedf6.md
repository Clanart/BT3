### Title
`ReceivedOutput` / `Output` deserialization trusts attacker-supplied `offset`, `kind`, and `presumed_origin` instead of deriving them from the `script_pubkey` - (File: networks/bitcoin/src/wallet/mod.rs)

### Summary
The reported bug class is "a credential/identity artifact is issued from client-supplied parameters (`userId`, `levelName`) with no check that the parameters correspond to the requester — values that must be derived server-side are instead trusted from input."

The Serai analog lives in the Bitcoin output deserialization path. `ReceivedOutput::read` reads a scalar `offset`, a `TxOut`, and an `OutPoint` from raw bytes and performs **no check that `offset` is the value actually registered with the `Scanner` and committed to by `output.script_pubkey`** (`networks/bitcoin/src/wallet/mod.rs:122-134`). Normally `offset` is produced exclusively by `Scanner::register_offset`/`scan_transaction`, which bind it to the script via the `scripts` map (`mod.rs:180-213`). Once serialized and re-read, that binding is silently dropped — the bytes self-assert their identity parameters, exactly like `userId`/`levelName` self-asserted the token's subject and tier.

The same pattern compounds at the processor layer: `Output::read` (`processor/src/networks/bitcoin.rs:145-166`) additionally trusts a serialized `kind: OutputType` and a fully attacker-controlled `presumed_origin`, even though `kind` is legitimately *derived* from the offset via the `kinds` map in `get_outputs` (`bitcoin.rs:693-695`).

### Finding Description
- `ReceivedOutput::read` (`networks/bitcoin/src/wallet/mod.rs:122-134`): `let offset = Secp256k1::read_F(r)?;` — the offset scalar is taken verbatim from the input stream. There is no recomputation of `p2tr_script_buf(key + offset·G)` against `output.script_pubkey`.
- `Output::key()` (`processor/src/networks/bitcoin.rs:112-122`) then computes `script_key − offset·G`, i.e. the multisig key attribution of the funds is algebraically determined by the unauthenticated offset bytes.
- `Output::read` (`bitcoin.rs:146-165`) reads `kind` and `presumed_origin` directly from the stream (`Option::<Vec<u8>>::decode(...).map(Address::try_from)` — no validation that `kind` matches `kinds[offset]` or that `presumed_origin` is the real spending input's script).
- These `read` functions are reachable via `N::Output::read(reader)` inside `Scheduler::read` (`processor/src/multisigs/scheduler/utxo.rs:96`) and are listed in-scope as untrusted-byte sinks (`ReceivedOutput::read`).

In the honest path, `get_outputs` derives `kind` from `kinds[offset_repr]` and `presumed_origin` from `tx.input[0]`'s spent output (`bitcoin.rs:686-736`). The deserialization path skips all of this — the analog of "reject requests where `levelName` is manually supplied; derive it server-side."

### Impact Explanation
Concrete consequences of a forged `(offset, kind, presumed_origin)` triple:

1. **Funds reported received that are not spendable**: an `Output` whose stored `offset` doesn't match its `script_pubkey` is scheduled and passed to `SignableTransaction`, which re-keys the threshold shares per-input by `output.offset()`. The resulting signature verifies under `group_key + offset·G`, not under the output's real key — the spend is invalid and the credited funds are unspendable, causing stuck plans/`assert_eq!(self.key, output.key())` failures (`utxo.rs:627`).
2. **Refund-target forgery**: `presumed_origin` is the basis for refund addressing (`presumed_origin()`, `refund_plan` at `utxo.rs:587-604`). Attacker-controlled bytes set `presumed_origin` to an attacker address, redirecting refund flows — the closest analog to "impersonate any user by supplying their `userId`."
3. **Kind escalation**: claiming `OutputType::External` for a `Branch`/`Forwarded` output (or vice versa) bypasses the `kind` gating in `scanner_event_to_multisig_event` (`multisigs/mod.rs:824-837`), causing externally-received-funds logic (instructions, data attachment) to run on internally-generated outputs.

### Likelihood Explanation
Medium. Exploitation requires the attacker to inject crafted serialized `Output`/`ReceivedOutput` bytes into a deserialization sink (scheduler DB blob, cross-component message). This is narrower than a public unauthenticated HTTP endpoint, but the rules explicitly scope `ReceivedOutput::read`/`Output::read` as untrusted-input surfaces, and no integrity/authentication layer (MAC, signature, or re-derivation) protects the fields.

### Recommendation
- On deserialization, re-derive instead of trusting: after `ReceivedOutput::read`, verify `p2tr_script_buf(key + offset·G) == output.script_pubkey` for the expected key(s), or store the owning key and recompute.
- Drop `kind` and `presumed_origin` from the wire format entirely and recompute them in `read` via the same `kinds` map / RPC origin lookup used by `get_outputs`; or at minimum cross-check `kinds[offset] == kind`.
- Treat serialized outputs as claims to be authenticated (the "token"), not as authoritative state — mirroring "the `userId` must belong to the authenticated caller."

### Proof of Concept
```rust
// Given an honest ReceivedOutput for a real deposit:
let honest = scanner.scan_transaction(&tx)[0].clone(); // offset = registered offset o

// Attacker crafts bytes with offset' = o + 1 (or any scalar):
let mut buf = honest.serialize();
buf[0..32].copy_from_slice(&(honest.offset() + Scalar::ONE).to_bytes());
let forged = ReceivedOutput::read(&mut buf.as_slice()).unwrap(); // succeeds, no check

// forged.offset() != o, but forged.output().script_pubkey still commits to key + o·G.
// Output::key() returns (key + o·G) - (o+1)·G = key - G  -> wrong multisig attribution,
// and any spend signing with keys.offset(o+1) produces a signature invalid under the
// real output key: funds reported received that are not spendable.
```

Note: I could not fully trace every `Output::read`/`ReceivedOutput::read` caller in the available context (e.g., tributary/processor message paths); the analysis is anchored on the confirmed deserialization sinks in `utxo.rs:96` and `bitcoin.rs:145-166` and the per-input offset re-keying used by `SignableTransaction`.