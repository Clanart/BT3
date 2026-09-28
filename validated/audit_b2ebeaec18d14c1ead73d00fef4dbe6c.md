### Title
Scanner credits immature coinbase outputs as spendable funds — funds reported received that cannot be spent - (File: networks/bitcoin/src/wallet/mod.rs)

### Summary
`Scanner::scan_block` iterates over the entire `block.txdata`, including `block.txdata[0]` — the coinbase transaction. Any coinbase output paying a registered P2TR script is returned as a `ReceivedOutput`, which the type contract treats as "a spendable output". Bitcoin consensus forbids spending coinbase outputs for 100 blocks after inclusion, so the scanner reports funds as received that are not actually spendable, analogous to recording an investor allocation without ever pulling the tokens.

### Finding Description
`ReceivedOutput` is documented as "A spendable output" (`networks/bitcoin/src/wallet/mod.rs:88-97`). `scan_block` (`mod.rs:221-227`) calls `scan_transaction` on every transaction in `block.txdata`, including index 0, the coinbase. `scan_transaction` (`mod.rs:199-214`) matches any `output.script_pubkey` against `self.scripts` and emits a `ReceivedOutput` with no check on whether the producing transaction is a coinbase or whether the output is mature. The doc comment on `scan_block` acknowledges outputs "are bound by maturity" and shifts the burden to "a post-processing pass", but the function itself unconditionally emits them as fully spendable outputs. Any downstream consumer that schedules these `ReceivedOutput`s as inputs will produce a transaction rejected by the network (BIP-34/COINBASE_MATURITY rule), and any consumer that credits the amount upon receipt will report balance that does not exist as spendable liquidity.

An unprivileged party can trigger this path: mining is permissionless, and a miner (or mining pool) can construct a coinbase paying the multisig's registered P2TR script. The scanner then returns the immature output as spendable. Equivalently, `ReceivedOutput::read` (`mod.rs:122-134`) deserializes an attacker-supplied `outpoint`/`TxOut`/`offset` triple with no verification that the outpoint exists, is unspent, or is mature — untrusted bytes are accepted as a claim of spendable funds.

### Impact Explanation
- Outputs credited as received cannot be spent: any Plan built on an immature coinbase output produces a transaction that fails relay/consensus (`bad-txns-premature-spend-of-coinbase`).
- If outputs are counted toward received liquidity (deposits, InInstructions) before maturity, Serai reports and potentially bridges value that the multisig cannot yet move — the exact "allocation recorded, tokens never transferred" failure of the reference report.
- `ReceivedOutput::read` additionally lets an untrusted byte stream fabricate a spendable-output claim (arbitrary `outpoint`, `value`, `offset`), which flows into sign/spend APIs without any on-chain verification.

### Likelihood Explanation
Medium. Triggering the on-chain variant requires producing a coinbase paying the target script (miner capability, unprivileged but not trivial), or simply relying on the natural case where the multisig receives mining-derived funds. The deserialization variant requires feeding crafted bytes to `ReceivedOutput::read` in a context where the result is used as a spendable input. The defect is unconditional in `scan_block`/`scan_transaction` — no maturity or coinbase check exists in the code path.

### Recommendation
- In `scan_transaction`/`scan_block`, skip `block.txdata[0]` or mark coinbase-produced outputs immature and exclude them until `block_height + COINBASE_MATURITY` is reached.
- Alternatively, add an `is_coinbase`/maturity field to `ReceivedOutput` so consumers cannot mistake immature outputs for spendable ones.
- For `ReceivedOutput::read`, either document it as trusted-only or have consumers re-verify the outpoint against chain state before treating it as spendable.

### Proof of Concept
```rust
// networks/bitcoin context
// 1. Miner crafts a coinbase tx paying Scanner-registered P2TR script
//    (p2tr_script_buf(key) or any register_offset'd script).
// 2. Block containing the coinbase is fed to:
let outputs = scanner.scan_block(&block); // block.txdata[0] is the coinbase
// 3. `outputs` contains a ReceivedOutput whose outpoint is the coinbase vout.
// 4. Building a spend Plan using this ReceivedOutput produces a transaction
//    rejected by Bitcoin consensus until 100 confirmations elapse:
//    the funds were reported received but are not spendable.
```
The flaw is structural: `scan_block` at `networks/bitcoin/src/wallet/mod.rs:221-227` iterates `block.txdata` from index 0 with no coinbase exclusion, and `ReceivedOutput` unconditionally advertises spendability at `mod.rs:88-97`.

Caveat: I was unable to verify whether the in-scope callers (e.g., `processor/src/networks/bitcoin.rs`) perform the documented post-processing to strip coinbase outputs before acting on them; the vulnerability claim rests on the in-scope scanner API itself emitting immature outputs as spendable, which is reachable regardless of caller behavior.