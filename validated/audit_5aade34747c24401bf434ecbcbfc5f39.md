### Title
`ReceivedOutput::read` deserializes an offset/script_pubkey pair without binding the offset to the script's key, allowing funds to be recorded under a key that cannot spend them - (File: networks/bitcoin/src/wallet/mod.rs)

### Summary
`ReceivedOutput::read` accepts three independently-valid encodings — a canonical scalar `offset`, a consensus-decoded `TxOut`, and an `OutPoint` — but never checks the composed invariant that `output.script_pubkey == p2tr_script_buf(scanner_key + offset * G)`. Each component is checked in isolation and then *reinterpreted* together, the same validate-then-reinterpret ordering flaw as CVE-2015-2060, where cabextract checked filename bytes before a UTF-8 transformation silently produced a `/`. Here, deserialization produces a `ReceivedOutput` whose declared `offset` does not correspond to the key embedded in the output's script, so an output is treated as received under a spend key that does not control it.

### Finding Description
`Scanner::scan_transaction` only produces `ReceivedOutput`s where the offset genuinely maps to the matched `script_pubkey` (`self.scripts.get(&output.script_pubkey)` at `wallet/mod.rs:205`). However, `ReceivedOutput::read` reconstructs the same type from untrusted bytes with no such binding:

```rust
// networks/bitcoin/src/wallet/mod.rs
pub fn read<R: Read>(r: &mut R) -> io::Result<ReceivedOutput> {
    let offset = Secp256k1::read_F(r)?;                       // canonical scalar — checked
    output  = TxOut::consensus_decode(&mut buf_r)...          // valid encoding — checked
    outpoint = OutPoint::consensus_decode(&mut buf_r)...      // valid encoding — checked
    Ok(ReceivedOutput { offset, output, outpoint })           // composed invariant — never checked
}
```

The offset is the scalar used to derive the spendable key (`key + offset * G`), and the script_pubkey embeds only the x-coordinate of that key (via `p2tr_script_buf` / `x_only`, `crypto.rs:21`, which drops the sign byte — another layer of byte transformation after parsing). An attacker who supplies serialized `ReceivedOutput` bytes can pair any TxOut with an arbitrary `offset`. Downstream code treats the output as owned and later attempts to spend it by offsetting the threshold keys; since `key + offset * G` does not equal the key committed in the script_pubkey, the resulting BIP-340 signature is invalid against the actual UTXO.

### Impact Explanation
Funds reported received that are not spendable: an output is recorded as an owned input (carrying `value()` and an outpoint), but the scalar offset does not correspond to the discrete-log offset of the key in the script, so any `SignableTransaction` built over it produces a signature invalid under BIP-340/`Prevouts::All` sighash. The Bitcoin network rejects the spend; the credited value is permanently unspendable under the recorded metadata.

### Likelihood Explanation
Medium. The corrupted object requires an untrusted party to feed crafted bytes into `ReceivedOutput::read` (or a consumer such as `Output::read` which embeds it, `processor/src/networks/bitcoin.rs:156`). Within `bitcoin-serai` itself the scanner produces self-consistent pairs, so exploitation requires the serialized form to cross a trust boundary. Additionally, since the script embeds only the x-only key, an attacker who controls the offset scalar can also select offsets that normalize (via parity/negation) to unrelated registered scripts, compounding the mismatch.

### Recommendation
Either make `ReceivedOutput` construction private to `Scanner` (enforcing the offset↔script binding by construction), or have `ReceivedOutput::read` take the scanning key and re-verify `p2tr_script_buf(key + offset * G) == output.script_pubkey`, rejecting bytes that fail the composed check — i.e., validate the object in its final, transformed form, not per-field.

### Proof of Concept
```rust
use k256::{Scalar, ProjectivePoint};
use bitcoin::{TxOut, OutPoint, Amount, ScriptBuf};
use bitcoin_serai::wallet::{ReceivedOutput, p2tr_script_buf};
use frost::curve::{Ciphersuite, Secp256k1};

// key K, attacker-observed output paying to script for K + o*G (offset o)
// Attacker serializes a ReceivedOutput with offset o' != o.
let mut bytes = o_prime.to_bytes().to_vec();      // canonical scalar — passes read_F
bytes.extend(bitcoin::consensus::encode::serialize(&txout));   // valid TxOut
bytes.extend(bitcoin::consensus::encode::serialize(&outpoint));// valid OutPoint

let ro = ReceivedOutput::read(&mut bytes.as_slice()).unwrap(); // accepted
// ro.offset() == o' yet ro.output().script_pubkey commits to x-only(K + o*G)
// p2tr_script_buf(K + o'*G) != ro.output().script_pubkey — never checked.
// Spending applies keys.offset(o'): group key K + o'*G signs for a script
// requiring K + o*G -> signature invalid -> funds reported received, unspendable.
```