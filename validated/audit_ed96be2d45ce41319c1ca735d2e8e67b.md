### Title
Deserialization of `ReceivedOutput` never validates that `offset` actually derives `output.script_pubkey`, allowing unspendable outputs to be reported as received - (File: `networks/bitcoin/src/wallet/mod.rs`)

### Summary
`ReceivedOutput::read` accepts untrusted bytes and constructs a `ReceivedOutput` with an arbitrary `offset` scalar, arbitrary `TxOut`, and arbitrary `OutPoint`, performing zero validation that the offset is the one registered with the `Scanner` or that `key + offset*G` produces `output.script_pubkey`. The class mirrors the report: a critical value (here the scalar offset which is the *only* thing that makes the output spendable) is accepted without any check that it is valid for the object it is attached to. The result is the same failure shape as transferring ownership to an invalid address: the funds are recorded as owned but cannot ever be spent — a permanent lockout of that output.

### Finding Description
`Scanner::scan_transaction` establishes the invariant that a `ReceivedOutput`'s `offset` is exactly the scalar registered for the `script_pubkey` it was found under (mod.rs:199-214, 205-210). That invariant is what later allows spending: `SignableTransaction`/the multisig machinery applies `ReceivedOutput::offset()` to the threshold keys to derive the spend key.

However, `ReceivedOutput::read` (mod.rs:122-134) deserializes `offset`, `output`, and `outpoint` independently:

```rust
pub fn read<R: Read>(r: &mut R) -> io::Result<ReceivedOutput> {
    let offset = Secp256k1::read_F(r)?;
    let output;
    let outpoint;
    {
      let mut buf_r = BufReader::with_capacity(0, r);
      output = TxOut::consensus_decode(&mut buf_r)...;
      outpoint = OutPoint::consensus_decode(&mut buf_r)...;
    }
    Ok(ReceivedOutput { offset, output, outpoint })
}
```

There is no check that `Scanner`'s `scripts` map contains `output.script_pubkey` keyed by `offset` — i.e., no check that `p2tr_script_buf(key + offset*G) == output.script_pubkey`. Any byte stream produces a structurally valid `ReceivedOutput` whose offset may correspond to a completely different (or no) spendable key. Because P2TR outputs commit to the x-only tweaked key, an output scanned under offset `o` can only be spent by keys offset by `o`; supplying a different offset produces a signature for the wrong key, so the funds are unspendable while the wallet layer treats them as valid balance.

### Impact Explanation
The protocol credits an output as received/spendable when it is not. Downstream, `SignableTransaction::new` will happily include such an output as an input, and the threshold signature produced will correspond to `key + offset*G` — a key that does not match `script_pubkey` — so the transaction is invalid on-chain and the value is effectively locked. This is a permanent, non-recoverable lockout per affected output with no error surfaced at deserialization time. High impact on the funds represented by the forged `ReceivedOutput`.

### Likelihood Explanation
Exploitation requires attacker-controlled bytes to reach `ReceivedOutput::read` — i.e., a peer or upstream component feeding a serialized `ReceivedOutput` that the victim did not itself produce via `Scanner::scan_transaction`/`scan_block`. In flows where serialized `ReceivedOutput`s are relayed between components, a single malicious or corrupted entry suffices. However, many deployments only deserialize their own scanner output, so likelihood is low-to-moderate rather than high — analogous to the "mistake in transfer" precondition of the original finding.

### Recommendation
Bind the offset to the output at deserialization, or make the trust boundary explicit:

1. Reject deserialized `ReceivedOutput`s whose `output.script_pubkey` was never registered: extend `read` to take a `&Scanner` (or the key + registered scripts) and verify `self.scripts.get(&output.script_pubkey) == Some(&offset)`, equivalently `p2tr_script_buf(scanner.key + GENERATOR * offset) == Some(output.script_pubkey.clone())`.
2. Alternatively, keep `read` raw but rename/document it as unchecked and add a `ReceivedOutput::validate(&self, scanner: &Scanner) -> bool` that callers must invoke before the output enters wallet balance/spend logic.
3. At minimum, sanity-check `offset` is non-zero only when `script_pubkey` differs from the base key's script, preventing trivially inconsistent objects.

### Proof of Concept
```rust
// Setup: a real scanner for an even key, with a real received output
let key: ProjectivePoint = even_key();
let scanner = Scanner::new(key).unwrap();
let real: ReceivedOutput = scanner.scan_transaction(&tx).remove(0); // offset = Scalar::ZERO

// Attacker crafts bytes: same TxOut/outpoint, but a bogus offset
let mut forged = vec![];
forged.extend(Scalar::from(0xdeadbeefu64).to_bytes().as_ref().iter()); // wrong offset
forged.extend(serialize(real.output()));
forged.extend(serialize(real.outpoint()));

// ReceivedOutput::read performs NO binding check — this succeeds
let evil = ReceivedOutput::read(&mut forged.as_slice()).unwrap();
assert_eq!(evil.offset(), Scalar::from(0xdeadbeefu64));
assert_eq!(evil.output(), real.output()); // claims the same funds

// Spending path: key + 0xdeadbeef*G != the script_pubkey's key, so the
// threshold signature is for the wrong key and the funds are locked.
```