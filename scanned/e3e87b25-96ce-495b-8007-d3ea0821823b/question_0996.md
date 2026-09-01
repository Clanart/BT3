# Q0996: `connect` and height/finality boundaries

## Question
Can any unprivileged party who can broadcast a Bitcoin transaction and pay fees exploit the finality or confirmation-depth boundary used by `connect` in `crates/clementine-extended-rpc/src/client.rs` - acting at exactly the boundary height, or across a range whose start/end are computed inconsistently - so the bridge commits to a fact at a depth from which it can still be reversed?

## Target
- File/function: `crates/clementine-extended-rpc/src/client.rs` -> `connect` (Extended Bitcoin RPC client with retry logic)
- Entrypoint: transaction placement around the finality boundary -> `connect`
- Attacker controls: the height at which the attacker's transaction confirms; attacker is an unprivileged party who can broadcast Bitcoin transactions and pay fees; holds no protocol role or key
- Exploit idea: commit the protocol to a reversible fact
- Invariant to test: the depth at which a fact is committed is at least the configured finality depth in every path
- Expected Immunefi impact: Critical - permanent freezing of bridged funds (a move-to-vault UTXO that can never be spent)
- Fast validation: assert boundary heights are handled identically across all consumers of `connect`
