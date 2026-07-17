Let me analyze the external report and search for nearcore analogs. The core pattern is: an unprivileged actor can update state attributed to another party (via a "recipient" parameter), which then blocks that party's legitimate actions due to a cooldown/timestamp check.

Let me dig deeper into the specific patterns - particularly around the `StatePartRequest.addr` field, chunk state witness routing, and any "on behalf of" patterns.

Let me look at the `partial_witness_tracker` implementation and the `PartialEncodedStateWitnessForward` handling, as well as the Tier3 connection authorization check.