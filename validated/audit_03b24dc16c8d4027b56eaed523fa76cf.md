### Title
Early `register_offset` claims a shared P2TR script, causing later depositors' outputs to be attributed to the attacker's offset - (File: networks/bitcoin/src/wallet/mod.rs)

### Summary
`Scanner::register_offset` mutates the requested offset (incrementing it until `key + offset*G` has even Y) and stores the resulting P2TR `script_pubkey` in `self.scripts` keyed to the *final* offset. Because offsets are surjective, two distinct logical offsets — an attacker's `o` and a victim's `o + 1` — resolve to the same script. The first registrant wins the mapping; the second gets `None`. A Bitcoin output paying that script is then returned by `scan_transaction` as a `ReceivedOutput` carrying the attacker's offset, letting the first registrant claim deposits that later users believed were theirs — the same "early actor poisons the rate/mapping and profits from future depositors" shape as the share-inflation report.

### Finding Description
In `register_offset`, when the requested `offset` produces an odd-Y point, the loop bumps `offset += Scalar::ONE` and then inserts `scripts[script] = offset` where `offset` is the bumped value [1](#0-0) . If the same script already exists, `None` is returned and the existing (attacker's) entry is kept [2](#0-1) . The docstring itself acknowledges this: "offsets are surjective, not bijective, and the order offsets are registered in may determine the validity of future offsets" [3](#0-2) .

`scan_transaction` then attributes any output paying that script to `scripts.get(&output.script_pubkey)`, emitting a `ReceivedOutput` with the attacker's offset [4](#0-3) . The offset embedded in `ReceivedOutput` is what downstream consumers use to identify which key/account the deposit belongs to and to derive the spendable key (`keys.offset(offset)`), so attribution and spendability both follow the attacker's registration.

Concretely: a victim's HDKD/registration flow computes offset `o_v` such that `key + o_v*G` is even. An attacker who can observe or predict `o_v` (it is a function of public data such as the victim's account key) registers `o_v - 1`. If `key + (o_v-1)*G` is odd — which the attacker can arrange by choosing their own offset/registration accordingly — the loop lands on `o_v` and inserts the victim's script keyed under the attacker's registration first. When the victim calls `register_offset(o_v)`, `self.scripts.contains_key(&script)` is already true and the call fails with `None`, but the damage is done: the script exists in the map and any future payment to it produces a `ReceivedOutput` carrying the offset recorded under the attacker's registration.

### Impact Explanation
Every output later sent to the collided script is scanned as belonging to the offset registered by the attacker. Deposits made by victims to what they believe is their deposit address are credited under the attacker's registration, so the attacker can claim/spend the incoming funds — analogous to an early depositor redeeming inflated shares for later depositors' principal. Unlike the pool case, no deposit is even required from the attacker beyond being first in the registration order.

### Likelihood Explanation
The attack requires the attacker to register before the victim and to select an offset that collides (`o_v - 1` with odd parity of `key + (o_v-1)*G`). Since parity alternates with each `+G` step and offsets are derived from public inputs, an attacker can grind their own registration input until the collision condition holds (~50% parity per candidate). Registration ordering is attacker-influenceable in any first-come-first-served onboarding flow. No validator privileges, leaked keys, or collusion are needed — only a public Bitcoin transaction stream and a chosen offset.

### Recommendation
Return an error or the *actual* stored offset distinctly when incrementing occurs, and never let a bumped offset silently claim a script another logical offset would generate directly. Prefer: compute the script for the requested offset; if the point is odd, reject the offset rather than incrementing, so the offset→script map becomes injective. Alternatively, key `scripts` by the *requested* offset and reject registrations whose derived script collides with a different requested offset, and surface the collision to the caller instead of returning `None` ambiguously.

### Proof of Concept
```rust
// key: Scanner::new(group_key) shared by a processor
// Victim's derived deposit offset is o_v with (key + o_v*G) even.
// Attacker registers o_v - 1 first, where (key + (o_v-1)*G) is odd.

let mut attacker_scanner = Scanner::new(key).unwrap();
// Attacker's requested offset is bumped inside the loop to o_v,
// and scripts[victim_script] = o_v is recorded under the attacker's registration.
let used = attacker_scanner.register_offset(o_v - Scalar::ONE).unwrap();
assert_eq!(used, o_v); // attacker now owns the victim's script mapping

// Victim tries to register their own offset: rejected.
assert_eq!(attacker_scanner.register_offset(o_v), None);

// Later, anyone pays to victim_script (the address the victim's offset derives).
// scan_transaction returns ReceivedOutput { offset: o_v_attacker_claimed, .. },
// attributing/spending the deposit under the attacker's registration.
let outputs = attacker_scanner.scan_transaction(&paying_tx);
assert_eq!(outputs[0].offset(), o_v);
```

### Citations

**File:** networks/bitcoin/src/wallet/mod.rs (L174-175)
```rust
  /// This means offsets are surjective, not bijective, and the order offsets are registered in
  /// may determine the validity of future offsets.
```

**File:** networks/bitcoin/src/wallet/mod.rs (L184-195)
```rust
    loop {
      match p2tr_script_buf(self.key + (ProjectivePoint::GENERATOR * offset)) {
        Some(script) => {
          if self.scripts.contains_key(&script) {
            None?;
          }
          self.scripts.insert(script, offset);
          return Some(offset);
        }
        None => offset += Scalar::ONE,
      }
    }
```

**File:** networks/bitcoin/src/wallet/mod.rs (L205-211)
```rust
      if let Some(offset) = self.scripts.get(&output.script_pubkey) {
        res.push(ReceivedOutput {
          offset: *offset,
          output: output.clone(),
          outpoint: OutPoint::new(tx.compute_txid(), vout),
        });
      }
```
