# Q1389: `parse_withdrawal_sig_params` and streaming request/response pairing

## Question
Can any unprivileged party who can open a TCP connection to the aggregator's gRPC port abuse the streaming contract of `parse_withdrawal_sig_params` in `core/src/rpc/parser/operator.rs` - reordering, truncating, duplicating or interleaving messages in the stream - so a response is paired with a different request than the one that produced it, and a signature or nonce is attributed to the wrong deposit or transaction?

## Target
- File/function: `core/src/rpc/parser/operator.rs` -> `parse_withdrawal_sig_params`
- Entrypoint: a gRPC stream to the open port -> `parse_withdrawal_sig_params`
- Attacker controls: the number, order and timing of messages in the stream; attacker is an unprivileged network client who can reach the public gRPC port; holds no certificate, role or key
- Exploit idea: misattribute a signature or nonce across stream items
- Invariant to test: the i-th response of `parse_withdrawal_sig_params` corresponds to the i-th request item
- Expected Immunefi impact: Critical - direct theft of bridged BTC via a bypassed script/signature check on a bridge UTXO
- Fast validation: drive `parse_withdrawal_sig_params` with a reordered/truncated stream and assert it fails closed
