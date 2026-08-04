# Q646: blockSubscribe boundary-object blowup

## Question
Can an unprivileged attacker use `blockSubscribe` within the single-client low-rate model and choose subscription parameters, pubkeys/signatures/filters, encoding choices, websocket disconnect timing, and one slow or bursty client such that `block_subscribe` triggers a path where one legal boundary object such as a dense block, large account set, or verbose notification has outsized cost inside this method, violating the invariant that worst-case legal objects must still stay within safe service budgets and causing `RPC DoS/Crash`?

## Target
- File/function: rpc/src/rpc_pubsub.rs::block_subscribe
- Entrypoint: WebSocket `blockSubscribe` from a single client connection
- Attacker controls: subscription parameters, pubkeys/signatures/filters, encoding choices, websocket disconnect timing, and one slow or bursty client
- Exploit idea: use the heaviest legal object the method can surface
- Invariant to test: worst-case legal objects must still stay within safe service budgets
- Expected Immunefi impact: RPC DoS/Crash
- Fast validation: pick the densest live object the method can return and measure peak heap and latency
